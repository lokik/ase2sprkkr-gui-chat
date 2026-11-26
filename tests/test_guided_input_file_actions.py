"""Guided input file actions use isolated sessions, not workspace documents."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ase2sprkkr_gui-test-matplotlib")

from ase import Atoms
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


def test_page_navigation_responds_to_mouse_and_keyboard():
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    app, dialog = _dialog()
    dialog.show()
    app.processEvents()
    rect = dialog.navigation.visualItemRect(dialog.navigation.item(1))
    QTest.mouseClick(dialog.navigation.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert dialog.navigation.currentRow() == 1
    assert dialog.pages.currentIndex() == 1

    dialog.navigation.setFocus()
    QTest.keyClick(dialog.navigation, Qt.Key.Key_Up)
    assert dialog.navigation.currentRow() == 0
    assert dialog.pages.currentIndex() == 0
    dialog.close()


def test_previous_and_next_navigate_pages():
    _app, dialog = _dialog()
    assert not dialog.previous_button.isEnabled()
    dialog.next_button.click()
    assert dialog.navigation.currentRow() == 1
    assert dialog.previous_button.isEnabled()
    dialog.previous_button.click()
    assert dialog.navigation.currentRow() == 0
    dialog.close()


def test_save_commits_current_draft_without_accepting_or_mutating_source(monkeypatch):
    _app, dialog = _dialog()
    source = dialog.session.working_parameters
    original = source.SCF.NITER()
    saved = []
    monkeypatch.setattr(
        gui_input, "choose_and_save_input_parameters",
        lambda params, parent, **_options: saved.append((params, parent)) or True,
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
    directory = tmp_path / "calculations" / "run"
    directory.mkdir(parents=True)
    parameters.CONTROL.POTFIL = str(directory / "input.pot")
    path = tmp_path / "test.inp"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        input_flows.QFileDialog, "getSaveFileName",
        lambda *_args, **_kwargs: (str(path), ""),
    )
    atoms = Atoms("Fe", cell=(3, 3, 3), pbc=(True, True, False))
    assert input_flows.choose_and_save_input_parameters(
        parameters, None, atoms=atoms, directory=directory,
    )
    saved = path.read_text()
    assert "POTFIL=input.pot" in saved
    assert "NKTAB2D" in saved
    assert "KKRMODE=TB" in saved
    monkeypatch.setattr(
        input_flows.QFileDialog, "getSaveFileName",
        lambda *_args, **_kwargs: ("", ""),
    )
    assert not input_flows.choose_and_save_input_parameters(parameters, None)
    assert path.read_text() == saved


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



def test_explicit_input_save_populates_recent_history(tmp_path, monkeypatch):
    from ase2sprkkr_gui.gui.application.recent_files import RecentFiles

    _app, dialog = _dialog()
    history = RecentFiles(tmp_path / "recent.json")
    dialog.recent_files = history
    target = tmp_path / "saved.inp"
    monkeypatch.setattr(
        input_flows.QFileDialog, "getSaveFileName",
        lambda *_args, **_kwargs: (str(target), ""),
    )
    dialog.save_input_button.click()
    assert target.is_file()
    assert history.paths("input") == (str(target.resolve()),)
    dialog.close()
