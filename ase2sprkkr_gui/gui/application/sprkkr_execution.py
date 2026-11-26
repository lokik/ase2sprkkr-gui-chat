"""Backend execution service for one SPR-KKR calculation request."""
from __future__ import annotations

import logging
import warnings
from dataclasses import replace
from typing import Any, Callable

from ase2sprkkr import SPRKKR
from kkrunner.backends.contracts import SubmissionNotStarted
from kkrunner.files import FileManifest
from kkrunner.runs import MpiNotFoundWarning, Run, RunRecord, RunState
from kkrunner.projects import ProjectRunManager
from kkrunner.runs.manager import RunManager

from ase2sprkkr_gui.gui.application.execution_cancellation import (
    Cancellation,
    register_runner,
    wait_for_runner,
)
from ase2sprkkr_gui.gui.application.result_loading import (
    FileBackedInput, LoadedResult, PotentialVersionChanged, load_result,
)
from ase2sprkkr_gui.gui.application.workspace_controller import CalculationRequest
from ase2sprkkr_gui.gui.input_parameters.dataset import dataset_value

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
        persistent_job_changed: Callable[[RunRecord], None] | None = None,
        submitted: Callable[[str | None], None] | None = None,
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
        self._submitted = submitted
        self._file_backed_input = None

    def file_names(self) -> tuple[str, str, str]:
        """Return input, potential and output filename templates for the request."""
        dataset = dataset_value(self.request.input_parameters)
        if dataset is None:
            return "%a_%T.inp", "%a.pot", "%a_%T.out"
        # DATASET is literal. Escape the percent markers used by ASE2SPRKKR.
        base = dataset.replace("%", "%%")
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
            if isinstance(self.execution_manager, RunManager) and not isinstance(self.execution_manager, ProjectRunManager):
                return self.execution_manager
            # Even an unassigned clean-cache run needs the same reservation
            # store as project runs. Without an injected manager, ASE2SPRKKR
            # supplies its default persistent manager for clear_cache=True.
            if not self.request.clear_cache or self.execution_manager is None:
                return None
            # ProjectRunManager cannot create unassigned jobs; an ordinary
            # RunManager can reserve against the very same shared RunStore.
            return RunManager(
                self.execution_manager.store,
                client_pool=self.execution_manager.client_pool,
                conflict_stores=self.execution_manager.conflict_stores,
            )
        if self.execution_manager is None:
            return self.project_id
        return self.execution_manager.for_project(self.project_id)

    def _create_runner(self, calculator: SPRKKR):
        input_file, potential_file, output_file = self.file_names()
        # The backend Potential.from_atoms factory recognizes embedded Atoms.
        # Keep the same public atoms= path as periodic calculations.
        return self._with_mpi_warnings(
            lambda: calculator.create_run(
                **self._input_source(potential_file),
                input_parameters=self.request.input_parameters,
                directory=self.request.directory,
                input_file=input_file,
                output_file=output_file,
                empty_spheres=False,
                read_callback=self._output,
                print_output=False,
                project=self._project_context(),
                restart_scf=self.request.restart_scf,
                clear_cache=self.request.clear_cache,
                create_subdirs=True,
            )
        )

    def prepare_files(self):
        """Write native input/potential files without submitting a runner."""
        input_file, potential_file, _output_file = self.file_names()
        return self._calculator().save_input(
            **self._input_source(potential_file),
            input_parameters=self.request.input_parameters,
            directory=self.request.directory,
            input_file=input_file,
            output_file=False,
            empty_spheres=False,
            restart_scf=self.request.restart_scf,
            create_subdirs=True,
        )

    def _input_source(self, potential_file):
        if self.request.potential is False:
            # The parsed atoms establish geometry, but the native POTFIL files
            # remain authoritative and must never be regenerated from them.
            return {"potential": False, "atoms": self.request.atoms}
        if self.request.potential is not None:
            return {"potential": self.request.potential, "potential_file": potential_file}
        source = self.request.reusable_potential_version
        if source is not None and not self.request.restart_scf:
            # A string without potential_file selects ASE2SPRKKR's existing
            # POTFIL path: it references the original bytes without rewriting.
            if not source.available or not source.is_current():
                raise PotentialVersionChanged(
                    f"Source potential changed before calculation: {source.path}"
                )
            return {"potential": str(source.path)}
        return {"atoms": self.request.atoms, "potential_file": potential_file}

    def _notify_persistent_job_changed(self, runner: Run) -> None:
        callback = self._persistent_job_changed
        record = runner.record
        if callback is None or record is None:
            return
        # A rejected reservation has an in-memory NEW record, but was never
        # inserted into the store. Do not bind the monitor to that attempt.
        if record.state == RunState.NEW:
            return
        try:
            callback(record)
        except Exception:  # noqa: BLE001 - observer must never fail execution
            logger.exception("Persistent-job change observer failed for %s", runner.id)

    def _submit(self, calculator: SPRKKR):
        runner = self._create_runner(calculator)
        register_runner(self.cancellation, runner)

        try:
            if (
                self.request.potential is False
                or (self.request.potential is None
                    and self.request.reusable_potential_version is not None
                    and not self.request.restart_scf)
            ):
                self._with_mpi_warnings(lambda: runner.submit(validate_inputs=self._validate_file_input))
            else:
                self._with_mpi_warnings(runner.submit)
        finally:
            # Submission is the first point at which ASE2SPRKKR may have written
            # the RunRecord. The observer receives its identity and reloads
            # authoritative metadata from the owning store.
            self._notify_persistent_job_changed(runner)
        # submit() returned successfully: ASE2SPRKKR has prepared its input
        # files and handed the calculation to the backend. Result completion
        # and parsing are unrelated to whether those inputs were saved.
        if self._submitted is not None:
            try:
                # Exact staged input, not an inferred filename template.
                path = FileManifest.from_spec(runner.spec).input_file
                self._submitted(str(path) if path is not None else None)
            except Exception:  # noqa: BLE001 - observers must not fail a launched job
                logger.exception("Submission observer failed")
        # A successfully accepted run owns files and a completion observer.
        # Even cancellation/detachment must wait for that lifecycle to settle.
        return runner

    def _validate_file_input(self, manifest):
        """Verify all reused source bytes against their staged manifest entries."""
        if self.request.potential is False:
            source = self.request.file_source
            if source is None:
                raise SubmissionNotStarted("File-backed calculation has no validated input source.")
            expected_versions = source.versions
        else:
            source = None
            version = self.request.reusable_potential_version
            if version is None or self.request.restart_scf or self.request.potential is not None:
                raise SubmissionNotStarted("Calculation has no reusable source potential.")
            expected_versions = (version,)

        for expected in expected_versions:
            staged = manifest.input_version(expected.path)
            if staged is None or staged != expected or not expected.is_current():
                raise SubmissionNotStarted(
                    f"Source potential does not match the launch input: {expected.path}"
                )
        if source is not None:
            self._file_backed_input = FileBackedInput(source.potential.atoms, source.versions)

    def run(self) -> LoadedResult:
        """Execute the request and return its loaded result."""
        self._status("Preparing calculation…")
        runner = self._submit(self._calculator())

        record = runner.record
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
        loaded = load_result(
            result,
            fallback_directory=self.request.directory,
            persistent_ref=runner.id if self.project_id is not None else None,
        )
        if self._file_backed_input is not None:
            loaded = replace(loaded, file_backed_input=self._file_backed_input)
        self._status("Finished")
        return loaded
