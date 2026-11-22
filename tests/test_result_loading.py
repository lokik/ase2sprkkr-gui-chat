from __future__ import annotations

from types import SimpleNamespace

import pytest
from ase import Atoms

from ase2sprkkr_gui.gui.application import result_loading as result_loading_module
from ase2sprkkr_gui.gui.application.result_loading import load_result, load_result_file
from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController


def test_embedded_scf_loads_new_clupot_instead_of_registered_start(tmp_path):
    start = tmp_path / "cluster.clupot"
    produced = tmp_path / "cluster.clupot_new"
    start.touch()
    produced.touch()
    output_atoms = Atoms("Fe")
    result = SimpleNamespace(
        files={"potential": start.name},
        directory=str(tmp_path),
        task_name="scf",
        is_embedded=True,
        potential_filename=produced.name,
        potential=SimpleNamespace(atoms=output_atoms),
    )
    result.path_to = lambda key: result.files[key]

    loaded = load_result(result, fallback_directory=tmp_path)

    assert loaded.potential_path == produced.resolve()
    assert loaded.atoms is output_atoms
    assert loaded.potential_error is None




def test_embedded_dos_resolves_cluster_potential_not_registered_host(tmp_path):
    """The DOS FILES potential is the host; the reusable state is CLUPOT."""
    host = tmp_path / "host.pot"
    cluster = tmp_path / "cluster.pot"
    host.touch()
    cluster.touch()
    cluster_atoms = Atoms("Fe")
    result = SimpleNamespace(
        task_name="dos",
        is_embedded=True,
        files={"potential": host.name},
        directory=str(tmp_path),
        potential_filename=cluster.name,
        potential=SimpleNamespace(atoms=cluster_atoms),
    )
    result.path_to = lambda key: result.files[key]

    loaded = load_result(result, fallback_directory=tmp_path)

    assert loaded.potential_path == cluster.resolve()
    assert loaded.potential_path != host.resolve()
    assert loaded.atoms is cluster_atoms
    assert loaded.potential_error is None


def test_native_dos_result_selects_control_potfil_clu(tmp_path, monkeypatch):
    """The real DOS result resolves its CLUPOT despite a host FILES entry."""
    from ase2sprkkr import InputParameters
    from ase2sprkkr.outputs.readers.dos import DosResult

    parameters = InputParameters.create("DOS")
    parameters.CONTROL.CLUTYPE = "embedded"
    parameters.CONTROL.POTFIL = "host.pot"
    parameters.CONTROL.POTFIL_CLU = "cluster.clu"
    # The native result requires a concrete host file for provenance,
    # even when this test stubs out potential parsing itself.
    (tmp_path / "host.pot").write_text("host placeholder")
    result = DosResult(parameters, None, tmp_path, task_name="dos")
    result.files.add_file("potential", "host.pot")
    parsed_atoms = Atoms("Fe")
    monkeypatch.setattr(
        result, "_read_potential",
        lambda path: SimpleNamespace(atoms=parsed_atoms),
    )

    loaded = load_result(result, fallback_directory=tmp_path)

    assert result.is_embedded
    assert loaded.potential_path == (tmp_path / "cluster.clu").resolve()
    assert loaded.atoms is parsed_atoms


def test_embedded_dos_does_not_use_host_when_cluster_reference_is_missing(tmp_path):
    """A host file must never masquerade as the embedded result's geometry."""
    host = tmp_path / "host.pot"
    host.touch()
    result = SimpleNamespace(
        task_name="dos",
        is_embedded=True,
        files={"potential": host.name},
        directory=str(tmp_path),
        potential_filename=None,
    )
    result.path_to = lambda key: result.files[key]

    loaded = load_result(result, fallback_directory=tmp_path)

    assert loaded.potential_path is None
    assert loaded.atoms is None


def test_imported_embedded_dos_restores_cluster_and_offers_edit_not_build(
    tmp_path, monkeypatch,
):
    """Imported DOS history reopens the original cluster, never its periodic host."""
    from ase.build import bulk
    from ase2sprkkr import SPRKKRAtoms
    from ase2sprkkr_gui.ase.embedding import EmbeddingDraft
    from ase2sprkkr_gui.gui.application import historical_state
    from ase2sprkkr_gui.gui.workflow.model import BuildEmbedding, WorkflowSection
    from ase2sprkkr_gui.gui.workflow.planner import WorkflowPlanner

    host_potential = SPRKKRAtoms.promote_ase_atoms(
        bulk("Fe", "bcc", a=2.87, cubic=True)
    ).potential
    host_potential.SCF_INFO.SCFSTATUS = "CONVERGED"
    draft = EmbeddingDraft(host_potential)
    draft.include([draft.center])
    embedded_atoms = draft.to_atoms()

    host_file = tmp_path / "host.pot"
    cluster_file = tmp_path / "cluster.clu"
    host_file.touch()
    cluster_file.touch()
    result = SimpleNamespace(
        task_name="dos",
        produces_potential=False,
        output_values={},
        is_embedded=True,
        files={"potential": host_file.name},
        directory=str(tmp_path),
        potential_filename=cluster_file.name,
        potential=SimpleNamespace(atoms=embedded_atoms),
        input_parameters=None,
    )
    result.path_to = lambda key: result.files[key]

    loaded = load_result(result, fallback_directory=tmp_path)
    controller = WorkspaceController(WorkspaceState())
    record = controller.adopt_external_result(loaded).record
    assert record is not None
    state = controller.result_store.state_for_result(record)
    assert state is not None
    assert state.potential_path == str(cluster_file.resolve())

    def read_state(version, *, embedding_host):
        assert version.path == cluster_file.resolve()
        assert embedding_host is host_potential
        return embedded_atoms

    monkeypatch.setattr(historical_state, "load_potential_version", read_state)
    controller.use_result_state(record.id)

    assert controller.structure_kind() == "0d"
    assert controller.atoms.embedding_host is host_potential
    actions = WorkflowPlanner().for_workspace(controller).group(WorkflowSection.CREATE)
    assert actions is not None
    assert next(
        action.title for action in actions.actions
        if action.command == BuildEmbedding()
    ) == "Edit Embedded Cluster"


def test_scf_output_without_converged_registration_overrides_input(tmp_path, monkeypatch):
    start = tmp_path / "Fe.pot"
    produced = tmp_path / "Fe.pot_new"
    start.touch()
    produced.touch()
    output_atoms = Atoms("Fe")

    def read(path, *, check_file_unchanged):
        assert check_file_unchanged
        assert path == str(produced.resolve())
        return SimpleNamespace(atoms=output_atoms)

    monkeypatch.setattr(result_loading_module.Potential, "from_file", staticmethod(read))
    result = SimpleNamespace(
        files={"potential": start.name},
        directory=str(tmp_path),
        task_name="scf",
        potential_filename=produced.name,
    )
    result.path_to = lambda key: result.files[key]

    loaded = load_result(result, fallback_directory=tmp_path)

    assert loaded.potential_path == produced.resolve()
    assert loaded.atoms is output_atoms


def test_result_loading_resolves_relative_artifacts_from_result_directory(
    tmp_path, monkeypatch
):
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    output = run_directory / "Fe.out"
    converged = run_directory / "Fe_converged.pot"
    fallback_potential = run_directory / "Fe.pot"
    converged.touch()
    fallback_potential.touch()
    atoms = Atoms("Fe")
    potential_reads = []
    parameters = object()
    result = SimpleNamespace(
        files={
            "output": output.name,
            "converged": converged.name,
            "potential": fallback_potential.name,
        },
        directory=str(run_directory),
        potential_filename=converged.name,
        input_parameters=parameters,
    )
    result.path_to = lambda key: result.files[key]

    def read_potential(path, *, check_file_unchanged):
        assert check_file_unchanged
        potential_reads.append(path)
        return SimpleNamespace(atoms=atoms)

    monkeypatch.setattr(
        result_loading_module.Potential,
        "from_file",
        staticmethod(read_potential),
    )

    loaded = load_result(result, fallback_directory=tmp_path / "unused-fallback")

    assert loaded.output_path == output.resolve()
    assert loaded.potential_path == converged.resolve()
    assert loaded.atoms is atoms
    assert loaded.potential_error is None
    assert loaded.directory == str(run_directory.resolve())
    assert loaded.input_parameters is parameters
    assert loaded.input_parameters_error is None
    assert potential_reads == [str(converged.resolve())]


def test_result_loading_keeps_input_parameter_parse_failure_nonfatal(tmp_path):
    error = ValueError("broken input")

    class Result:
        files = {}
        directory = str(tmp_path)
        output_file = None

        @property
        def input_parameters(self):
            raise error

    loaded = load_result(Result(), fallback_directory=tmp_path)

    assert loaded.input_parameters is None
    assert loaded.input_parameters_error is error


@pytest.mark.parametrize(
    ("files", "potential_filename", "expected_name"),
    [
        ({"potential": "registered.pot"}, "filename.pot", "filename.pot"),
        ({}, "filename.pot", "filename.pot"),
    ],
)
def test_result_loading_uses_result_potential_and_fallback_directory(
    tmp_path, monkeypatch, files, potential_filename, expected_name
):
    potential = tmp_path / expected_name
    potential.touch()
    atoms = Atoms("Fe")
    result = SimpleNamespace(
        files=files,
        directory=None,
        potential_filename=potential_filename,
    )
    result.path_to = lambda key: result.files[key]

    def read_potential(_path, *, check_file_unchanged):
        assert check_file_unchanged
        return SimpleNamespace(atoms=atoms)

    monkeypatch.setattr(
        result_loading_module.Potential,
        "from_file",
        staticmethod(read_potential),
    )

    loaded = load_result(result, fallback_directory=tmp_path)

    assert loaded.potential_path == potential.resolve()
    assert loaded.atoms is atoms
    assert loaded.potential_error is None


def test_result_loading_uses_output_file_before_explicit_output_fallback(tmp_path):
    result = SimpleNamespace(
        files={},
        directory=None,
        output_file="metadata.out",
    )

    loaded = load_result(
        result,
        fallback_directory=tmp_path,
        output_fallback=tmp_path / "external.out",
    )

    assert loaded.output_path == (tmp_path / "metadata.out").resolve()
    assert loaded.directory == str(tmp_path.resolve())


def test_result_loading_reports_a_potential_path_that_is_not_a_file(tmp_path):
    potential_directory = tmp_path / "potential.pot"
    potential_directory.mkdir()
    result = SimpleNamespace(
        files={"potential": potential_directory.name},
        directory=str(tmp_path),
        potential_filename=potential_directory.name,
    )
    result.path_to = lambda key: result.files[key]

    loaded = load_result(result, fallback_directory=None)

    assert loaded.potential_path == potential_directory.resolve()
    assert loaded.atoms is None
    assert isinstance(loaded.potential_error, IsADirectoryError)


def test_missing_output_path_is_advisory_provenance(tmp_path):
    missing_output = tmp_path / "missing.out"
    result = SimpleNamespace(
        files={"output": missing_output.name},
        directory=str(tmp_path),
    )
    result.path_to = lambda key: result.files[key]

    loaded = load_result(result, fallback_directory=tmp_path)

    assert loaded.output_path == missing_output.resolve()
    assert not missing_output.exists()
    assert loaded.potential_error is None


def test_missing_referenced_potential_is_nonfatal_and_does_not_replace_workspace(
    tmp_path, monkeypatch
):
    output = tmp_path / "external.out"
    output.touch()
    missing = tmp_path / "missing.pot"
    result = SimpleNamespace(
        task_name="scf",
        files={"output": output.name, "converged": missing.name},
        directory=str(tmp_path),
        produces_potential=True,
        potential_filename=missing.name,
    )
    result.path_to = lambda key: result.files[key]
    monkeypatch.setattr(
        result_loading_module.TaskResult,
        "from_file",
        staticmethod(lambda _path, **_kwargs: result),
    )
    original_atoms = Atoms("Cu")
    controller = WorkspaceController(
        WorkspaceState(atoms=original_atoms, potential_path="Cu.pot")
    )

    adoption = controller.adopt_external_result(load_result_file(output))

    assert adoption.potential_path == missing.resolve()
    assert isinstance(adoption.potential_error, FileNotFoundError)
    assert controller.result_store.results[0].result is result
    assert controller.atoms is original_atoms
    assert controller.potential_path == "Cu.pot"


def test_result_parse_failure_has_no_workspace_side_effects(tmp_path, monkeypatch):
    atoms = Atoms("Cu")
    workspace = WorkspaceState(
        atoms=atoms,
        directory="original",
        potential_path="Cu.pot",
    )
    controller = WorkspaceController(workspace)
    structure_generation = controller.structure_generation
    input_generation = controller.input_parameters_generation
    directory_generation = controller.directory_generation
    project_generation = controller.active_project_generation
    monkeypatch.setattr(
        result_loading_module.TaskResult,
        "from_file",
        staticmethod(lambda _path, **_kwargs: (_ for _ in ()).throw(ValueError("broken output"))),
    )

    (tmp_path / "broken.out").touch()
    with pytest.raises(ValueError, match="broken output"):
        load_result_file(tmp_path / "broken.out")

    assert controller.structure_generation == structure_generation
    assert controller.input_parameters_generation == input_generation
    assert controller.directory_generation == directory_generation
    assert controller.active_project_generation == project_generation
    assert controller.atoms is atoms
    assert controller.directory == "original"
    assert controller.potential_path == "Cu.pot"
    assert controller.result_store.results == ()


def test_native_cluster_result_requires_and_reuses_original_host(tmp_path, monkeypatch):
    from ase2sprkkr.potentials.cluster_potentials import ClusterPotential

    cluster_path = tmp_path / "after_scf.clu"
    cluster_path.write_text("GLOBAL SYSTEM PARAMETER\nNQCLU 1\n", encoding="utf-8")
    host = object()
    expected = Atoms("Fe")
    calls = []

    def parse(path, *, host):
        calls.append((path, host))
        return SimpleNamespace(atoms=expected)

    monkeypatch.setattr(ClusterPotential, "from_file", staticmethod(parse))
    result = SimpleNamespace(
        files={"converged": cluster_path.name},
        directory=str(tmp_path),
        potential_filename=cluster_path.name,
        input_parameters=None,
    )
    result.path_to = lambda key: result.files[key]

    missing = load_result(result, fallback_directory=tmp_path)
    assert missing.atoms is None
    assert "requires a host" in str(missing.potential_error)

    loaded = load_result(result, fallback_directory=tmp_path, embedding_host=host)
    assert loaded.atoms is expected
    assert loaded.potential_error is None
    assert calls == [(cluster_path.resolve(), host)]


def test_native_pot_extension_cluster_is_detected_by_header(tmp_path):
    from ase2sprkkr import Potential

    periodic = tmp_path / "bulk.pot"
    embedded = tmp_path / "cluster.pot_new"
    periodic.write_text("GLOBAL SYSTEM PARAMETER\nNQ 1\n", encoding="utf-8")
    embedded.write_text("GLOBAL SYSTEM PARAMETER\nNQCLU 3\n", encoding="utf-8")
    assert not Potential.is_cluster_file(periodic)
    assert Potential.is_cluster_file(embedded)
    assert not Potential.is_cluster_file(tmp_path / "missing.pot")


def test_imported_native_embedded_dos_preserves_host_version(tmp_path, native_embedded_potentials):
    """The reusable imported CLUPOT must retain its specific periodic host."""
    from ase2sprkkr import InputParameters
    from ase2sprkkr.outputs.readers.dos import DosResult
    from ase2sprkkr_gui.gui.application.historical_state import _restore_state_atoms
    from ase2sprkkr_gui.gui.application.result_loading import PotentialVersionChanged

    host, cluster = native_embedded_potentials

    parameters = InputParameters.for_task("DOS")
    parameters.CONTROL.CLUTYPE = "embedded"
    parameters.CONTROL.POTFIL = host.name
    parameters.CONTROL.POTFIL_CLU = cluster.name
    result = DosResult(parameters, None, tmp_path, task_name="dos")
    loaded = load_result(result, fallback_directory=tmp_path)
    assert loaded.potential_error is None
    assert loaded.atoms is not None
    assert len(loaded.source_versions) == 1
    assert loaded.source_versions[0].path == host
    assert loaded.source_versions[0].is_current()

    controller = WorkspaceController(WorkspaceState())
    record = controller.adopt_external_result(loaded).record
    state = controller.result_store.state_for_result(record)
    assert state.potential_path == str(cluster)
    assert state.source_dependencies == loaded.source_versions
    assert state.potential_version.path == cluster

    host.write_text("changed host potential")
    with pytest.raises(PotentialVersionChanged, match="changed"):
        _restore_state_atoms(state)


def test_imported_embedded_result_rejects_host_changed_during_loading(tmp_path, native_embedded_potentials, monkeypatch):
    """Do not attach a host version captured after the native potential was read."""
    from ase2sprkkr import InputParameters
    from ase2sprkkr.outputs.readers.dos import DosResult

    host, cluster = native_embedded_potentials

    parameters = InputParameters.for_task("DOS")
    parameters.CONTROL.CLUTYPE = "embedded"
    parameters.CONTROL.POTFIL = host.name
    parameters.CONTROL.POTFIL_CLU = cluster.name
    result = DosResult(parameters, None, tmp_path, task_name="dos")

    class ChangingPotential:
        @property
        def atoms(self):
            host.write_text("changed during import")
            return Atoms("Fe")

    monkeypatch.setattr(result, "_read_potential", lambda _: ChangingPotential())
    loaded = load_result(result, fallback_directory=tmp_path)
    assert loaded.atoms is None
    assert loaded.source_versions == ()
    assert "changed while" in str(loaded.potential_error)



def test_imported_embedded_result_uses_recorded_host_version(tmp_path, native_embedded_potentials):
    """Do not replace historical host evidence with a new disk snapshot."""
    from ase2sprkkr import InputParameters
    from ase2sprkkr.outputs.readers.dos import DosResult
    from kkrunner.files import FileEntry, FileManifest, FileVersion

    host, cluster = native_embedded_potentials

    params = InputParameters.for_task("DOS")
    params.CONTROL.CLUTYPE = "embedded"
    params.CONTROL.POTFIL = host.name
    params.CONTROL.POTFIL_CLU = cluster.name
    result = DosResult(params, None, tmp_path, task_name="dos")
    host_version = FileVersion.capture(host, require_digest=True)
    cluster_version = FileVersion.capture(cluster, require_digest=True)
    result.file_manifest = FileManifest(
        inputs=(FileEntry(local_path=str(host)), FileEntry(local_path=str(cluster))),
        input_versions=(host_version, cluster_version),
        captured_at="2026-11-20T00:00:00+00:00",
    )

    loaded = load_result(result, fallback_directory=tmp_path)
    assert loaded.potential_error is None
    assert loaded.source_versions == (host_version,)
    assert loaded.source_versions[0] is host_version


def test_batch_result_import_reuses_versioned_potential_and_invalidates(
    tmp_path, monkeypatch,
):
    from ase import Atoms
    from ase2sprkkr.outputs.task_result import TaskResult
    from ase2sprkkr_gui.gui.application import result_loading

    pot = tmp_path / "shared.pot"
    pot.write_text("first version")
    first, second, third = [tmp_path / name for name in (
        "first.out", "second.out", "third.out",
    )]
    for output in (first, second, third):
        output.touch()

    cache = result_loading.ResultImportCache()
    seen = []

    def fake_result(path, *, input_cache=None):
        assert input_cache is cache.inputs
        result = TaskResult(
            None, None, str(tmp_path), output_file=path.name, task_name="DOS"
        )
        result.files.add_file("potential", pot.name)
        result._input_cache = input_cache
        return result

    def fake_potential(_cls, path, *, atoms, check_file_unchanged, host=None):
        assert atoms is False
        assert check_file_unchanged
        seen.append(Path(path).resolve())
        return SimpleNamespace(atoms=Atoms("Fe", cell=(3, 3, 3), pbc=True))

    monkeypatch.setattr(
        result_loading.TaskResult, "from_file", staticmethod(fake_result)
    )
    monkeypatch.setattr(
        result_loading.Potential, "from_file", classmethod(fake_potential)
    )

    a = result_loading.load_result_file(first, cache=cache)
    b = result_loading.load_result_file(second, cache=cache)
    assert a.atoms is b.atoms
    assert seen == [pot.resolve()]

    pot.write_text("changed version, not the same bytes")
    c = result_loading.load_result_file(third, cache=cache)
    assert c.atoms is not b.atoms
    assert seen == [pot.resolve(), pot.resolve()]


def test_batch_embedded_cache_parses_host_and_clupot_once_without_atoms(
    tmp_path, native_embedded_potentials, monkeypatch,
):
    """Native file parsing and cluster-geometry construction have one batch owner."""
    import os

    from ase2sprkkr import InputParameters
    from ase2sprkkr.outputs.readers.dos import DosResult
    from ase2sprkkr_gui.gui.application.result_loading import ResultImportCache

    host, cluster = native_embedded_potentials
    params = InputParameters.for_task("DOS")
    params.CONTROL.CLUTYPE = "embedded"
    params.CONTROL.POTFIL = host.name
    params.CONTROL.POTFIL_CLU = cluster.name

    native_from_file = result_loading_module.Potential.from_file
    parsed = []

    def track(_cls, path, *args, **kwargs):
        parsed.append((str(path), kwargs.get("atoms")))
        assert kwargs["atoms"] is False
        return native_from_file(path, *args, **kwargs)

    monkeypatch.setattr(
        result_loading_module.Potential, "from_file", classmethod(track),
    )

    cache = ResultImportCache()

    def read():
        result = DosResult(params, None, tmp_path, task_name="dos")
        return load_result(result, fallback_directory=tmp_path, import_cache=cache)

    first, second = read(), read()
    assert first.potential_error is None
    assert second.potential_error is None
    assert first.atoms is second.atoms
    assert len(parsed) == 2  # host and CLUPOT, each parsed without Atoms
    assert set(path for path, _ in parsed) == {str(host), str(cluster)}

    # Even if CLUPOT is unchanged, replacing the host invalidates the
    # reconstructed cluster and the cached host parser.
    stat = host.stat()
    os.utime(host, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    third = read()
    assert third.potential_error is None
    assert third.atoms is not second.atoms
    assert len(parsed) == 4

    stat = cluster.stat()
    os.utime(cluster, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    fourth = read()
    assert fourth.potential_error is None
    assert fourth.atoms is not third.atoms
    assert len(parsed) == 5  # host reused, only changed CLUPOT is parsed


def test_embedded_scf_convergence_does_not_change_shared_clupot(
    tmp_path, native_embedded_potentials,
):
    from shutil import copyfile
    from pathlib import Path

    from ase2sprkkr import InputParameters
    from ase2sprkkr.outputs.readers.dos import DosResult
    from ase2sprkkr.outputs.readers.scf import ScfResult
    from ase2sprkkr_gui.gui.application.result_loading import ResultImportCache
    import ase2sprkkr

    host, cluster = native_embedded_potentials
    produced = tmp_path / (cluster.name + "_new")
    examples = Path(ase2sprkkr.__file__).parent / "potentials" / "examples"
    copyfile(examples / "Fe3CoFe3.clu.iterating.pot", produced)

    params = InputParameters.for_task("DOS")
    params.CONTROL.CLUTYPE = "embedded"
    params.CONTROL.POTFIL = host.name
    params.CONTROL.POTFIL_CLU = produced.name
    cache = ResultImportCache()
    dos_result = DosResult(params, None, tmp_path, task_name="dos")
    dos = load_result(dos_result, fallback_directory=tmp_path, import_cache=cache)
    assert dos.potential_error is None
    assert dos.atoms.potential.SCF_INFO.SCFSTATUS() == "ITERATING"

    scf_parameters = InputParameters.for_task("SCF")
    scf_parameters.CONTROL.CLUTYPE = "embedded"
    scf_parameters.CONTROL.POTFIL = host.name
    scf_parameters.CONTROL.POTFIL_CLU = cluster.name
    scf_result = ScfResult(scf_parameters, None, tmp_path)
    scf_result.return_code = 0
    scf_result.iterations = [{"converged": lambda: True}]
    scf = load_result(scf_result, fallback_directory=tmp_path, import_cache=cache)

    assert scf.potential_error is None
    assert scf.atoms is not dos.atoms
    assert scf.atoms.potential.SCF_INFO.SCFSTATUS() == "CONVERGED"
    assert scf.atoms.info["sprkkr_embedding_scf_status"] == "CONVERGED"
    assert dos.atoms.potential.SCF_INFO.SCFSTATUS() == "ITERATING"
    assert cache.loader.potential(produced, host=host).atoms is dos.atoms


def test_gui_disambiguation_consumes_per_file_association(tmp_path):
    from ase2sprkkr.outputs import FileAssociation
    from ase2sprkkr.outputs.task_result import TaskResult
    from ase2sprkkr_gui.gui.application.result_loading import (
        LoadedResult, PotentialProvenanceAmbiguous, disambiguate_scf_potentials,
    )

    potential = tmp_path / "Fe.pot_new"
    older, newer = tmp_path / "old.out", tmp_path / "new.out"
    result_old = TaskResult(None, None, tmp_path, output_file=older, task_name="SCF")
    result_old.produces_potential = True
    result_old.files.add_file("converged", potential)
    result_old.files["converged"].set_association(
        FileAssociation.NOT_THIS_RESULT, evidence="newer_scf_stdout",
        associated_with=newer,
    )
    result_new = TaskResult(None, None, tmp_path, output_file=newer, task_name="SCF")
    result_new.produces_potential = True
    result_new.files.add_file("converged", potential)
    result_new.files["converged"].set_association(
        FileAssociation.LIKELY, evidence="newest_scf_stdout"
    )

    def loaded(result, path):
        return LoadedResult(
            result=result, output_path=path, potential_path=potential,
            atoms=Atoms("Fe"), potential_error=None, directory=str(tmp_path),
        )

    entries = [(older, loaded(result_old, older)),
               (newer, loaded(result_new, newer))]
    resolved, ambiguous = disambiguate_scf_potentials(entries)
    assert ambiguous == ("old.out",)
    assert resolved[0][1].atoms is None
    assert isinstance(resolved[0][1].potential_error, PotentialProvenanceAmbiguous)
    assert resolved[1][1].atoms is entries[1][1].atoms


def test_standalone_scf_import_assigns_current_potential_to_newest_output(
    tmp_path, monkeypatch,
):
    import os
    from ase2sprkkr.outputs.task_result import TaskResult
    from ase2sprkkr_gui.gui.application.result_loading import PotentialProvenanceAmbiguous

    potential = tmp_path / "Fe.pot_new"
    potential.write_text("current")
    first, second = tmp_path / "one.out", tmp_path / "two.out"
    head = (
        " * KKRSCF VERSION 9.7 *\n"
        "TASK = SCF\n"
        "pot-file: Fe.pot_new\n"
    )
    first.write_text(head)
    second.write_text(head)
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(second, ns=(2_000_000_000, 2_000_000_000))

    class FakeSCF(TaskResult):
        produces_potential = True

        @property
        def potential_filename(self):
            return str(potential)

    def parsed(path, *, input_cache=None):
        return FakeSCF(None, None, tmp_path, output_file=path, task_name="SCF")

    monkeypatch.setattr(result_loading_module.TaskResult, "from_file", staticmethod(parsed))
    monkeypatch.setattr(
        FakeSCF, "_read_potential",
        lambda self, filename: SimpleNamespace(atoms=Atoms("Fe")),
    )
    older, newer = load_result_file(first), load_result_file(second)
    assert older.atoms is None
    assert isinstance(older.potential_error, PotentialProvenanceAmbiguous)
    assert newer.atoms is not None and newer.potential_error is None


def test_import_cache_reuses_file_digest_until_file_changes(tmp_path, monkeypatch):
    import os
    from ase2sprkkr_gui.gui.application.result_loading import ResultImportCache

    potential = tmp_path / "huge.pot"
    potential.write_text("first version")
    original = result_loading_module.PotentialFileVersion.capture
    called = []

    def capture(_cls, path, **kwargs):
        called.append((path, kwargs))
        return original(path, **kwargs)

    monkeypatch.setattr(
        result_loading_module.PotentialFileVersion, "capture", classmethod(capture),
    )
    cache = ResultImportCache()
    a = cache.version(potential)
    b = cache.version(potential)
    assert a is b
    assert len(called) == 1

    stat = potential.stat()
    potential.write_text("changed content")
    os.utime(potential, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    c = cache.version(potential)
    assert c != a
    assert len(called) == 2


def test_recent_batch_checks_newer_scf_even_when_not_imported(tmp_path, monkeypatch):
    import os
    from ase2sprkkr.outputs.task_result import TaskResult
    from ase2sprkkr_gui.gui.application.result_loading import (
        ResultImportCache, LoadedResult, disambiguate_scf_potentials,
    )
    potential = tmp_path / "Fe.pot_new"
    potential.write_text("current")
    old, new = tmp_path / "old.out", tmp_path / "new.out"
    head = " * KKRSCF VERSION 9.7 *\nTASK = SCF\npot-file: Fe.pot_new\n"
    old.write_text(head)
    new.write_text(head)
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new, ns=(2_000_000_000, 2_000_000_000))

    class FakeSCF(TaskResult):
        produces_potential = True

        @property
        def potential_filename(self):
            return str(potential)

    monkeypatch.setattr(
        TaskResult, "from_file",
        staticmethod(lambda path, *, input_cache=None:
                     FakeSCF(None, None, tmp_path, output_file=path, task_name="SCF")),
    )
    cache = ResultImportCache()
    old_result = cache.loader.load_file(old)
    loaded = LoadedResult(
        result=old_result, output_path=old, potential_path=potential,
        atoms=Atoms("Fe"), potential_error=None, directory=str(tmp_path),
    )
    items, invalid = disambiguate_scf_potentials([(old, loaded)], cache=cache)
    assert invalid == ("old.out",)
    assert items[0][1].atoms is None


def test_import_rejects_structure_changed_after_worker_read(tmp_path):
    from ase2sprkkr_gui.gui.application.result_loading import LoadedResult
    from ase2sprkkr_gui.gui.application.potential_version import PotentialFileVersion

    potential = tmp_path / "Fe.pot"
    potential.write_text("original")
    out = tmp_path / "Fe.out"
    out.write_text("SCF result")
    version = PotentialFileVersion.capture(potential)
    loaded = LoadedResult(
        result=SimpleNamespace(
            task_name="scf", produces_potential=True, output_values={},
        ),
        output_path=out, potential_path=potential,
        atoms=Atoms("Fe", cell=(3, 3, 3), pbc=True),
        potential_error=None, directory=str(tmp_path),
        parsed_potential_version=version,
    )
    controller = WorkspaceController()
    potential.write_text("later version")
    adoption = controller.adopt_external_result(loaded)
    assert adoption.record is not None
    assert adoption.potential_error is not None
    assert controller.result_store.state_for_result(adoption.record) is None


def test_import_preserves_preparsed_potential_version(tmp_path):
    from ase2sprkkr_gui.gui.application.result_loading import LoadedResult
    from ase2sprkkr_gui.gui.application.potential_version import PotentialFileVersion

    potential = tmp_path / "Fe.pot"
    potential.write_text("original")
    version = PotentialFileVersion.capture(potential)
    loaded = LoadedResult(
        result=SimpleNamespace(
            task_name="scf", produces_potential=True, output_values={},
        ),
        output_path=tmp_path / "Fe.out", potential_path=potential,
        atoms=Atoms("Fe", cell=(3, 3, 3), pbc=True),
        potential_error=None, directory=str(tmp_path),
        parsed_potential_version=version,
    )
    controller = WorkspaceController()
    adoption = controller.adopt_external_result(loaded)
    state = controller.result_store.state_for_result(adoption.record)
    assert state is not None
    assert state.potential_version is version


def test_import_rejects_stdout_modified_after_worker_read(tmp_path):
    from ase2sprkkr_gui.gui.application.result_loading import LoadedResult
    from ase2sprkkr_gui.gui.application.potential_version import PotentialFileVersion

    path = tmp_path / "Fe.out"
    path.write_text("old stdout")
    preparse = PotentialFileVersion.capture(path, digest=False)
    loaded = LoadedResult(
        result=SimpleNamespace(
            task_name="scf", produces_potential=True, output_values={},
        ),
        output_path=path, potential_path=None, atoms=None,
        potential_error=None, directory=str(tmp_path),
        parsed_output_version=preparse,
    )
    path.write_text("modified stdout content")
    controller = WorkspaceController()
    with pytest.raises(Exception, match="changed after it was presented"):
        controller.adopt_external_result(loaded)
    assert not controller.result_store.results


def test_reimport_same_stdout_with_overwritten_potential_keeps_new_state(tmp_path):
    """Unchanged stdout does not authorize reusing a stale structural record."""
    from ase2sprkkr_gui.gui.application.result_loading import LoadedResult
    from ase2sprkkr_gui.gui.application.potential_version import PotentialFileVersion

    output = tmp_path / "Fe.out"
    output.write_text("same old stdout")
    pot = tmp_path / "Fe.pot_new"
    pot.write_text("first result")
    controller = WorkspaceController()
    result = SimpleNamespace(
        task_name="scf", produces_potential=True, output_values={},
    )

    def adopt():
        version = PotentialFileVersion.capture(pot)
        return controller.adopt_external_result(LoadedResult(
            result=result, output_path=output, potential_path=pot,
            atoms=Atoms("Fe", cell=(3, 3, 3), pbc=True),
            potential_error=None, directory=str(tmp_path),
            parsed_potential_version=version,
        )).record

    earlier = adopt()
    old_state = controller.result_store.state_for_result(earlier)
    pot.write_text("second result, different charge density")
    latest = adopt()
    latest_state = controller.result_store.state_for_result(latest)
    assert latest.id != earlier.id
    assert len(controller.result_store.results) == 2
    assert old_state.potential_version != latest_state.potential_version
    assert not old_state.potential_version.is_current()
    assert latest_state.potential_version.is_current()
    assert adopt().id == latest.id
