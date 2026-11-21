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
    scf.write_text(" *       KKRSCF  VERSION  9.7          *\n")
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


def test_companion_matches_potential_before_loading_and_reuses_atoms(
    tmp_path, monkeypatch,
):
    from dataclasses import replace

    dos, wrong, right = (
        tmp_path / "Fe_DOS.out",
        tmp_path / "Fe_old_SCF.out",
        tmp_path / "Fe_SCF.out",
    )
    for path in (dos, wrong, right):
        path.write_text(" *       KKRSCF  VERSION  9.7          *\n")
    potential = (tmp_path / "Fe.pot_new").resolve()
    parsed = []
    atoms = object()

    def parse(path):
        parsed.append(path)
        filename = "old.pot_new" if path == wrong else potential.name
        result = SimpleNamespace(
            task_name="scf", directory=str(tmp_path),
            files={"converged": filename}, input_parameters=None,
            output_values={}, is_embedded=False,
        )
        result.path_to = lambda key: result.files[key]
        return result

    reads = []
    monkeypatch.setattr(result_loading.TaskResult, "from_file", staticmethod(parse))
    monkeypatch.setattr(
        result_loading, "_load_potential",
        lambda *_args, **_kwargs: reads.append("potential") or (None, None),
    )
    selected = replace(_loaded(dos, potential=potential), atoms=atoms)
    companion = load_companion_scf_result(selected)

    assert companion is not None
    assert companion.output_path == right.resolve()
    assert companion.atoms is atoms
    assert parsed == [right]  # Matching filename is checked before other SCFs.
    assert reads == []  # Neither unrelated nor already-parsed POTFIL is reread.


def test_companion_scf_prefilters_header_and_only_uses_out(tmp_path, monkeypatch):
    dos = tmp_path / "Fe_DOS.out"
    misleading = tmp_path / "Fe_SCF.out"
    valid = tmp_path / "other.out"
    log = tmp_path / "Fe_SCF.log"
    txt = tmp_path / "Fe_SCF.txt"
    long_intro = tmp_path / "Fe_old_SCF.out"
    dos.write_text(" *       KKR DOS  VERSION  9.7       *\n")
    misleading.write_text(" *       KKR DOS  VERSION  9.7       *\n")
    valid.write_text("    *       KKRSCF  VERSION  10.1       *\n")
    log.write_text(" *       KKRSCF  VERSION  9.7       *\n")
    txt.write_text(" *       KKRSCF  VERSION  9.7       *\n")
    long_intro.write_bytes(b" " * 8192 + b"KKRSCF VERSION 9.7")
    parsed = []
    potential = (tmp_path / "Fe.pot_new").resolve()

    def parse(path):
        parsed.append(path)
        result = SimpleNamespace(
            task_name="scf", directory=str(tmp_path),
            files={"converged": potential.name}, input_parameters=None,
        )
        result.path_to = lambda key: result.files[key]
        return result

    monkeypatch.setattr(result_loading.TaskResult, "from_file", staticmethod(parse))
    companion = load_companion_scf_result(_loaded(dos, potential=potential))

    assert companion is not None
    assert companion.output_path == valid.resolve()
    assert parsed == [valid]


def test_companion_scf_tries_next_candidate_if_potential_differs(
    tmp_path, monkeypatch,
):
    dos = tmp_path / "Fe_DOS.out"
    expected_name = tmp_path / "Fe_SCF.out"
    fallback = tmp_path / "Fe_older_SCF.out"
    dos.touch()
    expected_name.write_text(" *       KKRSCF  VERSION  9.7       *\n")
    fallback.write_text(" *       KKRSCF  VERSION  10.1       *\n")
    potential = (tmp_path / "Fe.pot_new").resolve()
    parsed = []

    def parse(path):
        parsed.append(path)
        name = "other.pot_new" if path == expected_name else potential.name
        result = SimpleNamespace(
            task_name="scf", directory=str(tmp_path),
            files={"converged": name}, input_parameters=None,
        )
        result.path_to = lambda key: result.files[key]
        return result

    monkeypatch.setattr(result_loading.TaskResult, "from_file", staticmethod(parse))
    companion = load_companion_scf_result(_loaded(dos, potential=potential))

    assert companion is not None
    assert companion.output_path == fallback.resolve()
    assert parsed == [expected_name, fallback]


def test_companion_scf_rejects_mismatched_native_potential_before_parsing(
    tmp_path, monkeypatch,
):
    dos = tmp_path / "Fe_DOS.out"
    wrong = tmp_path / "Fe_SCF.out"  # Preferred name, but different POTFIL.
    right = tmp_path / "Fe_backup_SCF.out"
    dos.write_text("DOS output")
    wrong.write_text(
        "  * KKRSCF VERSION 9.7 *\n"
        " TASK = SCF\n FILES:\n potential : Fe.pot\n"
        " parameters for SCF-cycle\n pot-file : other.pot_new\n"
    )
    right.write_text(
        "  * KKRSCF VERSION 9.7 *\n"
        " TASK = SCF\n FILES:\n potential : Fe.pot\n"
        " parameters for SCF-cycle\n pot-file : Fe.pot_new\n"
    )
    potential = (tmp_path / "Fe.pot_new").resolve()
    parsed = []

    def parse(path):
        parsed.append(path)
        output = SimpleNamespace(
            task_name="scf", directory=str(tmp_path), is_embedded=False,
            files={"converged": potential.name}, input_parameters=None,
            output_values={},
        )
        output.path_to = lambda key: output.files[key]
        return output

    monkeypatch.setattr(result_loading.TaskResult, "from_file", staticmethod(parse))
    companion = load_companion_scf_result(_loaded(dos, potential=potential))

    assert companion is not None
    assert companion.output_path == right.resolve()
    assert parsed == [right]
