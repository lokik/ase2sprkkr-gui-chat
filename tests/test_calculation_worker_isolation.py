from __future__ import annotations

from ase import Atoms
from ase2sprkkr.sprkkr.sprkkr_atoms import SPRKKRAtoms

from ase2sprkkr_gui.gui.application.sprkkr_execution import SprkkrExecution
from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController


class _Parameters:
    def copy(self, *, copy_values: bool):
        assert copy_values
        return _Parameters()


class _Cancellation:
    requested = False

    def register(self, _runner):
        return True


def test_execution_retains_plain_workspace_atoms_without_implicit_promotion(tmp_path):
    atoms = Atoms("Fe")
    controller = WorkspaceController(
        WorkspaceState(
            atoms=atoms,
            input_parameters=_Parameters(),
            directory=str(tmp_path),
        )
    )
    request = controller.create_calculation_request()

    execution = SprkkrExecution(request, _Cancellation())
    execution_atoms = execution.request.atoms

    assert request.atoms is atoms
    assert controller.atoms is atoms
    assert execution.request is request
    assert execution_atoms is atoms

    # Execution is read-only with respect to the published structure;
    # promotion or detachment belongs to an explicit editing operation.
    assert type(controller.atoms) is Atoms
    assert SPRKKRAtoms.sites_array_name not in controller.atoms.arrays


def test_execution_retains_authoritative_sprkkr_site_state(tmp_path):
    atoms = SPRKKRAtoms("Fe2", positions=((0, 0, 0), (1, 0, 0)), symmetry=False)
    atoms.sites[1].site_type = atoms.sites[0].site_type
    original_site_type = atoms.sites[0].site_type

    controller = WorkspaceController(
        WorkspaceState(
            atoms=atoms,
            input_parameters=_Parameters(),
            directory=str(tmp_path),
        )
    )
    execution = SprkkrExecution(
        controller.create_calculation_request(),
        _Cancellation(),
    )
    execution_atoms = execution.request.atoms

    assert execution_atoms is atoms
    assert execution_atoms.sites[0].site_type is execution_atoms.sites[1].site_type
    assert execution_atoms.sites[0].site_type is original_site_type
    assert execution_atoms.sites[0].site_type.atoms is atoms
    assert original_site_type.atoms is atoms
