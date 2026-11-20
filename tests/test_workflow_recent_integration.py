from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.workflow.model import ContinueRecent, RepeatRecentInput, WorkflowSection
from ase2sprkkr_gui.main import GuiApplication


def test_recent_history_change_refreshes_guided_plan(tmp_path):
    application = QApplication.instance() or QApplication([])
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json")
    window = gui.create_workflow_window()

    load = window.workflow_plan.group(WorkflowSection.LOAD)
    assert load is not None
    assert not any(isinstance(action.command, ContinueRecent) for action in load.actions)

    structure = tmp_path / "Fe.cif"
    structure.touch()
    gui.recent_files.remember("structure", structure)
    application.processEvents()

    load = window.workflow_plan.group(WorkflowSection.LOAD)
    assert load is not None
    recent = next(
        action for action in load.actions if isinstance(action.command, ContinueRecent)
    )
    expected_path = str(structure.resolve())
    assert recent.command == ContinueRecent("structure", expected_path)
    assert recent.tooltip == expected_path
    assert tuple(group.title for group in recent.menu) == ("Structures", "Results")
    assert recent.menu[0].items[0].command == recent.command
    assert recent.menu[1].items == ()

    window.close()

def test_recent_input_is_opened_with_standalone_editor(tmp_path, monkeypatch):
    from ase2sprkkr_gui.gui.flows import custom_calculation
    _app = QApplication.instance() or QApplication([])
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json")
    window = gui.create_workflow_window()
    path = tmp_path / "original.inp"
    path.touch()
    gui.recent_files.remember("input", path)
    _app.processEvents()

    load = window.workflow_plan.group(WorkflowSection.LOAD)
    assert load is not None
    assert not any(isinstance(action.command, ContinueRecent) for action in load.actions)
    custom = window.workflow_plan.group(WorkflowSection.CUSTOM)
    assert custom is not None
    recent = next(action for action in custom.actions if isinstance(action.command, RepeatRecentInput))
    assert recent.command == RepeatRecentInput(str(path.resolve()))
    opened = []
    monkeypatch.setattr(
        custom_calculation, "load_custom_input",
        lambda controller, history, parent, file_path, **kwargs:
            opened.append((controller, history, parent, file_path)) or True,
    )
    window.execute_workflow_command(recent.command)
    assert opened == [
        (gui.controller, gui.recent_files, window, str(path.resolve()))
    ]
    assert gui.controller.input_parameters is None
    window.close()
