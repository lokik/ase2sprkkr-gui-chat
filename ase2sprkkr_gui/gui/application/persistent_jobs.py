"""Track ASE2SPRKKR runs in both application stores behind one GUI-facing API."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable

from kkrunner import MemoryRunStore
from kkrunner.backends.contracts import BackendCapability
from kkrunner.files.versions import FileVersion
from kkrunner.projects import ProjectRepository, ProjectRunManager
from kkrunner.runs.manager import RunManager
from kkrunner.runs.records import CorruptRunRecord, RunRecord
from ase2sprkkr.outputs.project_results import (
    attach_project_run,
    load_project_result,
    retry_project_postprocessing,
)
from ase2sprkkr.sprkkr.projects import (
    project_execution_manager,
)
from ase2sprkkr_gui.gui.application.imported_project_results import (
    assignments, reference_id, reassign_references, remove_reference,
)
from ase2sprkkr import ResultLoader

from ase2sprkkr_gui.gui.application.run_file_deletion import FileDeletionPlan, RemoteFileReview
from ase2sprkkr_gui.gui.application.run_file_cleanup import (
    referenced_file_deletion as plan_referenced_file_deletion,
    remote_file_review as inspect_remote_file_review,
    capture_remote_cleanup as prepare_remote_file_cleanup,
    cleanup_remote_files as perform_remote_file_cleanup,
    delete_confirmed_local_files,
)

logger = logging.getLogger(__name__)

_FINAL_STATES = frozenset(
    {"finished", "failed", "cancelled"}
)
JobChangedListener = Callable[[str], None]
_SESSION_PREFIX = "session/"


def is_session_job_id(job_id: str) -> bool:
    return job_id.startswith(_SESSION_PREFIX)


def job_id_for_record(record: RunRecord) -> str:
    """Encode the store namespace in the existing string-valued GUI identity.

    Run ids cannot contain slashes, so the two namespaces cannot collide.
    """
    prefix = _SESSION_PREFIX if isinstance(record.store, MemoryRunStore) else ""
    return prefix + str(record.id)


def project_run_manager(projects: ProjectRepository) -> ProjectRunManager:
    """Use ASE2SPRKKR's storage policy with KKRunner's project-run manager."""
    return project_execution_manager(project_repository=projects)


def _execution_target(record: RunRecord) -> str:
    """Describe transport and native backend separately."""
    transport = record.execution.transport
    backend = record.execution.backend
    label = "Local" if transport.is_local else f"{transport.kind.upper()} · {transport.host}"
    if not record.execution.available:
        return f"{label} · unavailable backend/connection ({backend.name})"
    if backend.name == "psij":
        executor_name = dict(backend.options).get("executor_name", "slurm")
        return (f"PSI/J · {executor_name}" if transport.is_local
                else f"{label} · PSI/J {executor_name}")
    if backend.name == "kkrunner":
        return f"{label} · supervised"
    return label


def _attachment_availability(record: RunRecord, state: str) -> tuple[bool, str | None]:
    """Explain whether a persisted execution can be observed after a GUI restart."""
    if state in _FINAL_STATES:
        return False, None
    if not record.execution.available:
        return False, "The saved backend or connection is unavailable; local result files can still be viewed."
    if record.execution.backend_id is None:
        return (
            False,
            "No backend identity has been saved for this calculation yet. "
            "Refresh statuses to attempt submission recovery.",
        )
    backend = record.execution.backend.create()
    if BackendCapability.REATTACH not in backend.supports:
        return False, f"The {backend.name} backend does not support reattachment."
    if backend.name == "direct":
        local = record.execution.transport.is_local
        streams = (record.files.stdout, record.files.stderr)
        if not any(
            stream is not None and (
                stream.local_path if local else stream.remote_path
            )
            for stream in streams
        ):
            return (
                False,
                "Direct execution has no saved output or error file to monitor. "
                "Refresh statuses to check whether its process still exists.",
            )
    return True, None


def _cancellation_availability(record: RunRecord, state: str) -> tuple[bool, str | None]:
    """Offer cancellation only while a native calculation can still be stopped."""
    if state in _FINAL_STATES:
        return False, None
    if state in ("downloading", "postprocessing"):
        return False, "Execution has finished; result finalization cannot be cancelled as a native job."
    if not record.execution.available:
        return False, "The saved backend or connection is unavailable."
    token = record.execution.backend_id
    if token is None:
        return False, "This calculation has no saved process reference to cancel."
    backend = record.execution.backend.create()
    if BackendCapability.CANCEL not in backend.supports:
        return False, f"The {backend.name} backend cannot cancel persisted processes."
    if backend.name == "direct" and not token.startswith("direct:"):
        return (
            False,
            "This older direct run has no verified PID/start-time identity. "
            "It cannot be safely killed; you can remove its history record instead.",
        )
    return True, None


@dataclass(frozen=True)
class PersistentJob:
    """Stable presentation model for one tracked ASE2SPRKKR run."""

    id: str
    state: str
    created_at: str
    updated_at: str
    task: str
    directory: str
    dataset: str | None
    target: str
    return_code: int | None
    message: str | None
    result_files_state: str | None = None
    finished_at: str | None = None
    attachable: bool = False
    reattach_unavailable_reason: str | None = None
    result_files_verified: bool = False
    failure_phase: str | None = None
    project_id: str | None = None
    profile: str | None = None
    cancelable: bool = False
    cancel_unavailable_reason: str | None = None
    imported_path: str | None = None
    imported_key: str | None = None

    @property
    def started_at(self) -> str:
        return self.created_at

    @property
    def is_final(self) -> bool:
        return self.state in _FINAL_STATES or self.imported_path is not None

    @property
    def can_retry_download(self) -> bool:
        """Whether an explicit retry of failed output transfer is meaningful."""
        return self.state == "failed" and self.failure_phase == "download" and self.return_code == 0

    @property
    def can_retry_postprocessing(self) -> bool:
        """Whether failed result processing can be retried without execution."""
        return (
            self.state == "failed"
            and self.failure_phase == "postprocessing"
            and self.return_code == 0
        )

    @classmethod
    def from_run_record(
        cls,
        record: RunRecord,
        *,
        result_files_verified: bool = False,
    ) -> "PersistentJob":
        state = record.state.value
        result_files_state = record.files.state.value
        finished_at = record.finished_at
        metadata = record.metadata.get("ase2sprkkr", {})
        attachable, reattach_unavailable_reason = _attachment_availability(record, state)
        cancelable, cancel_unavailable_reason = _cancellation_availability(record, state)
        return cls(
            id=job_id_for_record(record),
            state=state,
            created_at=str(record.created_at),
            updated_at=str(record.updated_at),
            task=str(metadata.get("task") or "Unknown").upper(),
            directory=str(record.execution.directory.local),
            dataset=(None if metadata.get("dataset") is None else str(metadata["dataset"])),
            target=_execution_target(record),
            return_code=record.return_code,
            message=record.message,
            result_files_state=result_files_state,
            finished_at=None if finished_at is None else str(finished_at),
            attachable=attachable,
            reattach_unavailable_reason=reattach_unavailable_reason,
            result_files_verified=result_files_verified,
            failure_phase=(None if record.failure_phase is None else record.failure_phase.value),
            project_id=record.project_id,
            profile=metadata.get("profile"),
            cancelable=cancelable,
            cancel_unavailable_reason=cancel_unavailable_reason,
        )

    @classmethod
    def from_external_reference(
        cls, key: str, project_id: str | None,
    ) -> "PersistentJob":
        """Project-visible result backed by a file, not a fabricated run."""
        path = Path(key.split("::", 1)[0])
        try:
            modified = path.stat().st_mtime
            timestamp = datetime.fromtimestamp(modified, timezone.utc).isoformat()
            files_state = "current"
        except OSError:
            timestamp = "1970-01-01T00:00:00+00:00"
            files_state = "missing"
        return cls(
            id=reference_id(key), state="imported", created_at=timestamp,
            updated_at=timestamp, task="—", directory=str(path.parent),
            dataset=path.name, target="External file", return_code=None,
            message=(
                "Imported output: original execution status and settings are unknown. "
                "Finish time is estimated from the file's last modification time."
            ),
            finished_at=timestamp if files_state == "current" else None,
            result_files_state=files_state,
            result_files_verified=True,
            project_id=project_id,
            imported_path=str(path),
            imported_key=key,
        )


class PersistentJobSource:
    """Read model and command adapter for ASE2SPRKKR runs and imported results.

    RunManager owns execution state; the stores own records. This source
    caches their presentation, resolves GUI ids and delegates explicit
    commands. Reading or refreshing it never starts a Run observer.
    """

    def __init__(
        self, manager: RunManager, *, projects: ProjectRepository,
        session_manager: RunManager,
    ) -> None:
        self._manager = manager
        self._session_manager = session_manager
        self._projects = projects
        self.history_warning: str | None = None
        self._unreadable_records: dict[Path, str] = {}
        # Serialize authoritative read -> projection -> cache publication.
        # Readers only need the short cache lock, never the history I/O lock.
        self._update_lock = Lock()
        self._lock = RLock()
        self._jobs: dict[str, PersistentJob] = {}
        self._listeners: set[JobChangedListener] = set()

        self.reload(notify=False)

    def _target(self, job_id: str):
        """Resolve a GUI job id to the manager and its native record id."""
        if is_session_job_id(job_id):
            return self._session_manager, job_id[len(_SESSION_PREFIX):]
        return self._manager, job_id

    def record(self, job_id: str):
        """Return the authoritative record for a GUI job identity."""
        manager, run_id = self._target(job_id)
        return manager.store.get(run_id)

    def subscribe(self, listener: JobChangedListener) -> Callable[[], None]:
        """Subscribe to metadata changes and return an unsubscribe callback."""
        with self._lock:
            self._listeners.add(listener)

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.discard(listener)

        return unsubscribe

    def _notify(self, job_id: str) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(job_id)
            except Exception:  # noqa: BLE001 - subscriber isolation boundary
                logger.exception("Persistent-job listener failed for %s", job_id)

    def jobs(self) -> tuple[PersistentJob, ...]:
        """Return cached history without backend or filesystem I/O."""
        with self._lock:
            jobs = tuple(self._jobs.values())
        return tuple(sorted(jobs, key=lambda job: job.started_at, reverse=True))

    def job(self, job_id: str) -> PersistentJob | None:
        with self._lock:
            return self._jobs.get(str(job_id))

    def _report_unreadable_record(self, error: CorruptRunRecord) -> None:
        """Keep a damaged history entry visible as a warning, not as a run."""
        logger.warning("Skipping unreadable persistent run: %s", error)
        with self._lock:
            self._unreadable_records[error.path] = str(error)
            count = len(self._unreadable_records)
            noun = "record" if count == 1 else "records"
            verb = "is" if count == 1 else "are"
            self.history_warning = (
                f"{count} stored run {noun} could not be read and {verb} not shown "
                "in the history. The original files were not changed. "
                "See the application log for their paths and errors."
            )

    def _records(self):
        """Browse both stores while keeping strict reads for run operations."""
        with self._lock:
            self._unreadable_records.clear()
            self.history_warning = None
        records = []
        for manager in (self._manager, self._session_manager):
            records.extend(manager.store.runs(on_error=self._report_unreadable_record))
        return tuple(records)

    def reload(self, *, notify: bool = True) -> tuple[PersistentJob, ...]:
        """Reload authoritative metadata without starting another runner."""
        with self._update_lock:
            records = self._records()
            loaded = {
                job_id_for_record(record): PersistentJob.from_run_record(record)
                for record in records
                if "ase2sprkkr" in record.metadata
            }
            for key, project_id in assignments(self._projects).items():
                imported = PersistentJob.from_external_reference(key, project_id)
                loaded[imported.id] = imported
            with self._lock:
                previous = self._jobs
                self._jobs = loaded
            changed = tuple(
                job_id
                for job_id in set(previous) | set(loaded)
                if previous.get(job_id) != loaded.get(job_id)
            )
        if notify:
            for job_id in changed:
                self._notify(job_id)
        return self.jobs()

    def job_changed(self, job_id) -> PersistentJob | None:
        """Read one authoritative record and publish its changed presentation."""
        job_id = job_id if isinstance(job_id, str) else job_id_for_record(job_id)
        if job_id.startswith("external:"):
            self.reload()
            return self.job(job_id)
        with self._update_lock:
            try:
                record = self.record(job_id)
            except (FileNotFoundError, KeyError, CorruptRunRecord) as exc:
                if isinstance(exc, CorruptRunRecord):
                    self._report_unreadable_record(exc)
                record = None

            job = (PersistentJob.from_run_record(record)
                   if record is not None and "ase2sprkkr" in record.metadata else None)
            with self._lock:
                previous = self._jobs.get(job_id)
                if job is None:
                    self._jobs.pop(job_id, None)
                else:
                    self._jobs[job_id] = job
        if previous != job:
            self._notify(job_id)
        return job

    def refresh(self) -> tuple[PersistentJob, ...]:
        """Ask the managers to refresh their own records, then update the view."""
        for manager in (self._manager, self._session_manager):
            manager.refresh(on_error=self._report_unreadable_record)
        return self.reload()

    def check_result_files(self, job_id: str) -> PersistentJob:
        job = self.job(str(job_id))
        if job is not None and job.imported_path is not None:
            self.reload()
            current = self.job(str(job_id))
            if current is None:
                raise FileNotFoundError(job_id)
            return current
        with self._update_lock:
            record = self.record(str(job_id))
            job = PersistentJob.from_run_record(record, result_files_verified=True)
            changed = self._replace_cached_job(job)
        if changed:
            self._notify(job.id)
        return job

    def attach(
        self,
        job_id: str,
        *,
        callback=None,
        output_listener=None,
    ) -> Any:
        """Attach to an active run or explicitly retry a failed download."""
        manager, run_id = self._target(str(job_id))
        return attach_project_run(
            manager,
            run_id,
            callback=callback,
            output_listener=output_listener,
        )

    def cancel(self, job_id: str) -> PersistentJob | None:
        """Ask the backend to stop the native process, then refresh its record."""
        try:
            manager, run_id = self._target(str(job_id))
            manager.cancel(run_id)
        finally:
            self.job_changed(str(job_id))
        return self.job(str(job_id))

    def referenced_file_deletion(self, job_id: str) -> FileDeletionPlan:
        """Return the deletable files and their current reference/snapshot state."""
        manager, run_id = self._target(str(job_id))
        return plan_referenced_file_deletion(
            manager, self._projects, run_id, self.job(str(job_id)),
        )

    def remote_file_review(self, job_id: str) -> RemoteFileReview:
        manager, run_id = self._target(str(job_id))
        return inspect_remote_file_review(
            manager, run_id, self.job(str(job_id)),
        )

    def capture_remote_cleanup(self, job_id: str) -> Any:
        manager, run_id = self._target(str(job_id))
        return prepare_remote_file_cleanup(
            manager, run_id, self.job(str(job_id)),
        )

    def cleanup_remote_files(
        self, record: RunRecord, review: RemoteFileReview,
        selected_files: tuple[str, ...],
    ) -> tuple[dict, ...]:
        manager, _ = self._target(job_id_for_record(record))
        return perform_remote_file_cleanup(manager, record, review, selected_files)

    def forget(
        self, job_id: str, *, force: bool = False, delete_files: bool = False,
        expected_files: tuple[str, ...] | None = None,
        expected_file_snapshots: tuple | None = None,
        expected_shared_files: tuple[str, ...] = (),
        allow_shared_files: bool = False,
    ) -> Any:
        """Remove a history/reference entry; optionally delete reviewed local files.

        File cleanup is strictly opt-in and happens before removing the record so
        a file-removal failure leaves a retriable history entry.
        """
        job_id = str(job_id)
        job = self.job(job_id)
        manager, run_id = self._target(job_id)
        if delete_files:
            delete_confirmed_local_files(
                manager, self._projects, run_id, job,
                expected_files=expected_files,
                expected_file_snapshots=expected_file_snapshots,
                expected_shared_files=expected_shared_files,
                allow_shared_files=allow_shared_files,
            )

        if job is not None and job.imported_path is not None:
            remove_reference(self._projects, job.id)
            self.reload()
            return job
        removed = manager.forget(run_id, force=force)
        self.job_changed(job_id)
        return removed

    def retry_postprocessing(self, job_id: str) -> Any:
        """Reprocess persisted output files without executing the run again."""
        try:
            manager, run_id = self._target(str(job_id))
            return retry_project_postprocessing(manager, run_id)
        finally:
            self.job_changed(str(job_id))

    def reassign_runs(
        self, projects: ProjectRepository, job_ids, project_id: str | None,
        *, expected_project_ids,
    ) -> None:
        """Move tracked runs through KKRunner and external references through their store."""
        job_ids = tuple(job_ids)
        if any(is_session_job_id(job_id) for job_id in job_ids):
            raise ValueError("Session runs are not project history")
        imported_ids = tuple(
            job_id for job_id in job_ids
            if (job := self.job(job_id)) is not None and job.imported_path is not None
        )
        run_ids = tuple(job_id for job_id in job_ids if job_id not in imported_ids)
        if run_ids:
            records = projects.reassign_runs(
                run_ids, project_id, store=self._manager.store,
                expected_project_ids={key: expected_project_ids[key] for key in run_ids},
            )
            for record in records:
                self.job_changed(record.id)
        if imported_ids:
            reassign_references(
                projects, imported_ids, project_id,
                expected_project_ids={key: expected_project_ids[key] for key in imported_ids},
            )
            self.reload()

    def load_result(self, job_id: str, *, restore_missing=True) -> Any:
        """Load the native result; only explicit requests may restore missing files."""
        job = self.job(str(job_id))
        if job is not None and job.imported_path is not None:
            expected_digest = job.imported_key.partition("::")[2]
            version = FileVersion.capture(job.imported_path, digest=bool(expected_digest),
                                          require_digest=bool(expected_digest))
            if expected_digest and version.digest != expected_digest:
                raise RuntimeError(
                    "The imported output file has changed since it was assigned. "
                    "Add the updated file again to capture its new version."
                )
            result = ResultLoader().load_file(job.imported_path)
            if not version.is_current():
                raise RuntimeError("The imported output file changed while loading its result.")
            return result
        manager, run_id = self._target(str(job_id))
        result = load_project_result(
            manager, run_id, allow_unparsed=True, restore_missing=restore_missing,
        )
        # A passive parse only refreshes persisted metadata. An explicit load
        # may have restored artifacts and can report their verified state.
        if restore_missing:
            self.check_result_files(job_id)
        else:
            self.job_changed(job_id)
        return result

    def _replace_cached_job(self, job: PersistentJob) -> bool:
        with self._lock:
            previous = self._jobs.get(job.id)
            self._jobs[job.id] = job
        return previous != job
