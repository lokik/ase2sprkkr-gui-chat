"""Embedded-cluster editor behavior against the matching ASE2SPRKKR backend."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest
import numpy as np
from ase import Atoms

# The GUI is released independently of the still-unreleased embedding API.
pytest.importorskip("ase2sprkkr.sprkkr.embedding_geometry")

from ase2sprkkr.sprkkr.embedding_geometry import HostSite
from ase2sprkkr_gui.ase.embedding import (
    EmbeddingDraft, embedding_source, occupation_label, parse_occupation,
)
from ase2sprkkr_gui.gui.application.structure_snapshot import StructureSnapshot
from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.dialogs.structures.embedding import BuildEmbeddingDialog
from ase2sprkkr_gui.gui.flows import structures as structure_flows
from ase2sprkkr_gui.physics.lattice import detect_structure_kind


def host():
    return Atoms("Cu", cell=[2., 2., 2.], positions=[[0, 0, 0]], pbc=True)


def test_occupation_input_is_exact_and_not_silently_renormalized():
    assert parse_occupation("Fe") == {"Fe": 1.0}
    assert parse_occupation("Fe:0.7, Ni:0.3") == {"Fe": 0.7, "Ni": 0.3}
    assert parse_occupation("Vc") == {"X": 1.0}
    assert occupation_label({"Ni": .3, "Fe": .7}) == "Ni:0.3, Fe:0.7"
    for value in ("Fe:0.2", "Cu:-0.1", "Yy", "Fe:0.5, Fe:0.5", ""):
        with pytest.raises(ValueError):
            parse_occupation(value)


def test_draft_creates_explicit_cluster_without_mutating_host():
    parent = host()
    source = parent.copy()
    center = HostSite(0, (0, 0, 0))
    draft = EmbeddingDraft(parent)
    assert draft.center == center
    assert draft.entries == {}
    draft.include([center])
    draft.set_occupation([center], {"Fe": 1.0})
    draft.set_displacement(center, [0.01, -0.02, 0.03])
    result = draft.to_atoms()
    assert result.embedding_host is parent
    assert detect_structure_kind(result) == "0d"
    assert result.sites[0].embedding.cell_index == (0, 0, 0)
    assert result.sites[0].occupation.to_dict(True) == {"Fe": 1.0}
    np.testing.assert_allclose(result.sites[0].embedding.displacement, [0.01, -0.02, 0.03])
    assert len(result) == 1
    assert np.array_equal(parent.positions, source.positions)
    assert parent.get_chemical_symbols() == ["Cu"]


def test_symmetry_preview_can_modify_real_host_sites():
    parent = host()
    draft = EmbeddingDraft(parent)
    site = HostSite(0, (1, 0, 0))
    orbit = draft.targets(site, symmetric=True)
    assert len(orbit) == 6
    draft.set_occupation(orbit, {"Fe": 1.0})
    assert len(draft.entries) == 6
    result = draft.to_atoms()
    assert len(result) == 6
    assert all(s.occupation.to_dict(True) == {"Fe": 1.0} for s in result.sites)
    assert parent.get_chemical_symbols() == ["Cu"]


def test_radius_shells_and_edit_again_preserve_host_reference():
    parent = host()
    draft = EmbeddingDraft(parent)
    assert draft.add_shells(1) == 7
    assert draft.add_radius(2.01) == 0
    centre = HostSite(0, (0, 0, 0))
    draft.set_occupation([centre], {"Fe": 1.0})
    result = draft.to_atoms()
    original = result.copy()
    assert embedding_source(result)[0] is parent
    again = EmbeddingDraft(parent, result)
    assert len(again.entries) == 7
    again.exclude([HostSite(0, (1, 0, 0))])
    edited = again.to_atoms()
    assert len(edited) == 6
    assert edited.embedding_host is parent
    assert len(result) == len(original) == 7


def test_displacement_symmetry_rejects_noninvariant_direction_without_partial_edit():
    draft = EmbeddingDraft(host())
    with pytest.raises(ValueError, match="conflicts"):
        draft.set_displacement(draft.center, [0.1, 0, 0], symmetric=True)
    assert not draft.entries


def test_snapshot_restores_embedded_occupations_positions_and_host_reference():
    parent = host()
    draft = EmbeddingDraft(parent)
    site = HostSite(0, (1, 0, 0))
    draft.set_occupation([site], {"Fe": 0.75, "Ni": 0.25})
    draft.set_displacement(site, [0, 0, 0.1])
    original = draft.to_atoms()
    snapshot = StructureSnapshot.capture(original)
    restored = snapshot.to_atoms()
    assert restored is not original
    assert restored.embedding_host is parent
    assert restored.sites[0].occupation.to_dict(True) == {"Fe": .75, "Ni": .25}
    assert restored.sites[0].embedding.cell_index == (1, 0, 0)
    np.testing.assert_allclose(restored.positions, original.positions)
    assert detect_structure_kind(restored) == "0d"


def test_dialog_builds_cluster_and_is_cancel_safe():
    parent = host()
    dialog = BuildEmbeddingDialog(parent)
    dialog.occupation.setText("Fe")
    dialog._assign_occupation()
    assert len(dialog.draft.entries) == 1
    dialog._accept()
    assert dialog.result_atoms is not None
    assert dialog.result_atoms.sites[0].occupation.to_dict(True) == {"Fe": 1.0}
    assert parent.get_chemical_symbols() == ["Cu"]


def test_flow_cancellation_leaves_workspace_unmodified(monkeypatch):
    parent = host()
    controller = WorkspaceController(WorkspaceState(atoms=parent))
    revision = controller.structure_generation
    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.dialogs.structures.embedding.select_build_embedding_structure",
        lambda candidate, _parent: None,
    )
    assert structure_flows.build_embedding_structure(controller, None) is None
    assert controller.atoms is parent
    assert controller.structure_generation == revision


def test_flow_acceptance_creates_new_structure_and_clears_periodic_run_capability(
    monkeypatch,
):
    parent = host()
    controller = WorkspaceController(WorkspaceState(atoms=parent, input_parameters=object()))
    generated = EmbeddingDraft(parent)
    generated.set_occupation([generated.center], {"Fe": 1.0})
    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.dialogs.structures.embedding.select_build_embedding_structure",
        lambda candidate, _parent: generated.to_atoms(),
    )
    result = structure_flows.build_embedding_structure(controller, None)
    assert result is controller.atoms
    assert result.embedding_host is parent
    assert controller.structure_kind() == "0d"
    assert not controller.can_calculate()


def test_generic_structure_save_does_not_drop_embedding_without_host_potfil(
    monkeypatch,
):
    from ase2sprkkr_gui.gui.flows import files as file_flows

    parent = host()
    draft = EmbeddingDraft(parent)
    draft.include([draft.center])
    cluster = draft.to_atoms()
    controller = WorkspaceController(WorkspaceState(atoms=cluster))
    messages = []
    monkeypatch.setattr(
        file_flows.QMessageBox, "warning",
        lambda *args: messages.append(args),
    )
    assert file_flows.save_structure(controller, None, None) is False
    assert messages
    assert controller.atoms is cluster


def test_native_structure_save_uses_cluster_potential_writer(monkeypatch, tmp_path):
    from ase2sprkkr.potentials.cluster_potentials import ClusterPotential
    from ase2sprkkr.sprkkr.sprkkr_atoms import SPRKKRAtoms
    from ase2sprkkr_gui.gui.flows import files as file_flows

    class HostPotential:
        LATTICE = object()

        def __init__(self):
            self.atoms = host()

    host_potential = HostPotential()
    cluster = SPRKKRAtoms.from_embedding(
        host_potential, [(0, (0, 0, 0), "Fe")]
    )
    controller = WorkspaceController(WorkspaceState(atoms=cluster))
    output = tmp_path / "cluster.clu"
    monkeypatch.setattr(
        file_flows.QFileDialog, "getSaveFileName",
        lambda *args: (str(output), "SPR-KKR cluster potential (*.clu *.pot)"),
    )

    class Writer:
        def save_to_file(self, filename):
            assert filename == output
            filename.write_text("CLUPOT", encoding="utf-8")

    def make_writer(atoms, *, scf_status):
        assert atoms is cluster
        assert scf_status == "START"
        return Writer()

    monkeypatch.setattr(ClusterPotential, "from_atoms", staticmethod(make_writer))

    class Recent:
        def remember(self, kind, path):
            assert kind == "structure"
            assert path == output

    assert file_flows.save_structure(controller, Recent(), None) is True
    assert output.read_text() == "CLUPOT"
    assert not controller.has_unsaved_structure
