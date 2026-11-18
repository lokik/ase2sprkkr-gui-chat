"""Backend execution service for one SPR-KKR calculation request."""
from __future__ import annotations

import logging
import warnings
from typing import Any, Callable

from ase2sprkkr import SPRKKR
from kkrunner.runs import MpiNotFoundWarning

from ase2sprkkr_gui.gui.application.execution_cancellation import (
    Cancellation,
    ExecutionCancelled,
    register_runner,
    wait_for_runner,
)
from ase2sprkkr_gui.gui.application.result_loading import LoadedResult, load_result
from ase2sprkkr_gui.gui.application.workspace_controller import CalculationRequest
from ase2sprkkr_gui.gui.input_parameters.dataset import dataset_value, filename_component

logger = logging.getLogger(__name__)


class SprkkrExecution:
    """Run one immutable calculation request without any Qt dependency."""

    def __init__(
        self,
        request: CalculationRequest,
        cancellation: Cancellation,
        *,
        profile_name: str | None = None,
        execution_manager=None,
        project_id: str | None = None,
        output: Callable[[str, str], None] | None = None,
        status: Callable[[str], None] | None = None,
        warning: Callable[[str], None] | None = None,
        persistent_job_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.request = request
        self.cancellation = cancellation
        self.profile_name = profile_name
        self.execution_manager = execution_manager
        self.project_id = project_id
        self._output = output or (lambda _text, _kind: None)
        self._status = status or (lambda _text: None)
        self._warning = warning or (lambda _text: None)
        self._persistent_job_changed = persistent_job_changed

    def file_names(self) -> tuple[str, str, str]:
        """Return input, potential and output filename templates for the request."""
        dataset = dataset_value(self.request.input_parameters)
        if dataset is None:
            return "%a_%T.inp", "%a.pot", "%a_%T.out"
        base = filename_component(dataset)
        if not base:
            return "%a_%T.inp", "%a.pot", "%a_%T.out"
        return f"{base}_%T.inp", f"{base}.pot", f"{base}_%T.out"

    def _with_mpi_warnings(self, operation):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", MpiNotFoundWarning)
            result = operation()

        for item in caught:
            if issubclass(item.category, MpiNotFoundWarning):
                self._warning(str(item.message))
            else:
                warnings.showwarning(
                    item.message,
                    item.category,
                    item.filename,
                    item.lineno,
                    file=item.file,
                    line=item.line,
                )
        return result

    def _calculator(self) -> SPRKKR:
        return SPRKKR() if self.profile_name is None else SPRKKR(profile=self.profile_name)

    def _project_context(self):
        if self.project_id is None:
            # Even an unassigned clean-cache run needs the same reservation
            # store as project runs. Without an injected manager, ASE2SPRKKR
            # supplies its default persistent manager for clear_cache=True.
            if not self.request.clear_cache or self.execution_manager is None:
                return None
            from kkrunner.runs.manager import RunManager
            # ProjectRunManager cannot create unassigned jobs; an ordinary
            # RunManager can reserve against the very same shared RunStore.
            return RunManager(
                self.execution_manager.store,
                client_pool=self.execution_manager.client_pool,
            )
        if self.execution_manager is None:
            return self.project_id
        return self.execution_manager.for_project(self.project_id)

    def _create_runner(self, calculator: SPRKKR):
        input_file, potential_file, output_file = self.file_names()
        # The backend Potential.from_atoms factory recognizes embedded Atoms.
        # Keep the same public atoms= path as periodic calculations.
        source = (
            {"potential": self.request.potential}
            if self.request.potential is not None else
            {"atoms": self.request.atoms}
        )
        return self._with_mpi_warnings(
            lambda: calculator.create_run(
                **source,
                input_parameters=self.request.input_parameters,
                directory=self.request.directory,
                input_file=input_file,
                potential_file=potential_file,
                output_file=output_file,
                empty_spheres=False,
                read_callback=self._output,
                print_output=False,
                project=self._project_context(),
                restart_scf=self.request.restart_scf,
                clear_cache=self.request.clear_cache,
            )
        )

    def prepare_files(self):
        """Write native input/potential files without submitting a runner."""
        input_file, potential_file, _output_file = self.file_names()
        source = (
            {"potential": self.request.potential}
            if self.request.potential is not None else
            {"atoms": self.request.atoms}
        )
        return self._calculator().save_input(
            **source,
            input_parameters=self.request.input_parameters,
            directory=self.request.directory,
            input_file=input_file,
            potential_file=potential_file,
            output_file=False,
            empty_spheres=False,
            restart_scf=self.request.restart_scf,
        )

    def _notify_persistent_job_changed(self, runner: Any) -> None:
        callback = self._persistent_job_changed
        if callback is None or self.project_id is None:
            return
        try:
            callback(str(runner.id))
        except Exception:  # noqa: BLE001 - observer must never fail execution
            logger.exception("Persistent-job change observer failed for %s", runner.id)

    def _submit(self, calculator: SPRKKR):
        runner = self._create_runner(calculator)
        register_runner(self.cancellation, runner)

        try:
            self._with_mpi_warnings(runner.submit)
        finally:
            # Submission is the first point at which ASE2SPRKKR may have written
            # the persistent RunRecord. The observer receives only its stable id
            # and must reload authoritative metadata from the store.
            self._notify_persistent_job_changed(runner)
        if self.cancellation.requested:
            raise ExecutionCancelled
        return runner

    def run(self) -> LoadedResult:
        """Execute the request and return its loaded result."""
        self._status("Preparing calculation…")
        runner = self._submit(self._calculator())

        record = runner.record if self.project_id is not None else None
        if record is not None and not record.execution.transport.is_local:
            transport = record.execution.transport
            self._status(f"Running on {transport.host}…")
        else:
            self._status("Running…")

        try:
            result = wait_for_runner(self.cancellation, runner)
        finally:
            # A project runner persists its terminal record while waiting.
            self._notify_persistent_job_changed(runner)

        self._status("Loading result…")
        kwargs = {
            "fallback_directory": self.request.directory,
            "persistent_ref": runner.id if self.project_id is not None else None,
        }
        host = getattr(self.request.atoms, "embedding_host", None)
        if host is not None:
            # Native CLUPOT is not a standalone structure. Preserve the
            # canonical launch host when interpreting the result file.
            kwargs["embedding_host"] = host
        loaded = load_result(result, **kwargs)
        self._status("Finished")
        return loaded
