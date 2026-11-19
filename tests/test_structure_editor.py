"""Qt behavior of the integrated structure editor in Guided mode."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ase2sprkkr_gui-test-matplotlib")

import numpy as np
from ase import Atoms
from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.application.workspace_controller import (
    WorkspaceController,
)
from ase2sprkkr_gui.gui.dialogs.structures import structure_editor as editor_module
from ase2sprkkr_gui.gui.flows import structures as structure_flows


def _atoms():
    atoms = Atoms(
        "Fe2",
        scaled_positions=((0.0, 0.0, 0.0), (0.5, 0.5, 0.5)),
        cell=np.eye(3),
        pbc=True,
    )
    atoms.set_array("spacegroup_kinds", np.array([0, 0]))
    atoms.set_array("labels", np.array(["a.1", "a.2"], dtype=object))
    atoms.info["occupancy"] = {"0": {"Fe": 1.0}}
    return atoms


def _dialog(atoms=None, **kwargs):
    _app = QApplication.instance() or QApplication([])
    return editor_module.StructureEditorDialog(
        _atoms() if atoms is None else atoms, **kwargs
    )


def test_editor_defaults_to_integrated_occupations_with_shared_preview():
    dialog = _dialog()
    assert dialog.windowTitle() == "Structure Editor"
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "Sites & occupations", "Lattice", "Transform", "Advanced"
    ]
    assert dialog.tabs.currentIndex() == 0
    assert dialog._letter_widgets
    assert dialog.canvas.parentWidget() is not None
    assert dialog.editor_splitter.widget(1).isAncestorOf(dialog.canvas)
    assert not hasattr(dialog, "splitter")  # no temporary splitter to reparent
    dialog.close()


def test_noop_apply_has_no_result_and_cancel_preserves_original():
    atoms = _atoms()
    dialog = _dialog(atoms)
    dialog._on_ok()
    assert dialog._result is None
    dialog.close()

    dialog = _dialog(atoms)
    dialog._draft.split_site(dialog._draft.sites[0])
    dialog.reject()
    assert atoms.info["occupancy"] == {"0": {"Fe": 1.0}}
    np.testing.assert_array_equal(atoms.get_array("spacegroup_kinds"), [0, 0])
    dialog.close()


def test_sites_and_lattice_are_applied_as_one_detached_structure():
    atoms = _atoms()
    dialog = _dialog(atoms)
    dialog._letter_widgets[0].break_btn.click()
    dialog.tabs.setCurrentIndex(1)
    dialog.lattice_spinboxes[0][0].setValue(2.0)
    dialog._on_ok()
    result = dialog._result
    assert result is not None and result is not atoms
    np.testing.assert_allclose(result.cell[0, 0], 2.0)
    np.testing.assert_array_equal(result.get_array("spacegroup_kinds"), [0, 1])
    np.testing.assert_allclose(atoms.cell[0, 0], 1.0)
    dialog.close()


def test_transform_uses_staged_edits_and_does_not_publish_early(monkeypatch):
    atoms = _atoms()
    observed = []

    def operation(candidate, *, parent=None):
        observed.append(candidate)
        assert candidate is not atoms
        np.testing.assert_allclose(candidate.cell[0, 0], 2.0)
        transformed = candidate.copy()
        transformed.set_cell([3.0, 1.0, 1.0], scale_atoms=True)
        return transformed

    monkeypatch.setattr(editor_module, "scale_atoms", operation)
    dialog = _dialog(atoms)
    dialog.lattice_spinboxes[0][0].setValue(2.0)
    dialog.scale_button.click()
    assert len(observed) == 1
    np.testing.assert_allclose(dialog._draft.cell[0, 0], 3.0)
    np.testing.assert_allclose(atoms.cell[0, 0], 1.0)
    dialog._on_ok()
    np.testing.assert_allclose(dialog._result.cell[0, 0], 3.0)
    dialog.close()


def test_cancelled_transform_keeps_uncommitted_lattice_edit(monkeypatch):
    monkeypatch.setattr(editor_module, "scale_atoms", lambda atoms, parent=None: None)
    dialog = _dialog()
    dialog.lattice_spinboxes[0][0].setValue(2.0)
    dialog.scale_button.click()
    np.testing.assert_allclose(dialog._draft.cell[0, 0], 2.0)
    dialog.close()


def test_cluster_disables_generic_transforms():
    atoms = _atoms()
    atoms.set_array("sprkkr_embedding", np.zeros((len(atoms), 6), dtype=int))
    dialog = _dialog(atoms)
    assert not dialog.scale_button.isEnabled()
    assert not dialog.repeat_button.isEnabled()
    assert not dialog.rotate_button.isEnabled()
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


def test_guided_edit_cancel_leaves_workspace_revision_untouched(monkeypatch):
    _app = QApplication.instance() or QApplication([])
    controller = WorkspaceController()
    source = _atoms()
    controller.replace_structure(source)
    generation = controller.structure_generation
    monkeypatch.setattr(
        editor_module, "select_structure_editor", lambda *_a, **_k: None
    )
    assert not structure_flows.edit_structure(controller, None)
    assert controller.atoms is source
    assert controller.structure_generation == generation


def test_cluster_generic_occupation_edit_is_disabled():
    atoms = _atoms()
    atoms.set_array("sprkkr_embedding", np.zeros((len(atoms), 6), dtype=int))
    dialog = _dialog(atoms)
    assert not dialog.scroll.isEnabled()
    assert dialog.tabs.currentIndex() == 3
    assert not dialog.site_notice.isHidden()
    assert not dialog.lattice_spinboxes[0][0].isEnabled()
    assert dialog.embedding_button.isEnabled() is False  # capability not passed
    dialog.close()


def test_cluster_with_incomplete_generic_occupation_still_supports_embedding():
    # The native embedding editor owns cluster occupation semantics. The
    # generic read-only draft must not prevent launching it or accepting no-op.
    atoms = _atoms()
    atoms.set_array("sprkkr_embedding", np.zeros((len(atoms), 6), dtype=int))
    atoms.info["occupancy"] = {"0": {"Fe": 0.0}}
    dialog = _dialog(atoms, allow_build_embedding=True)
    assert not dialog._draft.is_valid()
    assert dialog.ok_btn.isEnabled()
    assert dialog.embedding_button.isEnabled()
    assert dialog._candidate() is atoms
    dialog._on_ok()
    assert dialog._result is None
    dialog.close()
