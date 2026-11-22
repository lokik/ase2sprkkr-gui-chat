"""Command-line startup reuses the existing workspace file workflows."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from ase2sprkkr_gui.gui.flows import files as file_flows
from ase2sprkkr_gui.gui.flows import custom_calculation as custom_flow
from ase2sprkkr_gui.main import GuiApplication


@pytest.mark.parametrize(
    ("filename", "explicit_kind", "expected"),
    [
        ("Fe.cif", "auto", "structure"),
        ("Fe.pot", "auto", "structure"),
        ("result.out", "auto", "output"),
        ("settings.inp", "auto", "input"),
        ("ambiguous.txt", "output", "output"),
        ("ambiguous.txt", "structure", "structure"),
        ("outputs", "auto", "directory"),
    ],
)
def test_startup_target_uses_existing_file_flows(
    tmp_path, monkeypatch, filename, explicit_kind, expected
):
    application = QApplication.instance() or QApplication([])
    path = tmp_path / filename
    if filename == "outputs":
        path.mkdir()
    else:
        path.touch()
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json")
    parent = QWidget()
    monkeypatch.setattr(gui, "create_workflow_window", lambda: parent)
    called = []

    def loader(kind):
        def load(_controller, _recent, target, _parent):
            called.append((kind, target))
            return (target,) if kind == "directory" else True
        return load

    for kind, method in (
        ("structure", "load_structure"),
        ("output", "load_output"),
        ("directory", "load_output_directory"),
    ):
        monkeypatch.setattr(file_flows, method, loader(kind))

    def load_custom_input(_controller, _recent, _parent, target, **kwargs):
        assert kwargs["project_repository"] is gui.projects
        assert kwargs["track_launch_monitor"] == gui._track_launch_monitor
        called.append(("input", target))
        return True

    # CLI --input now opens the custom-input flow, which launches a modal
    # editor. Mock that boundary rather than the obsolete file-flow loader.
    monkeypatch.setattr(custom_flow, "load_custom_input", load_custom_input)

    assert gui.open_startup_selection(explicit_kind, str(path))
    assert called == [(expected, path.resolve())]
    parent.close()


def test_open_last_selects_latest_existing_structure_not_latest_output(
    tmp_path, monkeypatch
):
    application = QApplication.instance() or QApplication([])
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json")
    parent = QWidget()
    monkeypatch.setattr(gui, "create_workflow_window", lambda: parent)
    present = tmp_path / "Fe.cif"
    present.touch()
    missing = tmp_path / "deleted.cif"
    output = tmp_path / "result.out"
    output.touch()
    gui.recent_files.remember("structure", present)
    gui.recent_files.remember("structure", missing)
    gui.recent_files.remember("output", output)
    called = []
    monkeypatch.setattr(
        file_flows, "load_structure",
        lambda _controller, _recent, target, _parent: called.append(target) or True,
    )

    assert gui.open_startup_selection("last", None)
    assert called == [present.resolve()]
    assert gui.recent_files.paths("structure") == (str(present.resolve()),)
    assert gui.recent_files.paths("output") == (str(output.resolve()),)
    parent.close()


def test_open_last_with_no_existing_structure_warns(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json")
    parent = QWidget()
    monkeypatch.setattr(gui, "create_workflow_window", lambda: parent)
    warned = []
    monkeypatch.setattr(
        "ase2sprkkr_gui.main.QMessageBox.warning",
        lambda _parent, title, message: warned.append((title, message)),
    )
    gui.recent_files.remember("structure", tmp_path / "gone.cif")

    assert not gui.open_startup_selection("last", None)
    assert warned[0][0] == "No Recent Structure"
    parent.close()


def test_missing_explicit_startup_file_warns(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json")
    parent = QWidget()
    monkeypatch.setattr(gui, "create_workflow_window", lambda: parent)
    warned = []
    monkeypatch.setattr(
        "ase2sprkkr_gui.main.QMessageBox.warning",
        lambda _parent, title, message: warned.append((title, message)),
    )

    assert not gui.open_startup_selection("auto", str(tmp_path / "gone.cif"))
    assert warned[0][0] == "Cannot Open File"
    parent.close()


def test_job_startup_routes_id_to_persistent_job_controller(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json")
    parent = QWidget()
    monkeypatch.setattr(gui, "create_workflow_window", lambda: parent)
    called = []
    monkeypatch.setattr(
        gui.persistent_jobs_controller, "open_job",
        lambda job_id, widget: called.append((job_id, widget)) or True,
    )
    assert gui.open_startup_selection("job", "run-17")
    assert called == [("run-17", parent)]
    parent.close()
