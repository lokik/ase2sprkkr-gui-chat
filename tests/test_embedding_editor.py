"""Embedded-cluster editor behavior against the matching ASE2SPRKKR backend."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest
import numpy as np
from ase import Atoms
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton

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
    dialog.occupation.set_occupation({"Fe": 1.0})
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

    def make_writer(atoms):
        assert atoms is cluster
        return Writer()

    monkeypatch.setattr(ClusterPotential, "from_atoms", staticmethod(make_writer))

    class Recent:
        def remember(self, kind, path):
            assert kind == "structure"
            assert path == output

    assert file_flows.save_structure(controller, Recent(), None) is True
    assert output.read_text() == "CLUPOT"
    assert not controller.has_unsaved_structure


def test_cluster_file_detection_distinguishes_host_potfil(tmp_path):
    from ase2sprkkr import Potential

    host_file = tmp_path / "host.pot"
    host_file.write_text("GLOBAL SYSTEM PARAMETER\nNQ 1\n")
    cluster_file = tmp_path / "cluster.pot"
    cluster_file.write_text("GLOBAL SYSTEM PARAMETER\nNQCLU 1\n")
    native_file = tmp_path / "cluster.clu"
    native_file.write_text("GLOBAL SYSTEM PARAMETER\nNQCLU 1\n")
    assert not Potential.is_cluster_file(host_file)
    assert Potential.is_cluster_file(cluster_file)
    assert Potential.is_cluster_file(native_file)


def test_open_native_cluster_with_explicit_host_preserves_reference(
    monkeypatch, tmp_path,
):
    from ase2sprkkr import Potential
    from ase2sprkkr.potentials.cluster_potentials import ClusterPotential
    from ase2sprkkr_gui.gui.flows import files as file_flows

    cluster_path = tmp_path / "sample.clu"
    cluster_path.write_text("mock-cluster")
    host_path = tmp_path / "host.pot"
    host_path.write_text("mock-host")
    reference = host()

    class HostPotential:
        LATTICE = object()
        atoms = reference

    original_host = HostPotential()
    generated = EmbeddingDraft(original_host)
    generated.set_occupation([generated.center], {"Fe": 1.0})
    expected = generated.to_atoms()

    class Parsed:
        @property
        def atoms(self):
            return expected

        def host_geometry_warning(self):
            return None

        def host_geometry_warning(self):
            return None

    calls = []
    monkeypatch.setattr(Potential, "from_file",
                        staticmethod(lambda path: original_host))
    monkeypatch.setattr(
        ClusterPotential, "from_file",
        staticmethod(lambda path, *, host: (
            calls.append((path, host)) or Parsed()
        )),
    )
    monkeypatch.setattr(file_flows, "run_modal_task",
                        lambda parent, action, **kwargs: action())

    class Recent:
        def __init__(self):
            self.saved = []

        def remember(self, kind, path):
            self.saved.append((kind, path))

    recent = Recent()
    controller = WorkspaceController(WorkspaceState(atoms=host()))
    generation = controller.structure_generation
    assert file_flows.load_embedded_cluster(
        controller, recent, cluster_path, None, host_path=host_path
    )
    assert controller.atoms is expected
    assert controller.atoms.embedding_host is original_host
    assert controller.structure_generation == generation.next()
    assert recent.saved == [("structure", cluster_path)]
    assert calls == [(cluster_path, original_host)]


def test_invalid_cluster_import_does_not_replace_workspace(monkeypatch, tmp_path):
    from ase2sprkkr import Potential
    from ase2sprkkr.potentials.cluster_potentials import ClusterPotential
    from ase2sprkkr_gui.gui.flows import files as file_flows

    initial = host()
    controller = WorkspaceController(WorkspaceState(atoms=initial))
    generation = controller.structure_generation
    monkeypatch.setattr(Potential, "from_file",
                        staticmethod(lambda path: object()))
    monkeypatch.setattr(ClusterPotential, "from_file",
                        staticmethod(lambda path, *, host: (_ for _ in ()).throw(
                            ValueError("Bad cluster")
                        )))
    monkeypatch.setattr(file_flows, "run_modal_task",
                        lambda parent, action, **kwargs: action())
    errors = []
    monkeypatch.setattr(file_flows.QMessageBox, "critical",
                        lambda *args: errors.append(args))

    assert not file_flows.load_embedded_cluster(
        controller, None, tmp_path / "bad.clu", None, host_path=tmp_path / "host.pot"
    )
    assert errors and "Bad cluster" in str(errors[0])
    assert controller.atoms is initial
    assert controller.structure_generation == generation


def test_embedded_global_preview_shows_distant_sites_without_allowing_picking():
    from types import SimpleNamespace

    dialog = BuildEmbeddingDialog(host())
    outside = HostSite(0, (15, -3, 2))
    dialog.draft.include([outside])
    dialog._render()
    assert outside in dialog._rendered_sites
    assert outside not in dialog._local_sites
    selected_before = dialog._selected_site
    dialog._local_pick(SimpleNamespace(artist=dialog._scatter, ind=[0]))
    assert dialog._selected_site == selected_before

    # Navigate to the distant cell; only its local preview becomes pickable.
    for spin, value in ((dialog.origin_x, 15), (dialog.origin_y, -3),
                        (dialog.layer, 2)):
        spin.setValue(value)
    dialog._selected_cell = outside.cell_index
    dialog._refresh_cells()
    dialog._refresh_sites()
    dialog._render()
    assert outside in dialog._local_sites
    idx = dialog._local_sites.index(outside)
    dialog._local_pick(SimpleNamespace(artist=dialog._local_scatter, ind=[idx]))
    assert dialog._selected_site == outside


def test_2d_editor_can_reopen_special_native_translation_vectors():
    from ase2sprkkr.sprkkr.sprkkr_atoms import SPRKKRAtoms

    parent = Atoms("Cu", positions=[[0, 0, 0]], cell=[2, 2, 12],
                   pbc=(True, True, False))
    original = HostSite(0, (1, 0, 0, 0, 1))
    cluster = SPRKKRAtoms.from_embedding(
        parent, [(original.original_site, original.cell_index, "Fe")]
    )
    draft = EmbeddingDraft(parent, cluster)
    assert original in draft.cell_sites((1, 0))
    assert original in draft.entries
    assert draft.to_atoms().sites[0].embedding.translation_vector == (1, 0, 0, 0, 1)


def test_reopening_unmodified_cluster_returns_lossless_copy():
    parent = host()
    original_draft = EmbeddingDraft(parent)
    original_draft.set_occupation([original_draft.center], {"Fe": 1.0})
    cluster = original_draft.to_atoms()
    cluster.sites[0].magnetisation = np.array([0.1, 0.25])
    editor_draft = EmbeddingDraft(parent, cluster)
    assert editor_draft.is_unchanged()
    restored = editor_draft.to_atoms()
    assert restored is not cluster
    assert restored.embedding_host is parent
    np.testing.assert_array_equal(restored.sites[0].magnetisation,
                                  cluster.sites[0].magnetisation)
    editor_draft.set_occupation([editor_draft.center], {"Ni": 1.0})
    assert not editor_draft.is_unchanged()
    assert not editor_draft.would_discard_radial_data()


def test_editor_requires_approval_to_drop_calculated_radial_data(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    parent = host()
    draft = EmbeddingDraft(parent)
    draft.include([draft.center])
    cluster = draft.to_atoms()
    dialog = BuildEmbeddingDialog(cluster)
    dialog.occupation.set_occupation({"Fe": 1.0})
    dialog._assign_occupation()
    monkeypatch.setattr(dialog.draft, "would_discard_radial_data", lambda: True)
    prompts = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args: (prompts.append(args) or QMessageBox.StandardButton.Cancel),
    )
    dialog._accept()
    assert len(prompts) == 1
    assert dialog.result_atoms is None
    assert dialog.draft.entries[dialog.draft.center].occupation == {"Fe": 1.0}
    assert cluster.sites[0].occupation.to_dict(True) == {"Cu": 1.0}


def test_radial_data_reset_requires_explicit_authorization(monkeypatch):
    parent = host()
    base = EmbeddingDraft(parent)
    base.include([base.center])
    cluster = base.to_atoms()
    edit = EmbeddingDraft(parent, cluster)
    edit.set_occupation([edit.center], {"Fe": 1.0})
    monkeypatch.setattr(edit, "would_discard_radial_data", lambda: True)
    with pytest.raises(ValueError, match="allow_radial_reset"):
        edit.to_atoms()
    changed = edit.to_atoms(allow_radial_reset=True)
    assert changed.sites[0].occupation.to_dict(True) == {"Fe": 1.0}
    assert cluster.sites[0].occupation.to_dict(True) == {"Cu": 1.0}


def test_embedding_snapshot_has_single_uncopied_host_link():
    parent = host()
    draft = EmbeddingDraft(parent)
    draft.include([draft.center])
    snapshot = StructureSnapshot.capture(draft.to_atoms())
    assert snapshot.info["sprkkr_embedding_host"] is parent
    assert not hasattr(snapshot, "embedding_host")
    assert snapshot.to_atoms().embedding_host is parent


def test_inplane_cells_is_an_actual_cell_count():
    dialog = BuildEmbeddingDialog(host())
    dialog.inplane_cells.setValue(4)
    assert dialog.cells.rowCount() == 4
    assert dialog.cells.columnCount() == 4
    assert dialog._inplane_offsets() == (-2, -1, 0, 1)
    dialog.inplane_cells.setValue(5)
    assert dialog.cells.rowCount() == 5
    assert dialog._inplane_offsets() == (-2, -1, 0, 1, 2)


def test_unchanged_embedding_editor_does_not_restart_workspace(monkeypatch):
    parent = host()
    draft = EmbeddingDraft(parent)
    draft.include([draft.center])
    cluster = draft.to_atoms()
    controller = WorkspaceController(WorkspaceState(atoms=cluster))
    generation = controller.structure_generation
    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.dialogs.structures.embedding.select_build_embedding_structure",
        lambda source, _parent: source,
    )
    assert structure_flows.build_embedding_structure(controller, None) is None
    assert controller.atoms is cluster
    assert controller.structure_generation == generation


def test_versioned_cluster_history_restores_native_file_using_same_host(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from ase2sprkkr.potentials.cluster_potentials import ClusterPotential
    from ase2sprkkr_gui.gui.application.historical_state import _restore_state_atoms

    parent = host()
    draft = EmbeddingDraft(parent)
    draft.set_occupation([draft.center], {"Fe": 1.0})
    atoms = draft.to_atoms()

    path = tmp_path / "calculated.clu"
    path.write_text("NQCLU 1\\n", encoding="utf-8")
    controller = WorkspaceController(WorkspaceState(
        atoms=atoms, potential_path=str(path)
    ))
    state = controller.result_store.state(controller.state_id)
    assert state.potential_version is not None
    assert state.structure_snapshot is not None
    assert state.structure_snapshot.info["sprkkr_embedding_host"] is parent

    calls = []
    monkeypatch.setattr(
        ClusterPotential,
        "from_file",
        staticmethod(lambda path, *, host: (
            calls.append((path, host)) or SimpleNamespace(atoms=atoms.copy())
        )),
    )

    restored = _restore_state_atoms(state)
    assert restored.embedding_host is parent
    assert restored.sites[0].occupation.to_dict(True) == {"Fe": 1.0}
    assert calls == [(path.resolve(), parent)]


def test_mismatched_host_requires_confirmation_before_workspace_replacement(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    from ase2sprkkr import Potential
    from ase2sprkkr.potentials.cluster_potentials import ClusterPotential
    from PyQt6.QtWidgets import QMessageBox
    from ase2sprkkr_gui.gui.flows import files as file_flows

    parent = host()
    draft = EmbeddingDraft(parent)
    draft.include([draft.center])
    cluster = draft.to_atoms()

    path = tmp_path / "unmatched.clu"
    path.write_text("NQCLU 1\\n", encoding="utf-8")
    original = host()
    controller = WorkspaceController(WorkspaceState(atoms=original))
    before = controller.structure_generation
    prompts = []

    monkeypatch.setattr(Potential, "from_file",
                        staticmethod(lambda *_args: SimpleNamespace(LATTICE=object())))
    monkeypatch.setattr(
        ClusterPotential, "from_file",
        staticmethod(lambda *args, **kwargs: SimpleNamespace(
            atoms=cluster,
            host_geometry_warning=lambda: "Host mismatch",
        )),
    )
    monkeypatch.setattr(file_flows, "run_modal_task",
                        lambda _parent, fn, **_kw: fn())
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args: (prompts.append(args) or QMessageBox.StandardButton.Cancel),
    )
    assert not file_flows.load_embedded_cluster(
        controller, None, path, None, host_path=tmp_path / "host.pot"
    )
    assert controller.atoms is original
    assert controller.structure_generation == before
    assert len(prompts) == 1


def test_active_cluster_potential_is_not_mistaken_for_a_periodic_host():
    from ase2sprkkr_gui.gui.flows.files import _current_host_potential

    base = host()
    draft = EmbeddingDraft(base)
    draft.include([draft.center])
    cluster = draft.to_atoms()
    cluster._potential = object()  # Simulate an attached non-periodic potential.
    controller = WorkspaceController(WorkspaceState(atoms=cluster))
    assert _current_host_potential(controller) is None


def test_guided_cluster_scf_is_available_without_input_parameters():
    from ase2sprkkr import Potential, SPRKKRAtoms
    from ase2sprkkr_gui.gui.workflow.planner import WorkflowPlanner
    from ase2sprkkr_gui.gui.workflow.model import RunTask, WorkflowSection

    native_host = Potential.from_atoms(host())
    cluster = SPRKKRAtoms.from_embedding(
        native_host, [(0, (0, 0, 0), "Fe")]
    )
    controller = WorkspaceController(WorkspaceState(atoms=cluster))
    assert controller.can_run_embedding()
    assert not controller.can_calculate()  # Guided setup creates parameters.
    plan = WorkflowPlanner().for_workspace(controller)
    calculation = plan.group(WorkflowSection.CALCULATE)
    assert calculation is not None
    assert RunTask("scf") in [action.command for action in calculation.actions]


def test_embedding_preview_uses_qt_labels_without_matplotlib_layout_warning():
    import warnings

    dialog = BuildEmbeddingDialog(host())
    dialog.draft.include([dialog.draft.center])
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        dialog._render()
        dialog.canvas.draw()

    assert "Included: 1/1" in dialog.preview_info.text()
    assert "Displayed:" in dialog.preview_info.text()
    assert "Cell (0, 0, 0)" in dialog.local_info.text()
    assert dialog.axes.get_title() == ""
    assert dialog.axes.get_xlabel() == ""
    assert dialog.axes.get_ylabel() == ""
    assert dialog.axes.get_zlabel() == ""
    assert not any("Tight layout not applied" in str(w.message) for w in captured)


def test_editor_occupation_matches_bulk_rows_with_normalize_and_picker():
    from ase2sprkkr_gui.gui.widgets.structures.occupation import OccupationEditor

    dialog = BuildEmbeddingDialog(host())
    assert isinstance(dialog.occupation, OccupationEditor)
    dialog.occupation.set_occupation({"Fe": 0.7, "Ni": 0.3})
    assert dialog.occupation.is_valid()
    assert dialog.occupation.occupation() == {"Fe": 0.7, "Ni": 0.3}
    dialog.occupation.set_occupation({"Fe": 0.6})
    assert not dialog.occupation.is_valid()
    dialog.occupation.normalize()
    assert dialog.occupation.occupation() == {"Fe": 1.0}
    dialog.occupation.set_occupation({"Vc": 1.0})
    assert dialog.occupation.occupation() == {"X": 1.0}
    dialog.occupation.set_occupation({"Fe": 1.0})
    dialog.occupation.add_row()
    second = dialog.occupation.rows[-1]
    second["elem"].setText("Ni")
    second["occ"].setValue(0.5)
    assert not dialog.occupation.is_valid()
    dialog.occupation.rows[0]["occ"].setValue(0.5)
    dialog._assign_occupation()
    assert dialog.draft.entries[dialog.draft.center].occupation == {
        "Fe": 0.5, "Ni": 0.5
    }


def test_global_preview_highlights_all_equivalents_not_only_selected_cell():
    dialog = BuildEmbeddingDialog(host())
    selected = HostSite(0, (1, 0, 0))
    dialog._selected_cell = selected.cell_index
    dialog._selected_site = selected
    dialog._update_symmetry_preview()
    dialog._render()
    assert len(dialog._highlighted) == 6
    assert set(dialog._highlighted) <= set(dialog._rendered_sites)
    assert any(site.cell_index != selected.cell_index
               for site in dialog._rendered_sites)
    assert len(dialog._local_sites) == 1
    dialog.global_scope.setCurrentIndex(1)  # Current layer
    dialog._render_global()
    assert all(site.cell_index[2] == dialog.layer.value()
               for site in dialog._rendered_sites)
    dialog.global_scope.setCurrentIndex(0)
    dialog._render_global()
    assert set(dialog._highlighted) <= set(dialog._rendered_sites)


def test_bulk_occupation_uses_the_same_widget_as_embedding():
    from ase2sprkkr_gui.gui.dialogs.structures.element_assignment import (
        ElementAssignmentDialog,
    )
    from ase2sprkkr_gui.gui.widgets.structures.occupation import OccupationEditor

    parent = host()
    parent.set_array("spacegroup_kinds", np.array([0]))
    parent.info["occupancy"] = {"0": {"Cu": 1.0}}
    editor = ElementAssignmentDialog()
    editor.setup(parent)
    row = editor._letter_widgets[0]
    assert isinstance(row.occupation_editor, OccupationEditor)
    row.occupation_editor.rows[0]["occ"].setValue(0.6)
    assert editor._draft.sites[0].occupancies[0].value == 0.6
    row.occupation_editor.normalize()
    assert editor._draft.sites[0].occupancies[0].value == 1.0


def test_occupation_editor_rejects_unnamed_positive_fraction():
    dialog = BuildEmbeddingDialog(host())
    dialog.occupation.set_occupation({"Fe": 0.5})
    dialog.occupation.add_row()
    dialog.occupation.rows[-1]["occ"].setValue(0.5)
    assert not dialog.occupation.is_valid()
    with pytest.raises(ValueError, match="element is required"):
        dialog.occupation.occupation()


def test_embedding_occupation_is_committed_automatically_only_when_valid(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    dialog = BuildEmbeddingDialog(host())
    site = dialog.draft.center
    prompts = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: prompts.append(args)
    )
    # Editing the selected site's occupation should not ask for a second Apply.
    dialog.occupation.rows[0]["elem"].setText("Fe")
    assert dialog.draft.entries[site].occupation == {"Fe": 1.0}

    # A temporary incomplete mixture must not corrupt the already valid state.
    dialog.occupation.add_row()
    dialog.occupation.rows[0]["occ"].setValue(0.7)
    dialog.occupation.rows[1]["elem"].setText("Ni")
    assert not dialog.occupation.is_valid()
    assert dialog.draft.entries[site].occupation == {"Fe": 1.0}

    row = dialog.occupation.rows[1]
    row["occ"].setValue(0.3)
    assert dialog.occupation.is_valid()
    assert dialog.draft.entries[site].occupation == {"Fe": 0.7, "Ni": 0.3}
    assert dialog.occupation.rows[1] is row  # No focus-destroying rebuild
    assert prompts == []


def test_embedding_auto_occupation_overwrites_other_symmetry_sites_only_with_consent(
    monkeypatch,
):
    from PyQt6.QtWidgets import QMessageBox

    dialog = BuildEmbeddingDialog(host())
    selected = HostSite(0, (1, 0, 0))
    dialog._selected_cell = selected.cell_index
    dialog._selected_site = selected
    dialog._refresh_sites()
    dialog.symmetric.setChecked(True)
    orbit = dialog.draft.targets(selected, symmetric=True)
    assert len(orbit) > 1

    dialog.occupation.rows[0]["elem"].setText("Fe")
    assert all(dialog.draft.entries[s].occupation == {"Fe": 1.0} for s in orbit)

    other = next(s for s in orbit if s != selected)
    dialog.draft.set_occupation([other], {"Ni": 1.0})
    messages = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: (
            messages.append(args) or QMessageBox.StandardButton.Cancel
        ),
    )

    dialog.occupation.rows[0]["elem"].setText("Co")
    assert dialog.draft.entries[other].occupation == {"Ni": 1.0}
    assert dialog.occupation.occupation() == {"Fe": 1.0}  # Restored after Cancel

    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: (
            messages.append(args) or QMessageBox.StandardButton.Yes
        ),
    )
    dialog.occupation.rows[0]["elem"].setText("Co")
    assert all(dialog.draft.entries[s].occupation == {"Co": 1.0} for s in orbit)

    dialog.occupation.rows[0]["elem"].setText("Mn")
    assert all(dialog.draft.entries[s].occupation == {"Mn": 1.0} for s in orbit)
    assert len(messages) == 2  # Cancel once, then approve the edit sequence


def test_incomplete_occupation_is_not_discarded_by_selection_or_ok(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    # A two-site host makes it possible to navigate the selected site's rows.
    source = Atoms(
        "Cu2", positions=[[0, 0, 0], [1, 1, 1]],
        cell=[2, 2, 2], pbc=True
    )
    dialog = BuildEmbeddingDialog(source)
    selected = dialog._selected_site
    dialog.occupation.rows[0]["occ"].setValue(0.4)
    assert not dialog.occupation.is_valid()

    # Switching the displayed site would silently discard the partial mixture.
    other_row = 1 if selected == dialog._site_rows[0] else 0
    dialog.sites.selectRow(other_row)
    assert dialog._selected_site == selected
    assert dialog.occupation.rows[0]["occ"].value() == 0.4

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args: warnings.append(args)
    )
    dialog._accept()
    assert dialog.result_atoms is None
    assert warnings


def test_clicked_grid_cell_matches_its_coordinates_and_not_a_symmetry_cell():
    dialog = BuildEmbeddingDialog(host())
    # Grid labels are x columns and y rows, even though y increases upwards.
    offsets = dialog._inplane_offsets()
    column = offsets.index(0)
    row = len(offsets) - 1 - offsets.index(-1)
    dialog.cells.setCurrentCell(row, column)
    assert dialog._selected_cell == (0, -1, 0)
    assert dialog._selected_site == HostSite(0, (0, -1, 0))
    assert HostSite(0, (-1, -1, 0)) not in dialog._highlighted
    current = dialog.cells.item(row, column)
    assert current.data(Qt.ItemDataRole.UserRole) == (0, -1, 0)
    assert current.background().color().name() == "#e5bd68"


def test_local_preview_contains_only_regular_sites_in_one_host_cell():
    from ase2sprkkr import SPRKKRAtoms

    parent = Atoms(
        "Cu", positions=[[0, 0, 0]], cell=[2, 2, 12],
        pbc=(True, True, False)
    )
    special = HostSite(0, (1, 0, 0, 0, 1))
    cluster = SPRKKRAtoms.from_embedding(
        parent, [(special.original_site, special.cell_index, "Fe")]
    )
    dialog = BuildEmbeddingDialog(cluster)
    dialog._selected_cell = (1, 0)
    assert special in dialog.draft.cell_sites((1, 0))  # still editable in table
    dialog._render_local()
    assert dialog._local_sites == [HostSite(0, (1, 0))]
    assert special not in dialog._local_sites


def test_global_whole_mode_includes_all_cluster_sites_and_has_padded_bounds():
    dialog = BuildEmbeddingDialog(host())
    left = HostSite(0, (-3, 1, 0))
    right = HostSite(0, (4, -2, 1))
    dialog.draft.include([left, right])
    dialog.global_scope.setCurrentIndex(0)
    dialog._render_global()
    assert set(dialog.draft.entries).issubset(set(dialog._rendered_sites))
    assert "Included: 2/2" in dialog.preview_info.text()
    bounds = [dialog.axes.get_xlim(), dialog.axes.get_ylim(), dialog.axes.get_zlim()]
    for site in (left, right):
        position = site.position(dialog.draft.host)
        assert all(lo < coordinate < hi for coordinate, (lo, hi) in zip(position, bounds))


def test_cell_actions_are_adjacent_to_cell_grid():
    dialog = BuildEmbeddingDialog(host())
    panel = dialog.cells.parentWidget()
    actions = panel.findChildren(QPushButton)
    assert {w.text() for w in actions} >= {"Include", "Exclude", "Set centre"}


def test_displacement_is_debounced_and_flushed_before_changing_selected_site():
    dialog = BuildEmbeddingDialog(
        Atoms("Cu2", positions=[[0, 0, 0], [1, 1, 1]],
              cell=[2, 2, 2], pbc=True)
    )
    original = dialog._selected_site
    assert original == dialog._site_rows[0]
    dialog.deltas[0].setValue(0.12)
    assert dialog._displacement_timer.isActive()
    assert original not in dialog.draft.entries
    dialog.sites.selectRow(1)
    assert not dialog._displacement_timer.isActive()
    assert dialog.draft.entries[original].displacement[0] == pytest.approx(0.12)
    assert dialog._selected_site == dialog._site_rows[1]


def test_restore_occupation_is_shared_and_hidden_when_none():
    from ase2sprkkr_gui.gui.widgets.structures.occupation import OccupationEditor
    control = OccupationEditor(require_full=True)
    assert not control.restore_btn.isVisible()
    control.set_occupation({"Fe": 1.0})
    control.set_initial_occupation({"Cu": 1.0})
    assert not control.restore_btn.isHidden()
    control.restore_initial()
    assert control.occupation() == {"Cu": 1.0}


def test_embedding_restore_original_occupation_is_in_shared_widget():
    dialog = BuildEmbeddingDialog(host())
    assert not dialog.occupation.restore_btn.isHidden()
    dialog.occupation.rows[0]["elem"].setText("Fe")
    assert dialog.draft.entries[dialog.draft.center].occupation == {"Fe": 1.0}
    dialog.occupation.restore_btn.click()
    assert dialog.draft.entries[dialog.draft.center].occupation == {"Cu": 1.0}


def test_bulk_restore_original_occupation_restores_model_values():
    from ase2sprkkr_gui.gui.dialogs.structures.element_assignment import (
        ElementAssignmentDialog,
    )

    parent = host()
    parent.info["occupancy"] = {"0": {"Cu": 1.0}}
    parent.set_array("spacegroup_kinds", np.array([0]))
    editor = ElementAssignmentDialog()
    editor.setup(parent)
    row = editor._letter_widgets[0]
    assert not row.occupation_editor.restore_btn.isHidden()
    row.occupation_editor.rows[0]["elem"].setText("Fe")
    assert editor._draft.sites[0].occupancies[0].symbol == "Fe"
    row.occupation_editor.restore_initial()
    assert editor._draft.sites[0].occupancies[0].symbol == "Cu"
