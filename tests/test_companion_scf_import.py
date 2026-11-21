"""Related SCF imports are opportunistic and never replace the chosen result."""
from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QWidget

from ase2sprkkr_gui.gui.misc.modal_task import _ModalTaskDialog

from ase2sprkkr_gui.gui.application.recent_files import RecentFiles
from ase2sprkkr_gui.gui.application.result_loading import (
    LoadedResult,
    load_companion_scf_result,
)
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.application import result_loading
from ase2sprkkr_gui.gui.flows import files as file_flows


def _loaded(path, *, task="dos", potential=None):
    return LoadedResult(
        result=SimpleNamespace(
            task_name=task, produces_potential=task == "scf", output_values={}
        ),
        output_path=path,
        potential_path=potential,
        atoms=None,
        potential_error=None,
        directory=str(path.parent),
    )


def test_companion_scf_requires_same_produced_potential(tmp_path, monkeypatch):
    dos = tmp_path / "Fe_DOS.out"
    scf = tmp_path / "Fe_SCF.out"
    unrelated = tmp_path / "other.out"
    corrupt = tmp_path / "broken.out"
    for path in (dos, scf, unrelated, corrupt):
        path.write_text(path.name)
    potential = (tmp_path / "Fe.pot_new").resolve()

    def parse(path):
        if path == corrupt:
            raise ValueError("malformed output")
        name = "Fe.pot_new" if path == scf else "other.pot_new"
        result = SimpleNamespace(
            task_name="scf",
            directory=str(tmp_path),
            files={"converged": name},
            input_parameters=None,
        )
        result.path_to = lambda key: result.files[key]
        return result

    monkeypatch.setattr(
        result_loading.TaskResult, "from_file", staticmethod(parse)
    )

    companion = load_companion_scf_result(_loaded(dos, potential=potential))
    assert companion is not None
    assert companion.output_path == scf.resolve()
    assert companion.potential_path == potential


def test_companion_scf_skips_unrelated_or_unreadable_outputs(tmp_path, monkeypatch):
    dos = tmp_path / "Fe_DOS.out"
    scf = tmp_path / "other.out"
    dos.touch()
    scf.touch()

    def parse(_path):
        raise ValueError("not an SCF output")

    monkeypatch.setattr(
        result_loading.TaskResult, "from_file", staticmethod(parse)
    )
    assert load_companion_scf_result(
        _loaded(dos, potential=tmp_path / "Fe.pot_new")
    ) is None
    assert load_companion_scf_result(_loaded(dos)) is None
    assert load_companion_scf_result(
        _loaded(dos, task="scf", potential=tmp_path / "Fe.pot_new")
    ) is None


def test_loading_dos_also_imports_scf_but_selects_dos(tmp_path, monkeypatch):
    dos_path = tmp_path / "Fe_DOS.out"
    scf_path = tmp_path / "Fe_SCF.out"
    dos_path.write_text("dos")
    scf_path.write_text("scf")
    potential = tmp_path / "Fe.pot_new"
    dos = _loaded(dos_path, potential=potential)
    scf = _loaded(scf_path, task="scf", potential=potential)
    monkeypatch.setattr(file_flows, "load_result_file", lambda *_: dos)
    monkeypatch.setattr(
        file_flows, "load_companion_scf_result", lambda _loaded: scf
    )
    monkeypatch.setattr(file_flows, "_finish_output_load", lambda *_args: None)
    controller = WorkspaceController()
    selected = []
    controller.resultSelectionRequested.connect(selected.append)

    assert file_flows.load_output(
        controller, RecentFiles(tmp_path / "recent.json"), dos_path, None
    )
    records = controller.result_store.results
    assert [record.task_name for record in records] == ["dos", "scf"]
    assert selected == [records[0].id]


def test_companion_failure_does_not_prevent_primary_output(tmp_path, monkeypatch):
    dos_path = tmp_path / "Fe_DOS.out"
    dos_path.write_text("dos")
    monkeypatch.setattr(
        file_flows, "load_result_file", lambda *_: _loaded(dos_path)
    )

    def fail(_loaded):
        raise OSError("missing or broken SCF output")

    monkeypatch.setattr(file_flows, "load_companion_scf_result", fail)
    monkeypatch.setattr(file_flows, "_finish_output_load", lambda *_args: None)
    controller = WorkspaceController()

    assert file_flows.load_output(
        controller, RecentFiles(tmp_path / "recent.json"), dos_path, None
    )
    assert [record.task_name for record in controller.result_store.results] == ["dos"]


def test_result_working_dialog_covers_companion_search_and_publication(
    tmp_path, monkeypatch,
):
    """Parsing belongs to worker; result and history signals run under Working."""
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    dos_path = tmp_path / "Fe_DOS.out"
    scf_path = tmp_path / "Fe_SCF.out"
    dos_path.touch()
    scf_path.touch()
    potential = tmp_path / "Fe.pot_new"
    dos = _loaded(dos_path, potential=potential)
    scf = _loaded(scf_path, task="scf", potential=potential)
    events = []

    def read(_path):
        assert QThread.currentThread() is not app.thread()
        events.append("read")
        return dos

    def companion(loaded):
        assert QThread.currentThread() is not app.thread()
        assert loaded is dos
        events.append("companion")
        return scf

    def assert_gui_and_working(label):
        assert QThread.currentThread() is app.thread()
        waiting = parent.findChild(_ModalTaskDialog)
        assert waiting is not None and waiting.isVisible()
        events.append(label)

    monkeypatch.setattr(file_flows, "load_result_file", read)
    monkeypatch.setattr(file_flows, "load_companion_scf_result", companion)
    controller = WorkspaceController()
    history = RecentFiles(tmp_path / "recent.json")
    controller.resultChanged.connect(
        lambda result: assert_gui_and_working(f"adopt:{result.task_name}")
    )
    history.changed.connect(lambda: assert_gui_and_working("history"))

    assert file_flows.load_output(controller, history, dos_path, parent)
    assert events == [
        "read", "companion", "adopt:scf", "adopt:dos", "history",
    ]
    assert [record.task_name for record in controller.result_store.results] == [
        "dos", "scf",
    ]
    parent.close()
