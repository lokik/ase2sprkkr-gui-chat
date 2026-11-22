"""GUI-only companion integration; SCF matching is tested in ASE2SPRKKR."""
from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QWidget

from ase2sprkkr_gui.gui.misc.modal_task import _ModalTaskDialog
from ase2sprkkr_gui.gui.application.recent_files import RecentFiles
from ase2sprkkr_gui.gui.application.result_loading import (
    LoadedResult, load_companion_scf_result,
)
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.application import result_loading
from ase2sprkkr_gui.gui.flows import files as file_flows


def _loaded(path, *, task="dos", potential=None):
    return LoadedResult(
        result=SimpleNamespace(
            task_name=task, produces_potential=task == "scf", output_values={}
        ),
        output_path=path, potential_path=potential, atoms=None,
        potential_error=None, directory=str(path.parent),
    )


def test_loading_dos_also_imports_scf_but_selects_dos(tmp_path, monkeypatch):
    dos_path = tmp_path / "Fe_DOS.out"
    scf_path = tmp_path / "Fe_SCF.out"
    dos_path.write_text("dos")
    scf_path.write_text("scf")
    potential = tmp_path / "Fe.pot_new"
    dos = _loaded(dos_path, potential=potential)
    scf = _loaded(scf_path, task="scf", potential=potential)
    monkeypatch.setattr(file_flows, "load_result_file", lambda *_args, **_kwargs: dos)
    monkeypatch.setattr(
        file_flows, "load_companion_scf_result", lambda _loaded, **_kwargs: scf
    )
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
        file_flows, "load_result_file", lambda *_args, **_kwargs: _loaded(dos_path)
    )

    def fail(_loaded, **_kwargs):
        raise OSError("missing or broken SCF output")

    monkeypatch.setattr(file_flows, "load_companion_scf_result", fail)
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

    def read(_path, *, cache=None):
        assert QThread.currentThread() is not app.thread()
        events.append("read")
        return dos

    def companion(loaded, *, cache=None):
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


def test_companion_loading_delegates_to_backend_without_rereading_potential(
    tmp_path, monkeypatch,
):
    from dataclasses import replace
    from ase2sprkkr.outputs.task_result import TaskResult

    dos_path = tmp_path / "Fe_DOS.out"
    scf_path = tmp_path / "Fe_SCF.out"
    potential = tmp_path / "Fe.pot_new"
    dos_path.touch()
    scf_path.touch()
    potential.write_text("reusable")
    atoms = object()
    selected = replace(
        _loaded(dos_path, potential=potential), atoms=atoms,
        parsed_potential_version=result_loading.PotentialFileVersion.capture(potential),
    )

    scf = TaskResult(None, None, tmp_path, output_file=scf_path, task_name="SCF")
    scf.produces_potential = True
    scf.files.add_file("converged", potential)

    seen = []
    def companion(self, result):
        seen.append(result)
        return scf

    monkeypatch.setattr(result_loading.ResultLoader, "companion_scf", companion)
    cache = result_loading.ResultImportCache()
    parses = []

    def raw_potential(_cls, path, *, atoms, check_file_unchanged, host=None):
        parses.append(path)
        assert atoms is False and check_file_unchanged
        return SimpleNamespace(atoms=selected.atoms)

    monkeypatch.setattr(
        result_loading.Potential, "from_file", classmethod(raw_potential)
    )
    # An earlier result loaded this same POTFIL into the batch cache.
    assert cache.loader.potential(potential).atoms is atoms

    companion_loaded = load_companion_scf_result(selected, cache=cache)
    assert companion_loaded is not None
    assert companion_loaded.output_path == scf_path.resolve()
    assert companion_loaded.atoms is atoms
    assert len(parses) == 1
    assert seen == [selected.result]


def test_explicit_load_shares_one_loader_but_next_load_starts_fresh(
    tmp_path, monkeypatch,
):
    """One import shares its loader with companion SCF; the next starts fresh."""
    path = tmp_path / "Fe_DOS.out"
    path.touch()
    main = _loaded(path, potential=tmp_path / "Fe.pot_new")
    seen = []

    def read(_path, *, cache):
        assert _path == path
        seen.append(("main", cache.loader))
        return main

    def companion(loaded, *, cache):
        assert loaded is main
        seen.append(("companion", cache.loader))
        return None

    # Native directory and parsed-potential caching is covered in ASE2SPRKKR.
    monkeypatch.setattr(file_flows, "load_result_file", read)
    monkeypatch.setattr(file_flows, "load_companion_scf_result", companion)
    monkeypatch.setattr(
        file_flows, "run_modal_task",
        lambda _parent, operation, **kwargs: kwargs["on_success"](operation()),
    )

    def explicit_load():
        return file_flows._read_output(
            path, None, known_scf_potentials=frozenset(),
            on_success=lambda loaded, scf: (loaded, scf),
        )

    assert explicit_load() == (main, None)
    assert explicit_load() == (main, None)
    assert [kind for kind, _ in seen] == [
        "main", "companion", "main", "companion",
    ]
    assert seen[0][1] is seen[1][1]
    assert seen[2][1] is seen[3][1]
    assert seen[0][1] is not seen[2][1]
