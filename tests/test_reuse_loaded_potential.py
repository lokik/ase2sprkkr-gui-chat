"""Freshly parsed potentials can be adopted without a second disk parse."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from ase import Atoms

from ase2sprkkr_gui.gui.application import result_loading
from ase2sprkkr_gui.gui.application.potential_version import PotentialFileVersion
from ase2sprkkr_gui.gui.application.result_loading import PotentialVersionChanged
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController


def _fresh_import(tmp_path, monkeypatch):
    potential = tmp_path / "Fe.pot"
    potential.write_text("test potential", encoding="utf-8")
    parsed_atoms = Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
    reads = []

    def read(path):
        reads.append(path)
        return parsed_atoms

    monkeypatch.setattr(result_loading, "load_potential_atoms", read)
    result = SimpleNamespace(
        task_name="dos",
        files={"potential": potential.name},
        directory=str(tmp_path),
        output_values={},
        input_parameters=None,
    )
    result.path_to = lambda key: result.files[key]
    loaded = result_loading.load_result(result, fallback_directory=tmp_path)
    controller = WorkspaceController()
    record = controller.adopt_external_result(loaded).record
    assert record is not None
    return controller, record, loaded, potential, reads


def test_fresh_import_reuses_verified_atoms(tmp_path, monkeypatch):
    controller, record, loaded, potential, reads = _fresh_import(tmp_path, monkeypatch)
    state = controller.result_store.state_for_result(record)
    assert loaded.parsed_potential_version == state.potential_version
    assert reads == [potential.resolve()]

    controller.use_result_state(record.id, reusable_loaded=loaded)
    assert controller.atoms is loaded.atoms
    assert reads == [potential.resolve()]  # no second potential parse


def test_unverified_loaded_version_falls_back_to_disk(tmp_path, monkeypatch):
    controller, record, loaded, potential, reads = _fresh_import(tmp_path, monkeypatch)
    unverified = replace(loaded, parsed_potential_version=None)

    controller.use_result_state(record.id, reusable_loaded=unverified)
    assert reads == [potential.resolve(), potential.resolve()]


def test_changed_potential_never_uses_previously_parsed_atoms(tmp_path, monkeypatch):
    controller, record, loaded, potential, reads = _fresh_import(tmp_path, monkeypatch)
    potential.write_text("changed potential with different size", encoding="utf-8")

    with pytest.raises(PotentialVersionChanged, match="changed"):
        controller.use_result_state(record.id, reusable_loaded=loaded)
    assert controller.atoms is None
    assert len(reads) == 1


def test_host_dependency_checked_on_reuse(tmp_path, monkeypatch):
    controller, record, loaded, potential, reads = _fresh_import(tmp_path, monkeypatch)
    host = tmp_path / "host.pot"
    host.write_text("original host", encoding="utf-8")
    host_version = PotentialFileVersion.capture(host)
    loaded = replace(loaded, source_versions=(host_version,))
    # A new import records both the parsed potential and the host dependency.
    new_controller = WorkspaceController()
    new_record = new_controller.adopt_external_result(loaded).record
    host.write_text("overwritten host with new content", encoding="utf-8")
    with pytest.raises(PotentialVersionChanged, match="changed"):
        new_controller.use_result_state(new_record.id, reusable_loaded=loaded)
    assert new_controller.atoms is None
