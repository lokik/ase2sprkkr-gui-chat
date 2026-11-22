"""Reuse a native POTFIL by identity, not by re-serializing Atoms."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from ase import Atoms
from ase2sprkkr import InputParameters
from kkrunner.backends.contracts import SubmissionNotStarted

from ase2sprkkr_gui.gui.application.execution_cancellation import RunCancellation
from ase2sprkkr_gui.gui.application.result_loading import PotentialVersionChanged
from ase2sprkkr_gui.gui.application.sprkkr_execution import SprkkrExecution
from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.flows.calculation import _one_off_task_request


def _file_backed_workspace(tmp_path):
    source = tmp_path / "Fe2.pot_new"
    source.write_bytes(b"opaque native POTFIL bytes that must remain untouched\n")
    atoms = Atoms("Fe2", positions=[(0, 0, 0), (1.5, 0, 0)], cell=(3, 3, 3), pbc=True)
    parameters = InputParameters.create("SPLEED")
    parameters.CONTROL.DATASET = "Fe2"
    controller = WorkspaceController(
        WorkspaceState(
            atoms=atoms,
            potential_path=str(source),
            input_parameters=parameters,
            directory=str(tmp_path),
        )
    )
    assert controller.mark_structure_saved(controller.structure_generation)
    return source, controller


def _execution(request):
    return SprkkrExecution(request, RunCancellation())


def test_regular_task_uses_original_scf_pot_new_without_rewriting(tmp_path):
    source, controller = _file_backed_workspace(tmp_path)
    original = source.read_bytes()
    request = controller.create_calculation_request()
    assert request.reusable_potential_version is not None

    execution = _execution(request)
    assert execution._input_source("Fe2.pot") == {"potential": str(source)}
    assert execution.file_names() == ("Fe2_%T.inp", "Fe2.pot", "Fe2_%T.out")
    assert source.read_bytes() == original


def test_native_input_references_original_bytes_without_generating_fe2_pot(tmp_path):
    source, controller = _file_backed_workspace(tmp_path)
    parameters = InputParameters.create("DOS")
    parameters.CONTROL.DATASET = "Fe2"
    request = replace(controller.create_calculation_request(), input_parameters=parameters)
    execution = _execution(request)
    content, mtime = source.read_bytes(), source.stat().st_mtime_ns

    _written, input_name, potential_name, _output_name = execution.prepare_files()
    assert Path(potential_name) == source
    parsed_input = InputParameters.from_file(input_name)
    assert parsed_input.CONTROL.POTFIL() == source.name
    assert source.read_bytes() == content
    assert source.stat().st_mtime_ns == mtime
    assert not (tmp_path / "Fe2.pot").exists()


def test_guided_spleed_inherits_the_exact_potential_version(tmp_path):
    source, controller = _file_backed_workspace(tmp_path)
    request = _one_off_task_request(
        controller,
        InputParameters.create("SPLEED"),
        expected_structure_generation=controller.structure_generation,
        parent=None,
    )
    assert request is not None
    assert request.reusable_potential_version == controller.reusable_potential_version()
    assert _execution(request)._input_source("Fe2.pot") == {"potential": str(source)}


def test_structure_edit_invalidates_direct_file_reuse(tmp_path):
    _source, controller = _file_backed_workspace(tmp_path)
    def change_geometry(atoms):
        atoms.positions[0, 0] += 0.2
        return atoms
    controller.apply_structure_edit(
        change_geometry, expected_structure_generation=controller.structure_generation
    )
    assert controller.reusable_potential_version() is None
    request = controller.create_calculation_request()
    assert request.reusable_potential_version is None
    assert _execution(request)._input_source("Fe2.pot") == {
        "atoms": request.atoms, "potential_file": "Fe2.pot"
    }


def test_restart_scf_never_bypasses_potential_serialization(tmp_path):
    _source, controller = _file_backed_workspace(tmp_path)
    controller.use_for_new_calculation()
    assert controller.restarted
    assert controller.reusable_potential_version() is None
    request = controller.create_calculation_request()
    assert request.restart_scf
    assert _execution(request)._input_source("Fe2.pot") == {
        "atoms": request.atoms, "potential_file": "Fe2.pot"
    }


def test_modified_source_is_rejected_before_any_files_are_written(tmp_path):
    source, controller = _file_backed_workspace(tmp_path)
    execution = _execution(controller.create_calculation_request())
    source.write_bytes(b"some other potential file, now a different version\n")
    with pytest.raises(PotentialVersionChanged, match="Source potential changed"):
        execution._input_source("Fe2.pot")


def test_staged_manifest_must_match_original_potential_version(tmp_path):
    source, controller = _file_backed_workspace(tmp_path)
    request = controller.create_calculation_request()
    version = request.reusable_potential_version
    assert version is not None
    execution = _execution(request)
    good_manifest = SimpleNamespace(input_version=lambda path: version if path == source else None)
    execution._validate_file_input(good_manifest)

    missing_manifest = SimpleNamespace(input_version=lambda path: None)
    with pytest.raises(SubmissionNotStarted, match="does not match"):
        execution._validate_file_input(missing_manifest)

    # Even a correctly staged manifest cannot authorize bytes changed afterwards.
    source.write_bytes(b"replaced potential after staging\n")
    with pytest.raises(SubmissionNotStarted, match="does not match"):
        execution._validate_file_input(good_manifest)


def test_explicit_potential_is_not_overridden_by_workspace_file(tmp_path):
    _source, controller = _file_backed_workspace(tmp_path)
    request = replace(controller.create_calculation_request(), potential="other.pot")
    execution = _execution(request)
    assert execution._input_source("new.pot") == {
        "potential": "other.pot", "potential_file": "new.pot"
    }
