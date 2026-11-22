from __future__ import annotations

from types import SimpleNamespace

from ase import Atoms
from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import QApplication, QWidget
from kkrunner.projects import ProjectRepository

from ase2sprkkr_gui.gui.application.recent_files import RecentFiles
from ase2sprkkr_gui.gui.application.result_loading import LoadedResult
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.flows import files as file_flows
from ase2sprkkr_gui.gui.misc.modal_task import _ModalTaskDialog
from ase2sprkkr_gui.gui.workflow.model import RunTask
from ase2sprkkr_gui.main import GuiApplication


def _loaded_scf(path, *, atoms=None) -> LoadedResult:
    return LoadedResult(
        result=SimpleNamespace(
            task_name="scf",
            produces_potential=True,
            output_values={},
        ),
        output_path=path,
        potential_path=None,
        atoms=(
            atoms
            if atoms is not None
            else Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
        ),
        potential_error=None,
        directory=str(path.parent),
        input_parameters=None,
    )


def _commands(window):
    return tuple(
        action.command
        for group in window.workflow_plan.groups
        for action in group.actions
    )


def test_continue_output_activates_state_and_reuses_same_result(tmp_path, monkeypatch):
    output = tmp_path / "SCF.out"
    output.write_text("first calculation", encoding="utf-8")
    loaded = _loaded_scf(output)
    monkeypatch.setattr(file_flows, "load_result_file", lambda _path, **_kwargs: loaded)

    def unexpected_warning(_parent, title, message):
        raise AssertionError(f"Unexpected {title} dialog: {message}")

    monkeypatch.setattr(file_flows.QMessageBox, "warning", unexpected_warning)
    controller = WorkspaceController()
    recent = RecentFiles(tmp_path / "recent.json")

    assert file_flows.continue_output(controller, recent, output, None)
    assert len(controller.result_store.results) == 1
    record = controller.result_store.results[0]
    assert controller.state_id == record.output_state_id
    assert controller.atoms is not None
    assert not controller.has_unsaved_structure
    assert controller.directory == str(tmp_path)

    # The second import uses a lightweight cached result without atoms:
    # its structure is already recorded, not missing or unreadable.
    cached = file_flows._cached_output(controller, output)
    assert cached is not None
    assert cached.result is record.result
    assert cached.atoms is None
    assert cached.cached_record_id == record.id

    structure_generation = controller.structure_generation
    directory_generation = controller.directory_generation
    assert file_flows.continue_output(controller, recent, output, None)
    assert len(controller.result_store.results) == 1
    assert controller.state_id == record.output_state_id
    assert controller.structure_generation == structure_generation
    assert controller.directory_generation == directory_generation
    assert not controller.has_unsaved_structure


def test_restart_after_continue_resets_effective_scf_status(tmp_path, monkeypatch):
    output = tmp_path / "SCF.out"
    output.write_text("calculation", encoding="utf-8")
    loaded = _loaded_scf(output)
    monkeypatch.setattr(file_flows, "load_result_file", lambda _path, **_kwargs: loaded)

    controller = WorkspaceController()
    recent = RecentFiles(tmp_path / "recent.json")
    assert file_flows.continue_output(controller, recent, output, None)

    controller.use_for_new_calculation(
        expected_structure_generation=controller.structure_generation
    )

    assert controller.restarted
    assert controller.scf_status() == "START"


def test_workflow_keeps_semantic_scf_action_after_continue_and_restart(
    tmp_path, monkeypatch
):
    application = QApplication.instance() or QApplication([])
    # This test checks workflow planning, not the QThread/Working lifecycle.
    # Keep the actual publish logic and GUI signals, but run its synthetic
    # file reader inline. The real modal path is covered by the last test.
    def run_inline(_parent, operation, *, on_success=None, **_kwargs):
        value = operation()
        return on_success(value) if on_success is not None else value

    monkeypatch.setattr(file_flows, "run_modal_task", run_inline)

    def unexpected_dialog(_parent, title, message, *_args, **_kwargs):
        raise AssertionError(f"Unexpected {title} dialog: {message}")

    monkeypatch.setattr(file_flows.QMessageBox, "critical", unexpected_dialog)
    monkeypatch.setattr(file_flows.QMessageBox, "warning", unexpected_dialog)
    output = tmp_path / "SCF.out"
    output.write_text("calculation", encoding="utf-8")
    loaded = _loaded_scf(output)
    monkeypatch.setattr(file_flows, "load_result_file", lambda _path, **_kwargs: loaded)

    gui = GuiApplication(
        recent_files_path=tmp_path / "recent.json",
        settings_path=tmp_path / "settings.json",
        # Do not load or recover actual jobs from the user's global database.
        project_repository=ProjectRepository(tmp_path / "projects"),
    )
    window = gui.create_workflow_window()
    scf_command = RunTask("scf")

    window._continue_recent("output", str(output))
    application.processEvents()
    assert scf_command in _commands(window)

    gui.controller.use_for_new_calculation(
        expected_structure_generation=gui.controller.structure_generation,
    )
    application.processEvents()
    assert scf_command in _commands(window)
    window.close()



def test_new_unusable_parse_does_not_inherit_cached_structure(tmp_path):
    """Even the same TaskResult object is not a cache hit without its marker."""
    output = tmp_path / "SCF.out"
    output.write_text("unchanged output", encoding="utf-8")
    controller = WorkspaceController()

    first = controller.adopt_external_result(_loaded_scf(output)).record
    assert first is not None
    assert first.output_state_id is not None

    missing_structure = LoadedResult(
        result=first.result,
        output_path=output,
        potential_path=None,
        atoms=None,
        potential_error=ValueError("no structure"),
        directory=str(tmp_path),
        input_parameters=None,
    )
    second = controller.adopt_external_result(missing_structure).record

    assert second is not None
    assert second.id != first.id
    assert controller.result_store.state_for_result(second) is None
    assert controller.result_store.state_for_result(first) is not None


def test_overwritten_output_path_creates_new_result(tmp_path):
    output = tmp_path / "SCF.out"
    output.write_text("first", encoding="utf-8")
    controller = WorkspaceController()

    first = controller.adopt_external_result(_loaded_scf(output)).record
    assert first is not None

    output.write_text("a different second calculation", encoding="utf-8")
    second = controller.adopt_external_result(_loaded_scf(output)).record

    assert second is not None
    assert second.id != first.id
    assert len(controller.result_store.results) == 2


def test_continue_output_reports_potential_error_once(tmp_path, monkeypatch):
    output = tmp_path / "SCF.out"
    output.write_text("calculation", encoding="utf-8")
    broken_potential = tmp_path / "SCF.pot"
    loaded = LoadedResult(
        result=SimpleNamespace(
            task_name="scf",
            produces_potential=True,
            output_values={},
        ),
        output_path=output,
        potential_path=broken_potential,
        atoms=None,
        potential_error=ValueError("invalid POT section"),
        directory=str(tmp_path),
        input_parameters=None,
    )
    monkeypatch.setattr(file_flows, "load_result_file", lambda _path, **_kwargs: loaded)
    warnings = []
    monkeypatch.setattr(
        file_flows.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    controller = WorkspaceController()
    recent = RecentFiles(tmp_path / "recent.json")
    assert not file_flows.continue_output(controller, recent, output, None)

    assert len(warnings) == 1
    title, message = warnings[0]
    assert title == "Cannot Continue Calculation"
    assert "invalid POT section" in message
    assert "no reusable calculation state" in message
    assert message.index("invalid POT section") < message.index("no reusable calculation state")
    assert len(controller.result_store.results) == 1
    assert controller.state_id is None


def test_continue_output_without_reconstructable_structure_reports_one_warning(
    tmp_path, monkeypatch
):
    output = tmp_path / "SCF.out"
    loaded = LoadedResult(
        result=SimpleNamespace(
            task_name="scf",
            produces_potential=True,
            output_values={},
        ),
        output_path=output,
        potential_path=None,
        atoms=None,
        potential_error=None,
        directory=str(tmp_path),
        input_parameters=None,
    )
    monkeypatch.setattr(file_flows, "load_result_file", lambda _path, **_kwargs: loaded)
    warnings = []
    monkeypatch.setattr(
        file_flows.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    controller = WorkspaceController()
    recent = RecentFiles(tmp_path / "recent.json")
    assert not file_flows.continue_output(controller, recent, output, None)
    assert len(warnings) == 1
    assert "no reusable calculation state" in warnings[0][1]
    assert "potential could not be loaded" not in warnings[0][1]


def test_continue_finishes_state_and_recent_history_under_working(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    # An unexpected Qt error dialog is otherwise modal and stalls headless
    # pytest forever. Report the error as a normal assertion instead.
    def unexpected_dialog(_parent, title, message, *_args, **_kwargs):
        raise AssertionError(f"Unexpected {title} dialog: {message}")

    monkeypatch.setattr(file_flows.QMessageBox, "critical", unexpected_dialog)
    monkeypatch.setattr(file_flows.QMessageBox, "warning", unexpected_dialog)
    output = tmp_path / "SCF.out"
    output.touch()
    loaded = _loaded_scf(output)
    controller = WorkspaceController()
    recent = RecentFiles(tmp_path / "recent.json")
    events = []

    def read(path, **_kwargs):
        assert path == output
        assert QThread.currentThread() is not app.thread()
        events.append("read")
        return loaded

    def check(label):
        assert QThread.currentThread() is app.thread()
        dialog = parent.findChild(_ModalTaskDialog)
        assert dialog is not None and dialog.isVisible()
        events.append(label)

    monkeypatch.setattr(file_flows, "load_result_file", read)
    controller.resultChanged.connect(lambda *_args: check("result"))
    controller.structureChanged.connect(lambda *_args: check("structure"))
    recent.changed.connect(lambda: check("history"))

    # If Working fails to complete, terminate the test's dialog rather than
    # hanging indefinitely. The worker is a bounded synthetic read.
    timed_out = []
    watchdog = QTimer(parent)
    watchdog.setSingleShot(True)

    def abort_stalled_dialog():
        timed_out.append(True)
        for dialog in parent.findChildren(_ModalTaskDialog):
            if dialog.isVisible():
                dialog.finish_with_error()

    watchdog.timeout.connect(abort_stalled_dialog)
    watchdog.start(10000)
    try:
        assert file_flows.continue_output(controller, recent, output, parent)
    finally:
        watchdog.stop()
    assert not timed_out, "Working dialog did not finish within 10 seconds"
    assert events[0] == "read"
    assert "result" in events and "structure" in events and "history" in events
    assert controller.atoms is not None
    assert not controller.has_unsaved_structure
    parent.close()
