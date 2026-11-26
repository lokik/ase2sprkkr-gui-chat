import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ase2sprkkr_gui-test-matplotlib")

from ase import Atoms
from PyQt6.QtWidgets import QApplication, QMessageBox

from ase2sprkkr_gui.gui.application.recent_files import RecentFiles
from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.flows import structures as structure_flows
from ase2sprkkr_gui.main import GuiApplication
from ase2sprkkr_gui.gui.workflow.model import Build2D, UpdateEmptySpheres


def test_controller_exposes_structure_operation_capabilities(monkeypatch):
    controller = WorkspaceController(WorkspaceState())
    assert not controller.can_build_2d()

    controller.replace_structure(
        Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
    )
    assert controller.can_build_2d()

    controller.replace_structure(
        Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=(True, True, False))
    )
    assert not controller.can_build_2d()

    monkeypatch.setattr(controller, "is_scf_converged", lambda: False)
    monkeypatch.setattr(controller, "scf_status", lambda: None)
    assert not controller.structure_change_requires_scf_restart()

    monkeypatch.setattr(controller, "scf_status", lambda: "START")
    assert not controller.structure_change_requires_scf_restart()

    monkeypatch.setattr(controller, "scf_status", lambda: "ITR")
    assert controller.structure_change_requires_scf_restart()

    monkeypatch.setattr(controller, "scf_status", lambda: "CUSTOM-STAGE")
    assert controller.structure_change_requires_scf_restart()

    monkeypatch.setattr(controller, "is_scf_converged", lambda: True)
    assert controller.structure_change_requires_scf_restart()


def test_restarted_workspace_does_not_require_another_scf_reset(monkeypatch):
    controller = WorkspaceController(
        WorkspaceState(atoms=Atoms("Fe"), restarted=True)
    )
    monkeypatch.setattr(controller, "is_scf_converged", lambda: True)
    monkeypatch.setattr(controller, "scf_status", lambda: "ITR")

    assert not controller.structure_change_requires_scf_restart()


def test_build2d_flow_rejects_non_3d_structure(tmp_path, monkeypatch):
    _application = QApplication.instance() or QApplication([])
    controller = WorkspaceController(
        WorkspaceState(
            atoms=Atoms(
                "Fe",
                cell=(2.8, 2.8, 2.8),
                pbc=(True, True, False),
            )
        )
    )
    recent_files = RecentFiles(tmp_path / "recent.json")
    selected = []
    messages = []

    monkeypatch.setattr(
        structure_flows,
        "select_build_2d_structure",
        lambda *args, **kwargs: selected.append((args, kwargs)),
    )
    monkeypatch.setattr(
        structure_flows.QMessageBox,
        "information",
        lambda *args: messages.append(args),
    )

    assert structure_flows.build_2d_structure(
        controller,
        recent_files,
        None,
    ) is None
    assert selected == []
    assert messages
    assert "3D periodic bulk" in messages[0][2]


def _commands(workflow):
    return tuple(
        action.command
        for group in workflow._actions.plan.groups
        for action in group.actions
    )


def test_workflow_hides_empty_spheres_when_scf_restart_is_required(
    tmp_path, monkeypatch
):
    application = QApplication.instance() or QApplication([])
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json",
                         settings_path=tmp_path / "settings.json")
    workflow = gui.create_workflow_window()
    controller = gui.controller
    controller.replace_structure(
        Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
    )
    monkeypatch.setattr(controller, "is_scf_converged", lambda: False)
    monkeypatch.setattr(controller, "scf_status", lambda: "START")
    workflow._refresh()
    assert any(isinstance(command, UpdateEmptySpheres)
               for command in _commands(workflow))

    monkeypatch.setattr(controller, "scf_status", lambda: "CUSTOM-STAGE")
    workflow._refresh()
    assert controller.structure_change_requires_scf_restart()
    assert not any(isinstance(command, UpdateEmptySpheres)
                   for command in _commands(workflow))
    workflow.close()


def test_workflow_build2d_action_uses_shared_capability(tmp_path):
    application = QApplication.instance() or QApplication([])
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json",
                         settings_path=tmp_path / "settings.json")
    workflow = gui.create_workflow_window()

    gui.controller.replace_structure(
        Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
    )
    workflow._refresh()
    assert any(isinstance(command, Build2D) for command in _commands(workflow))

    gui.controller.replace_structure(
        Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=(True, True, False))
    )
    workflow._refresh()
    assert not any(isinstance(command, Build2D) for command in _commands(workflow))
    workflow.close()


def test_empty_sphere_warning_does_not_preemptively_reset_scf(monkeypatch):
    _application = QApplication.instance() or QApplication([])
    controller = WorkspaceController(WorkspaceState(atoms=Atoms("Fe")))
    monkeypatch.setattr(
        controller, "structure_change_requires_scf_restart", lambda: True
    )
    generation = controller.structure_generation

    monkeypatch.setattr(
        structure_flows,
        "select_empty_spheres_parameters",
        lambda *, initial, parent: {},
    )
    monkeypatch.setattr(
        structure_flows.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        structure_flows.QMessageBox,
        "information",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        structure_flows,
        "add_empty_spheres",
        lambda atoms, **kwargs: atoms,
    )

    assert structure_flows.update_empty_spheres(controller, None)
    assert not controller.restarted
    assert controller.structure_generation == generation.next()


def test_empty_sphere_warning_can_cancel_before_search(monkeypatch):
    _application = QApplication.instance() or QApplication([])
    controller = WorkspaceController(WorkspaceState(atoms=Atoms("Fe")))
    monkeypatch.setattr(
        controller, "structure_change_requires_scf_restart", lambda: True
    )
    searched = []

    monkeypatch.setattr(
        structure_flows,
        "select_empty_spheres_parameters",
        lambda *, initial, parent: {},
    )
    monkeypatch.setattr(
        structure_flows.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    monkeypatch.setattr(
        structure_flows,
        "add_empty_spheres",
        lambda *args, **kwargs: searched.append((args, kwargs)),
    )

    assert not structure_flows.update_empty_spheres(controller, None)
    assert searched == []
