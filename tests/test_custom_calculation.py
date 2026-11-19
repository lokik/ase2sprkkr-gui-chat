"""Standalone input and native-task chooser regressions."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ase2sprkkr_gui-test-matplotlib")

from ase2sprkkr import InputParameters
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from ase2sprkkr_gui.gui.application.recent_files import RecentFiles
from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.dialogs import expert_input
from ase2sprkkr_gui.gui.dialogs import task_chooser
from ase2sprkkr_gui.gui.dialogs.task_chooser import (
    TaskChoice, TaskChooserDialog, applicability, order_choices,
)
from ase2sprkkr_gui.gui.flows import custom_calculation as custom_flow
from ase2sprkkr_gui.main import GuiApplication


def _app():
    return QApplication.instance() or QApplication([])


def _rows():
    return (
        TaskChoice("A", False, "Yes", "Ordinary"),
        TaskChoice("Z", True, "Yes", "Experimental"),
        TaskChoice("B", True, "No", "Unsupported", "Task not supported"),
        TaskChoice("C", False, "Unknown", "Unverified", "Support unverified"),
    )


def _names(table):
    return [
        table.item(row, 0).text() for row in range(table.rowCount())
    ]


def test_default_sort_then_column_primary_keeps_other_tie_breakers():
    assert [row.name for row in order_choices(_rows())] == ["Z", "A", "B", "C"]
    assert [row.name for row in order_choices(_rows(), primary="name")] == [
        "A", "B", "C", "Z"
    ]
    assert [row.name for row in order_choices(
        _rows(), primary="name", descending=True
    )] == ["Z", "C", "B", "A"]
    assert [row.name for row in order_choices(
        _rows(), has_structure=False
    )] == ["B", "Z", "A", "C"]


def test_applicability_is_not_conflated_with_execution_readiness():
    task = SimpleNamespace(
        name="TEST", executable="kkrgen", supports_embedded_cluster=False,
    )
    assert applicability(task, "0d")[0] == "No"
    assert applicability(task, "2d")[0] == "Unknown"
    task.supports_2d = True
    assert applicability(task, "2d")[0] == "Yes"
    task.supports_2d = False
    assert applicability(task, "2d")[0] == "No"
    assert applicability(task, "3d")[0] == "Yes"


def test_task_chooser_filters_sorts_and_keeps_unsupported_selectable(monkeypatch):
    _app()
    monkeypatch.setattr(task_chooser, "task_choices", lambda _atoms: _rows())
    chooser = TaskChooserDialog(object())
    assert [chooser.table.horizontalHeaderItem(i).text()
            for i in range(chooser.table.columnCount())] == [
        "Name", "Experimental", "Applicable to structure", "Info"
    ]
    assert _names(chooser.table) == ["Z", "A", "B", "C"]

    chooser._sort_by_column(0)
    assert _names(chooser.table) == ["A", "B", "C", "Z"]
    chooser.search.setText("unsupported")
    assert _names(chooser.table) == ["B"]
    chooser.search.clear()
    chooser.applicability_filter.setCurrentText("No")
    assert _names(chooser.table) == ["B"]

    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: (
        messages.append(True) or QMessageBox.StandardButton.Yes
    ))
    chooser._accept_selection()
    assert chooser.result() == QDialog.DialogCode.Accepted
    assert chooser.selected_task == "B"
    assert messages


def test_task_chooser_without_structure_has_no_applicability_column(monkeypatch):
    _app()
    monkeypatch.setattr(task_chooser, "task_choices", lambda _atoms: _rows())
    chooser = TaskChooserDialog(None)
    assert [chooser.table.horizontalHeaderItem(i).text()
            for i in range(chooser.table.columnCount())] == [
        "Name", "Experimental", "Info"
    ]
    assert chooser.applicability_filter is None
    assert _names(chooser.table) == ["B", "Z", "A", "C"]
    chooser.reject()


def test_custom_input_is_available_without_structure_and_does_not_publish_ipd(
    tmp_path, monkeypatch,
):
    _app()
    controller = WorkspaceController(WorkspaceState())
    history = RecentFiles(tmp_path / "recent.json")
    edited = []
    monkeypatch.setattr(custom_flow, "TaskChooserDialog", lambda *_args: SimpleNamespace(
        exec=lambda: QDialog.DialogCode.Accepted, selected_task="SCF",
    ))
    monkeypatch.setattr(
        custom_flow, "edit_custom_parameters",
        lambda ctrl, files, parent, parameters, **_kw: (
            edited.append((ctrl, files, parameters.task_name)) or True
        ),
    )
    assert custom_flow.choose_custom_task(
        controller, history, None, dataset_default_mode="empty"
    )
    assert edited == [(controller, history, "SCF")]
    assert controller.input_parameters is None
    assert controller.atoms is None
    assert "Load or create a structure" in custom_flow.calculation_unavailable_reason(
        controller, InputParameters.for_task("SCF"),
    )


def test_cli_input_uses_standalone_editor_and_not_workspace_document(tmp_path, monkeypatch):
    _app()
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json")
    path = tmp_path / "input.inp"
    path.write_text("not actually parsed", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        custom_flow, "load_custom_input",
        lambda controller, history, parent, input_path, **kwargs: (
            calls.append((controller, history, parent, input_path)) or True
        ),
    )
    assert gui.open_startup_selection("input", str(path))
    assert calls == [(gui.controller, gui.recent_files, gui.create_workflow_window(), path)]
    assert gui.controller.input_parameters is None
    gui.create_workflow_window().close()


def test_expert_input_supports_independent_save_and_calculation_reason(monkeypatch):
    _app()
    dialog = expert_input.InputParametersDialog(
        InputParameters.for_task("SCF"),
        calculation_mode=True,
        calculation_unavailable_reason=lambda _params: "No structure loaded",
    )
    assert dialog.ok_btn.text() == "Calculate"
    assert not dialog.ok_btn.isEnabled()
    assert "No structure loaded" in dialog.ok_btn.toolTip()
    assert dialog.save_btn.isEnabled()
    saved = []
    monkeypatch.setattr(dialog.tree_editor, "commit_pending", lambda: True)
    monkeypatch.setattr(
        expert_input, "choose_and_save_input_parameters",
        lambda params, parent: saved.append((params, parent)) or True,
    )
    dialog.save_btn.click()
    assert len(saved) == 1
    assert saved[0][1] is dialog
    assert saved[0][0] is not dialog.session.working_parameters
    assert dialog.result() == 0
    dialog.close()
