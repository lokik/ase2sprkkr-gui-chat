"""Transactional Structure Editor and its conditional pages."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ase2sprkkr_gui-test-matplotlib")

import numpy as np
from ase import Atoms
from PyQt6.QtWidgets import QApplication

from ase2sprkkr.sprkkr.sprkkr_atoms import SPRKKRAtoms
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.dialogs.structures import structure_editor as editor_module
from ase2sprkkr_gui.gui.flows import structures as structure_flows


def _atoms():
    atoms = Atoms(
        "Fe2", scaled_positions=((0, 0, 0), (0.5, 0.5, 0.5)),
        cell=np.eye(3), pbc=True,
    )
    atoms.set_array("spacegroup_kinds", np.array([0, 0]))
    atoms.set_array("labels", np.array(["a.1", "a.2"], dtype=object))
    atoms.info["occupancy"] = {"0": {"Fe": 1.0}}
    return atoms


def _cluster():
    host = Atoms("Fe2", positions=[[0, 0, 0], [1, 1, 1]],
                 cell=[2, 2, 4], pbc=True)
    atoms = SPRKKRAtoms.from_embedding(
        host, [(0, (0, 0, 0), "Co"), (1, (1, 0, 0), "Ni")]
    )
    atoms.sites[0].embedding.displacement = (0.04, -0.01, 0.02)
    return atoms


def _dialog(atoms=None, **kwargs):
    _app = QApplication.instance() or QApplication([])
    return editor_module.StructureEditorDialog(
        _atoms() if atoms is None else atoms, **kwargs
    )


def test_overview_first_and_advanced_removed_with_shared_preview():
    dialog = _dialog()
    assert dialog.windowTitle() == "Structure Editor"
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "Overview", "Sites", "Lattice", "Transform"
    ]
    assert dialog.tabs.currentIndex() == 0
    assert dialog.overview.site_table.rowCount() == 2
    assert not dialog.tabs.tabIcon(0).isNull()
    assert dialog.tabs.iconSize().width() >= 20
    assert dialog.editor_splitter.widget(1).isAncestorOf(dialog.canvas)
    assert not hasattr(dialog, "splitter")
    dialog.close()


def test_build_derived_in_transform_not_advanced():
    dialog = _dialog()
    dialog.tabs.setCurrentIndex(3)
    assert not dialog.derived_group.isHidden()
    assert dialog.build_surface_button.isEnabled() is False  # no recent files
    assert dialog.build_2d_button.isEnabled() is False
    assert dialog.embedding_button.isEnabled() is False  # capability not passed
    dialog.close()


def test_noop_and_cancel_do_not_modify_source():
    source = _atoms()
    dialog = _dialog(source)
    dialog._on_ok()
    assert dialog._result is None
    dialog.close()
    dialog = _dialog(source)
    dialog._draft.split_site(dialog._draft.sites[0])
    dialog.reject()
    assert source.info["occupancy"] == {"0": {"Fe": 1.0}}
    np.testing.assert_array_equal(source.get_array("spacegroup_kinds"), [0, 0])
    dialog.close()


def test_site_and_lattice_edits_commit_together():
    source = _atoms()
    dialog = _dialog(source)
    dialog._letter_widgets[0].break_btn.click()
    dialog.tabs.setCurrentIndex(2)
    dialog.lattice_spinboxes[0][0].setValue(2.0)
    dialog._on_ok()
    result = dialog._result
    assert result is not None and result is not source
    np.testing.assert_allclose(result.cell[0, 0], 2.0)
    np.testing.assert_array_equal(result.get_array("spacegroup_kinds"), [0, 1])
    np.testing.assert_allclose(source.cell[0, 0], 1.0)
    dialog.close()


def test_transform_uses_staged_edit_and_no_early_publication(monkeypatch):
    source = _atoms()
    seen = []
    def scale(candidate, *, parent=None):
        seen.append(candidate)
        np.testing.assert_allclose(candidate.cell[0, 0], 2.0)
        out = candidate.copy()
        out.set_cell([3, 1, 1], scale_atoms=True)
        return out
    monkeypatch.setattr(editor_module, "scale_atoms", scale)
    dialog = _dialog(source)
    dialog.lattice_spinboxes[0][0].setValue(2.0)
    dialog.scale_button.click()
    assert len(seen) == 1
    np.testing.assert_allclose(dialog._draft.cell[0, 0], 3.0)
    np.testing.assert_allclose(source.cell[0, 0], 1.0)
    dialog._on_ok()
    np.testing.assert_allclose(dialog._result.cell[0, 0], 3.0)
    dialog.close()


def test_cancel_transform_keeps_staged_lattice_edit(monkeypatch):
    monkeypatch.setattr(editor_module, "scale_atoms", lambda atoms, parent=None: None)
    dialog = _dialog()
    dialog.lattice_spinboxes[0][0].setValue(2.0)
    dialog.scale_button.click()
    np.testing.assert_allclose(dialog._draft.cell[0, 0], 2.0)
    dialog.close()


def test_embedded_page_edits_host_and_hides_shared_preview():
    atoms = _cluster()
    dialog = _dialog(atoms, allow_build_embedding=True)
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "Overview", "Sites", "Lattice", "Transform", "Embedded"
    ]
    assert dialog.tabs.currentIndex() == 0
    assert dialog.scroll.isEnabled()
    assert dialog.lattice_spinboxes[0][0].isEnabled()
    assert dialog.scale_button.isEnabled()
    assert dialog.repeat_button.isEnabled()
    assert not dialog.rotate_button.isEnabled()
    assert all(not row.break_btn.isEnabled() for row in dialog._letter_widgets)
    assert dialog._embedded_editor is not None
    dialog.tabs.setCurrentIndex(4)
    assert dialog._shared_preview.isHidden()
    dialog.tabs.setCurrentIndex(0)
    assert not dialog._shared_preview.isHidden()
    dialog.close()


def test_editing_host_lattice_rebases_cluster_displacement_without_touching_original():
    atoms = _cluster()
    original = atoms.positions.copy()
    dialog = _dialog(atoms)
    dialog.tabs.setCurrentIndex(2)
    dialog.lattice_spinboxes[0][0].setValue(4.0)
    candidate = dialog._candidate()
    assert candidate is not atoms
    assert candidate.embedding_host is not atoms.embedding_host
    np.testing.assert_allclose(candidate.sites[0].embedding.displacement,
                               [0.08, -0.01, 0.02])
    np.testing.assert_allclose(atoms.positions, original)
    dialog._on_ok()
    assert dialog._result is not None
    dialog.close()


def test_newly_built_embedded_structure_adds_and_selects_page():
    dialog = _dialog()
    cluster = _cluster()
    assert dialog._accept_operation_result(cluster, dialog._working_atoms)
    assert dialog.tabs.currentIndex() == 4
    assert dialog.tabs.tabText(4) == "Embedded"
    assert dialog._embedded_editor is not None
    dialog.close()


def test_0d_transform_rejects_unknown_site_mapping():
    dialog = _dialog(_cluster())
    assert not dialog.rotate_button.isEnabled()
    assert "mapping" in dialog.rotate_button.toolTip()
    dialog.close()


def test_guided_edit_commits_accepted_result_once(monkeypatch):
    _app = QApplication.instance() or QApplication([])
    controller = WorkspaceController()
    source = _atoms()
    controller.replace_structure(source)
    accepted = source.copy()

    def select(atoms, parent=None, **kwargs):
        assert atoms is source
        assert kwargs["allow_build_2d"] == controller.can_build_2d()
        return accepted

    monkeypatch.setattr(editor_module, "select_structure_editor", select)
    assert structure_flows.edit_structure(controller, None)
    assert controller.atoms is accepted
    assert controller.atoms is not source


def test_guided_cancel_keeps_workspace_revision(monkeypatch):
    _app = QApplication.instance() or QApplication([])
    controller = WorkspaceController()
    source = _atoms()
    controller.replace_structure(source)
    generation = controller.structure_generation
    monkeypatch.setattr(editor_module, "select_structure_editor", lambda *_a, **_k: None)
    assert not structure_flows.edit_structure(controller, None)
    assert controller.atoms is source
    assert controller.structure_generation == generation



def test_overview_reports_hall_number_for_3d_and_left_right_bulk_for_2d():
    from ase.build import bulk
    from ase2sprkkr.sprkkr.build import semiinfinite_system
    dialog = _dialog()
    assert "Space group:" in dialog.overview.symmetry.text()
    assert "Hall symbol:" in dialog.overview.symmetry.text()
    assert "No." in dialog.overview.symmetry.text()
    dialog.close()

    slab = semiinfinite_system(bulk("Cu", "sc", a=2.0), (0, 0))
    dialog = _dialog(slab)
    assert not dialog.overview.bulk_boxes["left"][0].isHidden()
    assert not dialog.overview.bulk_boxes["right"][0].isHidden()
    left_text = dialog.overview.bulk_boxes["left"][1].text()
    right_text = dialog.overview.bulk_boxes["right"][1].text()
    assert "Space group:" in left_text
    assert "Space group:" in right_text
    assert "Hall symbol:" in left_text
    assert "Hall symbol:" in right_text
    dialog.close()
