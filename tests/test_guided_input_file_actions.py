"""Guided input file actions use isolated sessions, not workspace documents."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ase2sprkkr_gui-test-matplotlib")

from pathlib import Path

from ase2sprkkr import InputParameters
from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.dialogs import guided_input as gui_input
from ase2sprkkr_gui.gui.dialogs.guided_input import GuidedInputParametersDialog
from ase2sprkkr_gui.gui.flows import input_parameters as input_flows


def _dialog(**kwargs):
    app = QApplication.instance() or QApplication([])
    return app, GuidedInputParametersDialog(
        "scf", InputParameters.create("scf"), **kwargs
    )


def test_undo_redo_are_in_top_right_header_not_footer():
    _app, dialog = _dialog()
    layout = dialog.layout()
    top = layout.itemAt(0).layout()
    footer = layout.itemAt(layout.count() - 1).layout()
    assert top.indexOf(dialog.undo_button) >= 0
    assert top.indexOf(dialog.redo_button) >= 0
    assert top.indexOf(dialog.undo_button) < top.indexOf(dialog.redo_button)
    assert footer.indexOf(dialog.undo_button) == -1
    assert footer.indexOf(dialog.redo_button) == -1
    assert dialog.save_input_button.text() == "Save Input…"
    dialog.close()


def test_save_commits_current_draft_without_accepting_or_mutating_source(monkeypatch):
    _app, dialog = _dialog()
    source = dialog.session.working_parameters
    original = source.SCF.NITER()
    saved = []
    monkeypatch.setattr(
        gui_input, "choose_and_save_input_parameters",
        lambda params, parent: saved.append((params, parent)) or True,
    )
    assert dialog.session.set_value(("SCF", "NITER"), 314)
    dialog.save_input_button.click()
    assert len(saved) == 1
    params, parent = saved[0]
    assert parent is dialog
    assert params is not dialog.session.working_parameters
    assert params.SCF.NITER() == 314
    assert dialog.result() == 0
    assert source.SCF.NITER() == original
    dialog.close()


def test_save_input_uses_native_serializer_and_cancel_is_noop(monkeypatch, tmp_path):
    parameters = InputParameters.create("scf")
    path = tmp_path / "test.inp"
    writes = []
    monkeypatch.setattr(
        input_flows.QFileDialog, "getSaveFileName",
        lambda *_args, **_kwargs: (str(path), ""),
    )
    monkeypatch.setattr(
        type(parameters), "to_file",
        lambda self, name: writes.append((self, Path(name))),
    )
    assert input_flows.choose_and_save_input_parameters(parameters, None)
    assert writes == [(parameters, path)]
    monkeypatch.setattr(
        input_flows.QFileDialog, "getSaveFileName",
        lambda *_args, **_kwargs: ("", ""),
    )
    assert not input_flows.choose_and_save_input_parameters(parameters, None)
    assert writes == [(parameters, path)]


def test_calculate_disabled_with_reason_and_restores_after_session_edit(monkeypatch):
    # Isolate contextual availability from native validity of an empty model.
    monkeypatch.setattr(gui_input, "validate_setup", lambda _params: None)
    _app, dialog = _dialog(
        calculation_unavailable_reason=lambda p: (
            "This input needs an existing converged potential"
            if p.SCF.NITER() != 314 else None
        ),
    )
    assert not dialog.calculate_button.isEnabled()
    assert "converged potential" in dialog.calculate_button.toolTip()
    assert dialog.save_input_button.isEnabled()
    assert dialog.session.set_value(("SCF", "NITER"), 314)
    assert dialog.calculate_button.isEnabled()
    assert "Run a calculation" in dialog.calculate_button.toolTip()
    dialog.close()


def test_calculate_invalid_input_is_disabled_without_disabling_save(monkeypatch):
    from ase2sprkkr_gui.gui.input_parameters.validation import InputParametersValidationError

    def fail(_parameters):
        raise InputParametersValidationError("Energy window must be corrected")

    monkeypatch.setattr(gui_input, "validate_setup", fail)
    _app, dialog = _dialog()
    assert not dialog.calculate_button.isEnabled()
    assert "Energy window" in dialog.calculate_button.toolTip()
    assert dialog.save_input_button.isEnabled()
    dialog.close()
