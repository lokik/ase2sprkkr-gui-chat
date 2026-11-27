"""File cleanup coordination for saved runs and imported results.

This module only handles files. Forgetting a run belongs to PersistentJobSource.
A preview is advisory: references and file identities are rechecked at deletion.
KKRunner remains authoritative for run records and remote file operations.
"""
from __future__ import annotations

import logging
import posixpath
from kkrunner.runs import RunManager, RunRecord
from typing import Any

from kkrunner.runs.conflicts import (
    file_reference_overlaps, filesystem_namespace, referenced_paths,
)
from ase2sprkkr_gui.gui.application.imported_project_results import assignments
from ase2sprkkr_gui.gui.application.run_file_deletion import (
    FileDeletionPlan, RemoteFileReview, declared_local_kinds, delete_local_files,
    expected_identity, plan_file_deletion, referenced_local_paths,
    referenced_remote_paths, snapshot_from_review,
)

logger = logging.getLogger(__name__)


def _run_records(manager):
    """Include process-local reservations in the same file-reference scope."""
    for store in (manager.store, *manager.conflict_stores):
        yield from store.runs()


def referenced_file_deletion(manager: RunManager, projects: Any, job_id: str, job: Any) -> FileDeletionPlan:
    """Preview explicit local-file cleanup using authoritative persisted refs.

    Other saved records are detected regardless of project or application.
    Shared local files may only be deleted through explicit selection and
    an additional GUI warning.
    """
    job_id = str(job_id)
    if job is None:
        raise KeyError(job_id)
    if not job.is_final:
        raise RuntimeError("Files of active or unknown calculations cannot be deleted")

    runs = tuple(_run_records(manager))
    imported = assignments(projects)
    remote: tuple[str, ...] = ()
    if job.imported_path is not None:
        if job.imported_key not in imported:
            raise RuntimeError("The imported result reference has changed; refresh first")
        candidates = (job.imported_path,)
        candidate_kinds = {job.imported_path: "file"}
        ignored_key = job.imported_key
        ignored_run_key = None
    else:
        record = manager.store.get(job_id)
        if not record.state.is_terminal:
            raise RuntimeError("The calculation is no longer in a final state")
        directory = str(record.execution.directory.local)
        candidates = referenced_local_paths(record.files, directory)
        candidate_kinds = declared_local_kinds(record.files, directory)
        remote = (
            referenced_remote_paths(record.files)
            if not record.execution.target.is_local else ()
        )
        ignored_key = None
        ignored_run_key = record.key

    other_paths: list[str] = []
    reference_details: dict[str, set[str]] = {}
    for record in runs:
        if record.key == ignored_run_key:
            continue
        metadata = record.metadata.get("ase2sprkkr", {})
        label = (
            f"Run {record.id} ({metadata.get('dataset') or 'unknown dataset'}, "
            f"project {record.project_id or 'unassigned'})"
        )
        for path in referenced_local_paths(
            record.files, str(record.execution.directory.local)
        ):
            other_paths.append(path)
            reference_details.setdefault(path, set()).add(label)

    for key, project_id in imported.items():
        if key == ignored_key:
            continue
        path = key.split("::", 1)[0]
        other_paths.append(path)
        reference_details.setdefault(path, set()).add(
            f"Imported result (project {project_id or 'unassigned'})"
        )
    return plan_file_deletion(
        candidates, other_references=other_paths, remote_references=remote,
        reference_details=reference_details, expected_kinds=candidate_kinds,
    )


def _remote_reference_details(manager: RunManager, record: RunRecord) -> dict[str, tuple[str, ...]]:
    """Other persisted runs referencing the same execution filesystem."""
    references: dict[str, set[str]] = {}
    for other in _run_records(manager):
        if (
            other.key == record.key
            or other.execution.target.is_local
            or filesystem_namespace(other) != filesystem_namespace(record)
        ):
            continue
        metadata = other.metadata.get("ase2sprkkr", {})
        label = (
            f"Run {other.id} ({metadata.get('dataset') or 'unknown dataset'}, "
            f"project {other.project_id or 'unassigned'})"
        )
        for path in referenced_paths(other):
            references.setdefault(posixpath.normpath(path), set()).add(label)
    return {path: tuple(sorted(labels)) for path, labels in references.items()}


def remote_file_review(manager: RunManager, job_id: str, job: Any) -> RemoteFileReview:
    """Inspect remote files; the caller runs blocking SSH work off the GUI thread."""
    if job is None or job.imported_path is not None or not job.is_final:
        raise ValueError("Remote deletion is available only for finished tracked SSH runs")
    record = manager.store.get(str(job_id))
    if record.execution.target.is_local or not record.execution.available:
        raise ValueError("This run has no remote files")
    candidates = referenced_remote_paths(record.files)
    referrers = _remote_reference_details(manager, record)
    replies = manager.client_for(record).inspect_remote_files(record, candidates)
    snapshots = []
    unavailable = []
    for item in replies:
        if item["status"] == "ok":
            snapshots.append(snapshot_from_review(item))
        else:
            unavailable.append((
                item["path"], item.get("message") or item["status"],
            ))
    def referencing_runs(snapshot):
        path = posixpath.normpath(snapshot.path)
        labels = set()
        for reference, names in referrers.items():
            if file_reference_overlaps(path, reference, directory=snapshot.kind == "directory"):
                labels.update(names)
        return tuple(sorted(labels))

    details = tuple(
        (snap.path, referencing_runs(snap))
        for snap in snapshots if referencing_runs(snap)
    )
    shared = tuple(sorted(path for path, _labels in details))
    return RemoteFileReview(
        record.execution.target.host, tuple(snapshots),
        shared, details, tuple(unavailable),
    )


def capture_remote_cleanup(manager: RunManager, job_id: str, job: Any) -> Any:
    """Keep the run's connection and manifest after its history is removed."""
    if job is None or not job.is_final or job.imported_path is not None:
        raise ValueError("Remote cleanup requires a finished persisted run")
    record = manager.store.get(str(job_id))
    if record.execution.target.is_local or not record.execution.available:
        raise ValueError("The selected run has no SSH connection")
    return record


def cleanup_remote_files(
    manager: RunManager, record: RunRecord, review: RemoteFileReview,
    selected_files: tuple[str, ...],
) -> tuple[dict, ...]:
    """Best-effort *post-forget* SSH cleanup; never used by local-file deletion.

    The caller schedules this outside the blocking deletion workflow.
    Shared-reference changes since the preview cause only the affected
    remote path to be skipped. The record is already removed by this point.
    """
    snapshots = {snapshot.path: snapshot for snapshot in review.snapshots}
    references = _remote_reference_details(manager, record)
    approved = {}
    skipped = []
    for path in selected_files:
        snapshot = snapshots.get(path)
        if snapshot is None:
            skipped.append({"path": path, "status": "skipped",
                            "message": "Not present in the inspected remote file list"})
            continue
        before = review.referrers_for(path)
        norm = posixpath.normpath(path)
        current_labels = set()
        for reference, labels in references.items():
            if file_reference_overlaps(norm, reference, directory=snapshot.kind == "directory"):
                current_labels.update(labels)
        if before != tuple(sorted(current_labels)):
            skipped.append({"path": path, "status": "skipped",
                            "message": "Other run references changed since confirmation"})
            continue
        approved[path] = expected_identity(snapshot)
    # A recursive directory target already covers explicitly selected
    # children; sending both would lead to misleading missing-file errors.
    directories = {
        path for path, snapshot in snapshots.items()
        if path in approved and snapshot.kind == "directory"
    }
    for path in tuple(approved):
        if any(
            path != directory
            and posixpath.commonpath((path, directory)) == directory
            for directory in directories
        ):
            del approved[path]
    if not approved:
        return tuple(skipped)
    try:
        outcomes = manager.client_for(record).delete_remote_files(record, approved)
    except Exception as exc:  # noqa: BLE001 - background SSH I/O boundary
        logger.warning("Remote cleanup failed on %s: %s", review.host, exc)
        outcomes = tuple(
            {"path": path, "status": "skipped", "message": str(exc)}
            for path in approved
        )
    result = (*skipped, *outcomes)
    for item in result:
        if item["status"] != "deleted":
            logger.warning(
                "Remote cleanup skipped %s on %s: %s",
                item["path"], review.host, item.get("message") or item["status"],
            )
    return result


def delete_confirmed_local_files(
    manager: RunManager, projects: Any, job_id: str, job: Any, *,
    expected_files: tuple[str, ...] | None,
    expected_file_snapshots: tuple | None,
    expected_shared_files: tuple[str, ...],
    allow_shared_files: bool,
) -> None:
    """Revalidate a user-confirmed selection before deleting any local files."""
    if expected_files is None:
        raise ValueError("File deletion requires a confirmed file preview")
    selected = tuple(expected_files)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("File deletion requires a nonempty, unique selection")
    plan = referenced_file_deletion(manager, projects, job_id, job)
    if not set(selected).issubset(plan.selectable_files):
        raise RuntimeError(
            "Referenced files changed since confirmation; review them again"
        )
    shared = tuple(sorted(set(selected).intersection((
        *plan.shared, *plan.shared_directories,
    ))))
    if shared != tuple(sorted(expected_shared_files)):
        raise RuntimeError(
            "Shared file references changed since confirmation; review them again"
        )
    if shared and not allow_shared_files:
        raise RuntimeError(
            "Deletion of files referenced by other runs requires an "
            "additional explicit confirmation"
        )
    selected_snapshots = tuple(
        snapshot for snapshot in plan.selectable_snapshots
        if snapshot.path in selected
    )
    if expected_file_snapshots is None or (
        tuple(expected_file_snapshots) != selected_snapshots
    ):
        raise RuntimeError(
            "A file changed since the displayed last-modified times; "
            "review the deletion list again"
        )
    delete_local_files(selected, snapshots=selected_snapshots)
