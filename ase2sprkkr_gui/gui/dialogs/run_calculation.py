from __future__ import annotations

import logging
from threading import Event
from typing import Any, Callable
from collections.abc import Sequence

from PyQt6.QtCore import QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QMessageBox
from kkrunner import RunState
from kkrunner.backends.contracts import SubmissionNotStarted
from kkrunner.runs import DuplicateSubmissionError

from ase2sprkkr_gui.gui.application.execution_cancellation import (
    ExecutionCancelled,
    RunCancellation,
)
from ase2sprkkr_gui.gui.application.sprkkr_execution import SprkkrExecution
from ase2sprkkr_gui.gui.application.persistent_jobs import job_id_for_record
from ase2sprkkr_gui.gui.application.workspace_controller import CalculationRequest
from ase2sprkkr_gui.gui.dialogs.run_display import info_page_settings
from ase2sprkkr_gui.gui.dialogs.run_monitor import RunMonitorWindow, RunMonitorWorker
from ase2sprkkr_gui.gui.dialogs.run_conflict import SubmissionConflictDialog


logger = logging.getLogger(__name__)


class _SprkkrRunWorker(RunMonitorWorker):
    """Qt signal adapter around the backend-only :class:`SprkkrExecution`."""

    persistentJobChanged = pyqtSignal(object)
    submitted = pyqtSignal(object)
    conflict = pyqtSignal(object)
    submissionRejected = pyqtSignal(str)

    def __init__(
        self,
        request: CalculationRequest,
        cancellation: RunCancellation,
        *,
        profile_name: str | None = None,
        execution_manager=None,
        project_id: str | None = None,
    ) -> None:
        super().__init__()
        self._cancellation = cancellation
        self._execution = SprkkrExecution(
            request,
            cancellation,
            profile_name=profile_name,
            execution_manager=execution_manager,
            project_id=project_id,
            output=self.output.emit,
            status=self.status.emit,
            warning=self.warning.emit,
            persistent_job_changed=self.persistentJobChanged.emit,
            submitted=self.submitted.emit,
        )

    def _emit_cancelled(self) -> None:
        self.status.emit("Monitoring stopped" if self._cancellation.observation_only else "Cancelled")
        self.cancelled.emit()

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.finished.emit(self._execution.run())
        except ExecutionCancelled:
            self._emit_cancelled()
        except DuplicateSubmissionError as exc:
            # This is a rejected preflight reservation, not a backend failure.
            # Preserve the RunRecords and per-file collisions for the UI.
            if self._cancellation.requested:
                self._emit_cancelled()
            else:
                self.conflict.emit(exc)
                self.cancelled.emit()
        except SubmissionNotStarted as exc:
            if self._cancellation.requested:
                self._emit_cancelled()
            else:
                self.submissionRejected.emit(str(exc))
                self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - worker/backend boundary
            logger.exception("SPR-KKR calculation or result loading failed")
            self.error.emit(str(exc))


class SprkkrRunWindow(RunMonitorWindow):
    """Create an SPR-KKR runner and display it in the generic run monitor."""

    persistentJobChanged = pyqtSignal(str)
    conflictJobChanged = pyqtSignal(object)

    def __init__(
        self,
        request: CalculationRequest,
        parent: Any | None = None,
        *,
        profile_name: str | None = None,
        profile_description: str | None = None,
        profile_fields: Sequence[tuple[str, str]] | None = None,
        project_id: str | None = None,
        on_finished=None,
        on_submitted: Callable[[str | None], None] | None = None,
        on_back_to_setup: Callable[[], None] | None = None,
    ) -> None:
        # Preserve the old free-form interface for external callers, but use
        # structured fields whenever the profile has been resolved by the GUI.
        header = None
        header_fields = [("Directory", str(request.directory))]
        if profile_fields is None and profile_description:
            header = f"Directory: {request.directory}\n{profile_description}"
            header_fields = None
        elif profile_fields is not None:
            header_fields.extend(profile_fields)

        self._request = request
        self._execution_manager = None
        self.launch_project_id = project_id
        self._project_id: str | None = project_id
        self._back_to_setup = on_back_to_setup
        self._submitted_callback = on_submitted
        self._submission_error: str | None = None

        def worker_factory(cancellation: RunCancellation) -> _SprkkrRunWorker:
            worker = _SprkkrRunWorker(
                self._request,
                cancellation,
                profile_name=profile_name,
                execution_manager=self._execution_manager,
                project_id=self._project_id,
            )
            worker.persistentJobChanged.connect(self._persistent_job_changed_received)
            worker.submitted.connect(self._on_submitted)
            worker.conflict.connect(self._on_conflict)
            worker.submissionRejected.connect(self._on_submission_rejected)
            return worker

        # Ownership and execution/project context are established by the caller
        # before ``start()``. This prevents a fast local submit from publishing a
        # run id before the application owns this top-level window.
        parameters = getattr(request, "input_parameters", None)
        task_name = str(getattr(parameters, "task_name", ""))
        super().__init__(
            worker_factory,
            parent,
            title="SPRKKR Run",
            header=header,
            header_fields=header_fields,
            warning_title="MPI Not Detected",
            on_finished=on_finished,
            task_name=task_name,
            **info_page_settings(task_name, parameters),
            autostart=False,
        )
        self._conflict_dialog: SubmissionConflictDialog | None = None

    def request_shutdown(self) -> tuple[Event, ...]:
        operations = super().request_shutdown()
        if self._conflict_dialog is not None:
            # Abandon this rejected launch even if another monitor later
            # prevents the application from exiting.
            self._close_requested = True
            if "resolve-conflicting-runs" not in self._background_operations:
                self._conflict_dialog.reject()
        return operations

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._conflict_dialog is not None:
            # A conflict dialog is modal and may outlive the submission
            # worker. Neither a close request nor thread.finished may destroy
            # its parent. The dialog's own Cancel remains available when idle.
            event.ignore()
            return
        super().closeEvent(event)

    def set_execution_context(self, manager, *, project_id: str | None = None) -> None:
        """Capture persistent execution context before this monitor is started."""
        if self._thread is not None:
            raise RuntimeError("execution context cannot change after the run has started")
        self._execution_manager = manager
        self._project_id = project_id

    @pyqtSlot(object)
    def _on_submitted(self, input_path: str | None) -> None:
        """Deliver submission acknowledgement on the GUI thread."""
        if self._submitted_callback is not None:
            self._submitted_callback(input_path)

    @pyqtSlot(object)
    def _persistent_job_changed_received(self, record) -> None:
        self.set_job_id(record.id)
        self.persistentJobChanged.emit(job_id_for_record(record))

    def _return_to_setup(self) -> None:
        """Return control to the original run picker after a rejected submission."""
        if self._back_to_setup is None:
            return
        self._set_status("Returning to run setup…")
        # Let the worker's terminal signals and this dialog's close complete
        # before opening the next modal profile/directory picker.
        callback = self._back_to_setup
        self.close()
        QTimer.singleShot(0, callback)

    @pyqtSlot(str)
    def _on_submission_rejected(self, message: str) -> None:
        self._on_error(message)
        self._set_status("Calculation not started")
        self._submission_error = message
        if self._thread is None or not self._thread.isRunning():
            self._show_submission_error()

    @pyqtSlot()
    def _thread_finished(self) -> None:
        super()._thread_finished()
        if not self._close_requested:
            self._show_submission_error()

    def _show_submission_error(self) -> None:
        message = self._submission_error
        if message is None or self._close_requested or self._cancellation.requested or self._shutting_down:
            return
        self._submission_error = None
        QMessageBox.critical(self, "Could Not Start Calculation", message)
        self._return_to_setup()

    @pyqtSlot(object)
    def _on_conflict(self, conflict: DuplicateSubmissionError) -> None:
        """Keep the resolution dialog available while any conflict remains."""
        if self._cancellation.requested or self._shutting_down:
            return
        self._set_status("New calculation not started — files are in use")
        dialog = SubmissionConflictDialog(conflict, parent=self)
        self._conflict_dialog = dialog
        dialog.actionRequested.connect(self._on_conflict_action)
        try:
            decision = dialog.exec()
        finally:
            self._conflict_dialog = None
            dialog.deleteLater()
        if self._shutting_down:
            return
        if self._close_requested:
            self.close()
            return
        if decision == dialog.DialogCode.Accepted:
            self._set_status("Conflicts resolved — starting calculation…")
            self._retry_after_conflict()
        else:
            self._set_status("New calculation cancelled; existing jobs were kept")
            self._return_to_setup()

    def _conflict_manager(self):
        if self._execution_manager is not None:
            return self._execution_manager
        from ase2sprkkr.sprkkr.projects import default_project_repository
        from ase2sprkkr_gui.gui.application.persistent_jobs import project_run_manager

        return project_run_manager(default_project_repository())

    @pyqtSlot(str, object)
    def _on_conflict_action(self, action: str, selected: object) -> None:
        if self._shutting_down or self._conflict_dialog is None:
            return
        proposed = self._conflict_dialog.proposed_record
        records = self._conflict_dialog.conflicting_records
        self._start_background_operation(
            "resolve-conflicting-runs",
            lambda: self._resolve_conflict_action(action, tuple(selected), proposed, records),
            self._on_conflict_action_finished,
        )

    def _resolve_conflict_action(
        self, action: str, selected: tuple, proposed, records
    ) -> tuple:
        """Resolve only selected records, then recheck the global reservations.

        An overridden FAILED state or a deleted record does not terminate a
        native process. User confirmation for these operations belongs to the
        dialog, and the actual new submission still goes through reserve().
        """
        errors: dict[tuple | str, str] = {}
        updated = False
        conflicts = None
        try:
            manager = self._conflict_manager()
            if action == "refresh":
                try:
                    for refreshed in manager.refresh(manager.conflicts(proposed)):
                        self.conflictJobChanged.emit(refreshed)
                except Exception as exc:  # noqa: BLE001 - backend status query
                    logger.exception("Failed to refresh conflicting job statuses")
                    errors["Refresh"] = str(exc)
            elif action in ("stop", "fail", "delete"):
                by_key = {record.key: record for record in records}
                for key in selected:
                    record = by_key[key]
                    try:
                        record = record.store.get(record.id)
                        if action == "stop":
                            if not record.state.is_terminal:
                                manager.cancel(record)
                        elif action == "fail":
                            if not record.state.is_terminal:
                                result = manager.transition(
                                    record,
                                    state=RunState.FAILED,
                                    expected_record=record,
                                    message=(
                                        "Manually marked failed to clear a file "
                                        "reservation; process termination was NOT verified."
                                    ),
                                )
                                if result.state != RunState.FAILED:
                                    raise RuntimeError(
                                        "The job could not be marked failed in its current state"
                                    )
                                updated = True
                        else:
                            manager.forget(record, force=True)
                            updated = True
                        self.conflictJobChanged.emit(record)
                    except FileNotFoundError:
                        # The record was removed concurrently; verify the
                        # complete active set below rather than assume success.
                        self.conflictJobChanged.emit(record)
                    except Exception as exc:  # noqa: BLE001 - backend/store boundary
                        logger.exception(
                            "Could not %s conflicting job %s", action, record.id
                        )
                        errors[key] = str(exc)
            else:
                errors["Action"] = f"Unsupported conflict action: {action}"

            # Use the same collision predicate and global store as reserve().
            # Do not assume that the initial conflict list remains complete.
            conflicts = DuplicateSubmissionError(proposed, manager.conflicts(proposed))
        except Exception as exc:  # noqa: BLE001 - manager/storage boundary
            logger.exception("Could not check run reservation conflicts")
            errors["Reservation check"] = str(exc)
        return action, conflicts, errors, updated

    def _on_conflict_action_finished(self, outcome: object, error: BaseException | None) -> None:
        dialog = self._conflict_dialog
        if dialog is None:
            return
        try:
            if self._shutting_down:
                return
            if error is not None:
                dialog.set_verification_error({"Conflict resolution": str(error)})
                return
            action, conflicts, errors, updated = outcome
            if conflicts is not None:
                dialog.set_conflicts(
                    conflicts,
                    errors=errors,
                    manually_changed=updated and action in ("fail", "delete"),
                )
            elif errors:
                # A failed store check cannot justify enabling Calculate.
                dialog.set_verification_error(errors)
        finally:
            dialog.set_busy(False)
            if self._shutting_down:
                dialog.reject()

    @pyqtSlot()
    def _retry_after_conflict(self) -> None:
        """Retry only a rejected preflight submission, with fresh runner state."""
        if self._close_requested or self._shutting_down or self._cancellation.requested:
            return
        previous = self._thread
        if previous is not None and previous.isRunning():
            previous.finished.connect(self._retry_after_conflict)
            return
        # The first worker ended at preflight, before any native job started.
        # Do not reuse its cancelled monitor state or its failed runner.
        self._thread = None
        self._worker = None
        self._cancellation = RunCancellation()
        self._stop_btn.setEnabled(True)
        if previous is not None:
            previous.deleteLater()
        self._start_worker()
