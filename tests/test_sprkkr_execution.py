from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ase import Atoms
from types import SimpleNamespace
import pytest
from kkrunner import ExecutionOutcome

from ase2sprkkr_gui.gui.application import sprkkr_execution as execution_module
from ase2sprkkr_gui.gui.application.execution_cancellation import ExecutionCancelled
from ase2sprkkr_gui.gui.application.sprkkr_execution import SprkkrExecution
from ase2sprkkr_gui.gui.application.workspace_controller import CalculationRequest


class _Cancellation:
    def __init__(self, *, requested: bool = False, register: bool = True):
        self.requested = requested
        self._register = register
        self.runner = None

    def register(self, runner):
        self.runner = runner
        return self._register and not self.requested


@pytest.mark.parametrize("code", [1, None, 0])
def test_raw_execution_outcome_is_reported_before_result_loading(monkeypatch, tmp_path, code):
    outcome = ExecutionOutcome(
        output_file=str(tmp_path / "Fe_SCF.out"),
        error_file=str(tmp_path / "Fe_SCF.out.err"),
        return_code=code,
    )
    runner = SimpleNamespace(wait=lambda: outcome, id="failed-run", failure_message="Native execution stopped")
    statuses = []
    execution = SprkkrExecution(_request(tmp_path), _Cancellation(), status=statuses.append)
    monkeypatch.setattr(execution, "_calculator", lambda: object())
    monkeypatch.setattr(execution, "_submit", lambda _calculator: runner)

    def forbid_load(*_args, **_kwargs):
        raise AssertionError("Raw execution diagnostics must not be adopted as TaskResult")

    monkeypatch.setattr(execution_module, "load_result", forbid_load)
    with pytest.raises(RuntimeError) as caught:
        execution.run()
    message = str(caught.value)
    assert "Native execution stopped" in message
    assert str(outcome.output_file) in message
    assert str(outcome.error_file) in message
    if code == 1:
        assert "exit code 1" in message
    assert "Loading result…" not in statuses
    assert "Finished" not in statuses


def _request(tmp_path, *, restart_scf: bool = False, clear_cache: bool = False) -> CalculationRequest:
    return CalculationRequest(
        atoms=Atoms("Fe"),
        input_parameters=object(),
        directory=str(tmp_path),
        structure_id=1,
        state_id=1,
        restart_scf=restart_scf,
        clear_cache=clear_cache,
    )


def test_execution_constructs_sprkkr_with_selected_profile(monkeypatch, tmp_path):
    captured = {}

    class Calculator:
        def create_run(self, **_kwargs):
            return Runner()

    class Runner:
        id = "run-1"

        def submit(self):
            pass

        def wait(self):
            return object()

    def make_calculator(*, profile):
        captured["profile"] = profile
        return Calculator()

    monkeypatch.setattr(execution_module, "SPRKKR", make_calculator)
    monkeypatch.setattr(execution_module, "load_result", lambda *_args, **_kwargs: object())

    SprkkrExecution(
        _request(tmp_path),
        _Cancellation(),
        profile_name="cluster",
    ).run()

    assert captured == {"profile": "cluster"}


def test_execution_retains_request_structure_without_copying(monkeypatch, tmp_path):
    request = _request(tmp_path)

    def forbid_copy():
        raise AssertionError("Execution must use the authoritative structure as is")

    monkeypatch.setattr(request.atoms, "copy", forbid_copy)
    execution = SprkkrExecution(request, _Cancellation())
    assert execution.request is request
    captured = []

    class Calculator:
        def create_run(self, **kwargs):
            captured.append(kwargs["atoms"])
            return object()

        def save_input(self, **kwargs):
            captured.append(kwargs["atoms"])
            return object()

    calculator = Calculator()
    monkeypatch.setattr(execution, "_calculator", lambda: calculator)
    execution._create_runner(calculator)
    execution.prepare_files()
    assert all(atoms is request.atoms for atoms in captured)


def test_execution_requests_persistent_backend_run(monkeypatch, tmp_path):
    captured = {}

    class Runner:
        id = "run-1"

        def submit(self):
            pass

        def wait(self):
            return object()

    class Calculator:
        def create_run(self, **kwargs):
            captured.update(kwargs)
            return Runner()

    execution = SprkkrExecution(_request(tmp_path, clear_cache=True), _Cancellation())
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())
    monkeypatch.setattr(execution_module, "load_result", lambda *_args, **_kwargs: object())

    execution.run()

    assert captured["project"] is None
    assert captured["print_output"] is False
    assert captured["empty_spheres"] is False
    assert captured["clear_cache"] is True


def test_execution_binds_persistent_manager_to_launch_project(monkeypatch, tmp_path):
    captured = {}
    bound_manager = object()
    project_ids = []

    class Manager:
        def for_project(self, project_id):
            project_ids.append(project_id)
            return bound_manager

    manager = Manager()

    class Runner:
        id = "run-1"
        record = SimpleNamespace(
            execution=SimpleNamespace(
                transport=SimpleNamespace(is_local=True, host=None)
            )
        )

        def submit(self):
            pass

        def wait(self):
            return object()

    class Calculator:
        def create_run(self, **kwargs):
            captured.update(kwargs)
            return Runner()

    execution = SprkkrExecution(
        _request(tmp_path),
        _Cancellation(),
        execution_manager=manager,
        project_id="project-17",
    )
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())
    monkeypatch.setattr(execution_module, "load_result", lambda *_args, **_kwargs: object())

    execution.run()

    assert captured["project"] is bound_manager
    assert project_ids == ["project-17"]


def test_cancel_before_registration_never_submits(monkeypatch, tmp_path):
    cancellation = _Cancellation(requested=True)
    events = []

    class Runner:
        def cancel(self):
            events.append("cancel")

        def submit(self):  # pragma: no cover - contract forbids this
            events.append("submit")

    class Calculator:
        def create_run(self, **_kwargs):
            return Runner()

    execution = SprkkrExecution(_request(tmp_path), cancellation)
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())

    try:
        execution.run()
    except ExecutionCancelled:
        pass
    else:  # pragma: no cover - assertion helper
        raise AssertionError("cancelled execution must not continue")

    assert events == ["cancel"]


def test_cancel_racing_with_submit_is_not_cancelled_twice(monkeypatch, tmp_path):
    cancellation = _Cancellation()
    events = []

    class Runner:
        def submit(self):
            events.append("submit")
            cancellation.requested = True
            self.cancel()

        def cancel(self):
            events.append("cancel")

        def wait(self):  # pragma: no cover - cancellation stops before wait
            events.append("wait")

    class Calculator:
        def create_run(self, **_kwargs):
            return Runner()

    execution = SprkkrExecution(_request(tmp_path), cancellation)
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())

    try:
        execution.run()
    except ExecutionCancelled:
        pass
    else:  # pragma: no cover - assertion helper
        raise AssertionError("cancelled execution must not continue")

    assert events == ["submit", "cancel"]


def test_persistent_reference_is_forwarded_to_result_loader(monkeypatch, tmp_path):
    backend_result = object()
    loaded_result = object()
    calls = []

    class Runner:
        id = "persistent-run-42"

        def submit(self):
            pass

        def wait(self):
            return backend_result

    class Calculator:
        def create_run(self, **_kwargs):
            return Runner()

    def load_result(result, *, fallback_directory, persistent_ref):
        calls.append((result, fallback_directory, persistent_ref))
        return loaded_result

    execution = SprkkrExecution(_request(tmp_path), _Cancellation())
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())
    monkeypatch.setattr(execution_module, "load_result", load_result)

    assert execution.run() is loaded_result
    assert calls == [(backend_result, str(tmp_path), None)]


def test_execution_notifies_persistent_job_id_after_submit_and_completion(
    monkeypatch, tmp_path
):
    changes = []

    class Runner:
        id = "persistent-run-17"

        def submit(self):
            pass

        def wait(self):
            return object()

    class Calculator:
        def create_run(self, **_kwargs):
            return Runner()

    execution = SprkkrExecution(
        _request(tmp_path),
        _Cancellation(),
        persistent_job_changed=changes.append,
    )
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())
    monkeypatch.setattr(execution_module, "load_result", lambda *_args, **_kwargs: object())

    execution.run()

    assert changes == []


def test_persistent_job_observer_failure_does_not_fail_calculation(
    monkeypatch, tmp_path
):
    class Runner:
        id = "persistent-run-17"

        def submit(self):
            pass

        def wait(self):
            return object()

    class Calculator:
        def create_run(self, **_kwargs):
            return Runner()

    def broken_observer(_job_id):
        raise RuntimeError("observer failure")

    execution = SprkkrExecution(
        _request(tmp_path),
        _Cancellation(),
        persistent_job_changed=broken_observer,
    )
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())
    monkeypatch.setattr(execution_module, "load_result", lambda *_args, **_kwargs: object())

    assert execution.run() is not None


def test_embedded_execution_forwards_original_host_to_result_reader(
    monkeypatch, tmp_path
):
    from ase2sprkkr import SPRKKRAtoms

    host = Atoms("Cu", positions=[[0, 0, 0]], cell=[2, 2, 2], pbc=True)
    cluster = SPRKKRAtoms.from_embedding(
        host, [(0, (0, 0, 0), "Fe")]
    )
    request = CalculationRequest(
        atoms=cluster, input_parameters=object(), directory=str(tmp_path),
        structure_id=1, state_id=1,
    )
    calls = []

    class Runner:
        id = "cluster-run"

        def submit(self):
            pass

        def wait(self):
            return object()

    class Calculator:
        def create_run(self, **kwargs):
            assert kwargs["atoms"].embedding_host is host
            assert kwargs["empty_spheres"] is False
            return Runner()

    execution = SprkkrExecution(request, _Cancellation())
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())

    def read_result(_result, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(execution_module, "load_result", read_result)
    execution.run()
    assert calls[0]["embedding_host"] is host
    assert calls[0]["fallback_directory"] == str(tmp_path)


def test_prepare_only_uses_save_input_without_creating_a_runner(monkeypatch, tmp_path):
    captured = {}

    class Calculator:
        def save_input(self, **kwargs):
            captured.update(kwargs)
            return ("parameters", "input.inp", "host.pot", None)

        def create_run(self, **kwargs):
            raise AssertionError("Preparing files must not launch a runner")

    execution = SprkkrExecution(_request(tmp_path, restart_scf=True), _Cancellation())
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())
    assert execution.prepare_files() == ("parameters", "input.inp", "host.pot", None)
    assert captured["directory"] == str(tmp_path)
    assert captured["empty_spheres"] is False
    assert captured["restart_scf"] is True
    assert captured["output_file"] is False
    assert captured["input_file"] == "%a_%T.inp"
    assert captured["potential_file"] == "%a.pot"


def test_unassigned_clear_cache_uses_shared_project_run_store(tmp_path):
    from kkrunner.projects import ProjectRepository, ProjectRunManager
    from kkrunner.runs.manager import RunManager
    from kkrunner.runs.records import RunStore

    store = RunStore(tmp_path / "runs")
    projects = ProjectRepository(tmp_path / "projects")
    manager = ProjectRunManager(store=store, project_repository=projects)
    execution = SprkkrExecution(
        _request(tmp_path, clear_cache=True),
        _Cancellation(),
        execution_manager=manager,
    )
    reserved_manager = execution._project_context()
    assert type(reserved_manager) is RunManager
    assert reserved_manager.store is store

    normal = SprkkrExecution(_request(tmp_path), _Cancellation(), execution_manager=manager)
    assert normal._project_context() is None


@pytest.mark.parametrize("failure_point", ["none", "submit", "wait"])
def test_submission_acknowledgement_precedes_completion_and_ignores_rejected_launches(
    monkeypatch, tmp_path, failure_point,
):
    """A confirmed backend submission saves inputs regardless of later outcome."""
    events = []

    class Runner:
        def submit(self):
            events.append("submit")
            if failure_point == "submit":
                raise RuntimeError("submission rejected")

        def wait(self):
            events.append("wait")
            if failure_point == "wait":
                raise RuntimeError("calculation failed")
            return object()

    class Calculator:
        def create_run(self, **_kwargs):
            return Runner()

    execution = SprkkrExecution(
        _request(tmp_path), _Cancellation(),
        submitted=lambda: events.append("acknowledged"),
    )
    monkeypatch.setattr(execution, "_calculator", lambda: Calculator())
    monkeypatch.setattr(
        execution_module, "load_result",
        lambda *_args, **_kwargs: events.append("loaded") or object(),
    )
    if failure_point == "none":
        execution.run()
        assert events == ["submit", "acknowledged", "wait", "loaded"]
    else:
        with pytest.raises(RuntimeError):
            execution.run()
        assert events == (
            ["submit"] if failure_point == "submit"
            else ["submit", "acknowledged", "wait"]
        )


def test_run_monitor_forwards_worker_submit_signal_on_gui_thread(tmp_path):
    from PyQt6.QtWidgets import QApplication
    from ase2sprkkr_gui.gui.dialogs.run_calculation import SprkkrRunWindow

    app = QApplication.instance() or QApplication([])
    observed = []
    window = SprkkrRunWindow(
        _request(tmp_path),
        on_submitted=lambda: observed.append("submitted"),
    )
    worker = window._worker_factory(window._cancellation)
    worker.submitted.emit()
    app.processEvents()
    assert observed == ["submitted"]
    window.close()
