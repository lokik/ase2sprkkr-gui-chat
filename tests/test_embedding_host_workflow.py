"""Embedding creation must establish its native host before SCF setup."""
from __future__ import annotations

import pytest
from ase.build import bulk
from ase2sprkkr import InputParameters, Potential, SPRKKRAtoms

from ase2sprkkr_gui.ase.embedding import EmbeddingDraft, embedding_source
from ase2sprkkr_gui.gui.application.recent_files import RecentFiles
from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.dialogs.structures import embedding as embedding_dialog
from ase2sprkkr_gui.gui.flows import calculation, files, structures


@pytest.mark.parametrize("source_kind", [
    "python_bulk", "native_host", "geometry_cluster", "layered_geometry",
])
def test_created_cluster_reuses_native_host_during_scf(monkeypatch, tmp_path, source_kind):
    source = bulk("Fe", "bcc", a=2.87, cubic=True)
    original_host = None
    if source_kind == "native_host":
        original_host = SPRKKRAtoms.promote_ase_atoms(source).potential
    elif source_kind == "geometry_cluster":
        draft = EmbeddingDraft(source)
        draft.include([draft.center])
        source = draft.to_atoms()
    elif source_kind == "layered_geometry":
        from ase2sprkkr.sprkkr.atoms_region import AtomsRegion

        source = SPRKKRAtoms(
            "Fe3", positions=[[0, 0, 0], [0, 0, 2], [0, 0, 4]],
            cell=[2, 2, 6], pbc=[True, True, False],
        )
        cell = source.cell.copy()
        cell[2] /= 3
        for index, name in enumerate(("left", "central", "right")):
            AtomsRegion(name, slice(index, index + 1), cell.copy(),
                        pbc=[True, True, name != "central"], atoms=source)
    controller = WorkspaceController(WorkspaceState(atoms=source))
    hosts = []

    def accept_cluster(candidate, _parent):
        host, existing = embedding_source(candidate)
        assert isinstance(host, Potential)
        hosts.append(host)
        draft = EmbeddingDraft(host, existing)
        draft.include([draft.center])
        return draft.to_atoms()

    def unexpected_host_prompt(*_args, **_kwargs):
        pytest.fail("A cluster created in the GUI must already have its host potential")

    monkeypatch.setattr(embedding_dialog, "select_build_embedding_structure", accept_cluster)
    monkeypatch.setattr(files.QFileDialog, "getOpenFileName", unexpected_host_prompt)
    result = structures.build_embedding_structure(controller, None)
    assert result is controller.atoms
    assert controller.can_run_embedding()
    assert result.embedding_host is hosts[0]
    if source_kind == "layered_geometry":
        assert set(hosts[0].atoms.regions) == {"left", "central", "right"}
        for name, region in source.regions.items():
            assert region.atoms is source
            assert hosts[0].atoms.regions[name].atoms is hosts[0].atoms
    if original_host is not None:
        assert hosts[0] is original_host
    else:
        # Creating a START host must not mutate the original structure.
        assert not isinstance(source, SPRKKRAtoms) or not source.has_potential()

    parameters = InputParameters.create("SCF")
    monkeypatch.setattr(calculation, "select_guided_input_parameters",
                        lambda *_args, **_kwargs: parameters)
    launched = []
    monkeypatch.setattr(calculation, "run_calculation",
                        lambda *_args, **_kwargs: launched.append(controller.atoms) or "run")
    assert calculation.configure_and_run_task(
        controller, RecentFiles(tmp_path / "recent.json"), None,
        "scf", dataset_default_mode="auto",
    ) == "run"
    assert launched == [result]
    assert controller.atoms.embedding_host is hosts[0]


def test_failed_host_creation_never_opens_embedding_editor(monkeypatch):
    source = bulk("Fe", "bcc", a=2.87, cubic=True)
    controller = WorkspaceController(WorkspaceState(atoms=source))
    generation = controller.structure_generation

    def fail_host(_atoms):
        raise ValueError("Cannot create host")

    def unexpected_editor(*_args):
        pytest.fail("The editor cannot open before its native host exists")

    messages = []
    monkeypatch.setattr(Potential, "from_atoms", fail_host)
    monkeypatch.setattr(embedding_dialog, "select_build_embedding_structure", unexpected_editor)
    monkeypatch.setattr(structures.QMessageBox, "warning",
                        lambda _parent, _title, message: messages.append(message))
    assert structures.build_embedding_structure(controller, None) is None
    assert messages == ["Cannot create host"]
    assert controller.atoms is source
    assert controller.structure_generation == generation
