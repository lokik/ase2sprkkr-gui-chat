"""GUI dialogs select destinations; scientific file methods stay headless."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from ase2sprkkr_gui.gui.dialogs.object_view import execute_value_action
from ase2sprkkr_gui.gui.widgets.result_actions import (
    result_action_icon,
    result_action_tooltip,
)


class _Option:
    info = "Auxiliary output"

    def __init__(self, path):
        self._path = path
        self.calls = []

    def path(self):
        return str(self._path)

    def copy_to(self, destination):
        self.calls.append(("copy_to", destination))

    def export_nexus(self, destination):
        self.calls.append(("export_nexus", destination))

    def open(self):
        self.calls.append(("open", None))


def test_save_as_chooses_path_in_gui_then_calls_python_api(tmp_path, monkeypatch):
    _application = QApplication.instance() or QApplication([])
    widget = QWidget()
    value = _Option(tmp_path / "output.out")
    target = str(tmp_path / "saved.out")
    dialogs = []

    def choose(parent, title, proposed, file_filter):
        dialogs.append((parent, title, proposed, file_filter))
        return target, ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    execute_value_action(value, "copy_to", widget)

    assert value.calls == [("copy_to", target)]
    assert dialogs[0][0] is widget
    assert dialogs[0][1] == "Save Result File As"
    assert dialogs[0][2].endswith("output_copy.out")
    assert result_action_tooltip(value, "copy_to") == "Save as… — Auxiliary output"
    assert not result_action_icon(widget.style(), "copy_to").isNull()
    widget.close()


def test_export_nexus_uses_nxs_default_and_handles_cancel(tmp_path, monkeypatch):
    _application = QApplication.instance() or QApplication([])
    widget = QWidget()
    value = _Option(tmp_path / "spectrum.spc")
    selected = []

    def choose(parent, title, proposed, file_filter):
        selected.append((title, proposed, file_filter))
        return "", ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    execute_value_action(value, "export_nexus", widget)

    assert value.calls == []
    assert selected == [(
        "Export Spectrum to NeXus",
        str(tmp_path / "spectrum.nxs"),
        "NeXus files (*.nxs);;All files (*)",
    )]

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *args: (str(tmp_path / "chosen.nxs"), ""),
    )
    execute_value_action(value, "export_nexus", widget)
    assert value.calls == [("export_nexus", str(tmp_path / "chosen.nxs"))]
    widget.close()


def test_existing_no_argument_actions_and_error_reporting(tmp_path, monkeypatch):
    _application = QApplication.instance() or QApplication([])
    widget = QWidget()
    value = _Option(tmp_path / "output.out")
    execute_value_action(value, "open", widget)
    assert value.calls == [("open", None)]

    errors = []
    monkeypatch.setattr(
        QMessageBox, "critical", lambda parent, title, message: errors.append(message),
    )

    def fail(destination):
        raise RuntimeError("cannot copy")

    value.copy_to = fail
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *args: (str(tmp_path / "copy.out"), ""),
    )
    execute_value_action(value, "copy_to", widget)
    assert len(errors) == 1 and "cannot copy" in errors[0]
    widget.close()
