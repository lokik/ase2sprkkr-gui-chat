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
    referenced_remote_paths, referencing_paths, snapshot_from_review,
)

logger = logging.getLogger(__name__)


def _run_records(manager):
    """Include process-local reservations in the same file-reference scope."""
    for store in (manager.store, *manager.conflict_stores):
        yield from store.runs()


def _local_file_references(manager: RunManager, projects: Any, job_id: str, job: Any):
    """Collect declarations and other run references without inspecting file contents."""
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
        candidate_kinds = {job.imported_path: "file"}
        ignored_key = job.imported_key
        ignored_run_key = None
    else:
        record = manager.store.get(job_id)
        if not record.state.is_terminal:
            raise RuntimeError("The calculation is no longer in a final state")
        directory = str(record.execution.directory.local)
        candidate_kinds = declared_local_kinds(record.files, directory)
        remote = (
            referenced_remote_paths(record.files)
            if not record.execution.transport.is_local else ()
        )
        ignored_key = None
        ignored_run_key = record.key

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
            reference_details.setdefault(path, set()).add(label)

    for key, project_id in imported.items():
        if key == ignored_key:
            continue
        path = key.split("::", 1)[0]
        reference_details.setdefault(path, set()).add(
            f"Imported result (project {project_id or 'unassigned'})"
        )
    return candidate_kinds, remote, reference_details


def referenced_file_deletion(manager: RunManager, projects: Any, job_id: str, job: Any) -> FileDeletionPlan:
    """Preview explicit local-file cleanup using authoritative persisted refs."""
    candidate_kinds, remote, reference_details = _local_file_references(manager, projects, job_id, job)
    return plan_file_deletion(
        candidate_kinds, other_references=reference_details, remote_references=remote,
        reference_details=reference_details, expected_kinds=candidate_kinds,
    )


def _remote_reference_details(manager: RunManager, record: RunRecord) -> dict[str, tuple[str, ...]]:
    """Other persisted runs referencing the same execution filesystem."""
    references: dict[str, set[str]] = {}
    for other in _run_records(manager):
        if (
            other.key == record.key
            or other.execution.transport.is_local
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
    if record.execution.transport.is_local or not record.execution.available:
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
        record.execution.transport.host, tuple(snapshots),
        shared, details, tuple(unavailable),
    )


def capture_remote_cleanup(manager: RunManager, job_id: str, job: Any) -> Any:
    """Keep the run's connection and manifest after its history is removed."""
    if job is None or not job.is_final or job.imported_path is not None:
        raise ValueError("Remote cleanup requires a finished persisted run")
    record = manager.store.get(str(job_id))
    if record.execution.transport.is_local or not record.execution.available:
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
    Do not hold a global reservation lock during an SSH request: remote
    cleanup is best effort, including its active-reference check.
    """
    snapshots = {snapshot.path: snapshot for snapshot in review.snapshots}
    approved = {}
    skipped = []
    for path in selected_files:
        snapshot = snapshots.get(path)
        if snapshot is None:
            skipped.append({"path": path, "status": "skipped",
                            "message": "Not present in the inspected remote file list"})
        else:
            approved[path] = expected_identity(snapshot)
    if not approved:
        return tuple(skipped)

    try:
        blocked = manager.file_mutation_conflicts(
            {path: snapshots[path].kind for path in approved}, target=record,
        )
        references = _remote_reference_details(manager, record)
        for path in tuple(approved):
            labels = {
                label
                for reference, names in references.items()
                if file_reference_overlaps(
                    posixpath.normpath(path), reference,
                    directory=snapshots[path].kind == "directory",
                )
                for label in names
            }
            message = blocked.get(path)
            if message is None and review.referrers_for(path) != tuple(sorted(labels)):
                message = "Other run references changed since confirmation"
            if message is not None:
                del approved[path]
                skipped.append({"path": path, "status": "skipped", "message": message})

        # A selected directory already covers its selected descendants.
        # Only collapse paths after excluding blocked directories.
        directories = {path for path in approved if snapshots[path].kind == "directory"}
        for path in tuple(approved):
            if any(path != directory and posixpath.commonpath((path, directory)) == directory
                   for directory in directories):
                del approved[path]
        outcomes = (manager.client_for(record).delete_remote_files(record, approved)
                    if approved else ())
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
    if expected_file_snapshots is None:
        raise ValueError("File deletion requires confirmed file identities")
    kinds = {snapshot.path: snapshot.kind for snapshot in expected_file_snapshots}
    if set(kinds) != set(selected):
        raise ValueError("File deletion requires a snapshot for each selected path")
    with manager.file_mutation_guard(kinds):
        candidate_kinds, _, references = _local_file_references(manager, projects, job_id, job)
        if any(candidate_kinds.get(path) != kind for path, kind in kinds.items()):
            raise RuntimeError(
                "Referenced files changed since confirmation; review them again"
            )
        shared = tuple(sorted(
            path for path, kind in kinds.items()
            if referencing_paths(path, kind == "directory", references)
        ))
        if shared != tuple(sorted(expected_shared_files)):
            raise RuntimeError(
                "Shared file references changed since confirmation; review them again"
            )
        if shared and not allow_shared_files:
            raise RuntimeError(
                "Deletion of files referenced by other runs requires an "
                "additional explicit confirmation"
            )
        delete_local_files(selected, snapshots=expected_file_snapshots)
