from __future__ import annotations

from ase import Atoms
from ase2sprkkr import InputParameters, TaskResult
from ase2sprkkr.outputs.reader_registry import reader_registry
from ase2sprkkr.outputs.task_result import KkrOutputReader
from kkrunner.backends import BackendConfig
from kkrunner.transports import LocalTransport
from kkrunner.files import FileEntry, FileManifest, FileVersion
from kkrunner.projects import ProjectRepository, ProjectRunManager
from kkrunner.runs import RunState, RunStore

from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJobSource
from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.flows import calculation as calculation_flow


def test_persistent_task_identity_round_trip_drives_recompute_editor(
    monkeypatch,
    tmp_path,
):
    """Preserve effective task and schema across persistence and Guy routing."""
    parameters = InputParameters.create("ARPES")
    parameters.TASK.TASK = "SPLEED"
    parameters.CONTROL.DATASET = "contract"
    assert parameters.definition_name.upper() == "ARPES"
    assert parameters.task_name.upper() == "SPLEED"

    input_path = tmp_path / "contract.inp"
    output_path = tmp_path / "contract.out"
    with input_path.open("w") as stream:
        parameters.save_to_file(stream, validate=False)
    output_path.write_text("synthetic persistent output\n")

    projects = ProjectRepository(tmp_path / "projects")
    project = projects.create("Test")
    manager = ProjectRunManager(store=RunStore(tmp_path / "runs"), project_repository=projects, project=project)
    run = manager.new_record(
        directory=tmp_path,
        transport=LocalTransport(),
        backend=BackendConfig("kkrunner"),
        metadata={"ase2sprkkr": {
            "task": "SPLEED",
            "input_definition": "ARPES",
            "dataset": "contract",
        }},
        files=FileManifest(
            inputs=(FileEntry(local_path=str(input_path)),),
            # An actual submitted run records the input bytes before launch.
            # Completing a manually constructed record captures only outputs.
            input_versions=(FileVersion.capture(input_path, require_digest=True),),
            stdout=FileEntry(local_path=str(output_path)),
        ),
    )
    manager.store.save(run)
    run = manager.complete(run, state=RunState.FINISHED, return_code=0, artifacts_available=True)
    assert manager.input_file_is_current(run)

    class SyntheticParser:
        def __init__(self, print_info=False, on_info=None):
            # The current parser contract accepts an optional live-info observer.
            self.on_info = on_info

        def read_from_file(self, output_file, error_file, results, return_code):
            # Provenance must be attached before parsing, not only to the
            # completed result returned to the GUI.
            assert results[0].run_record.id == run.id
            return None, None, return_code

    class Reader(KkrOutputReader):
        parser_class = SyntheticParser
        result_class = TaskResult

    resolved_tasks = []

    def reader_class_for_task(task):
        resolved_tasks.append(task)
        return Reader

    monkeypatch.setattr(reader_registry, "class_for_task", reader_class_for_task)

    loaded = PersistentJobSource(manager).load_result(run.id)

    assert len(resolved_tasks) == 1
    assert resolved_tasks[0].name == "SPLEED"
    # Native task registry schema is SPLEED; the historical run stores ARPES separately.
    assert resolved_tasks[0].input_parameters_definition_name == "SPLEED"
    assert loaded.task_name.upper() == "SPLEED"
    assert loaded.input_parameters.definition_name.upper() == "ARPES"
    assert loaded.input_parameters.task_name.upper() == "SPLEED"
    assert loaded.run_record.metadata["ase2sprkkr"]["task"] == "SPLEED"
    assert loaded.run_record.metadata["ase2sprkkr"]["input_definition"] == "ARPES"

    # Recompute now uses the actual workspace's structure/state identities
    # and creates a one-off request instead of mutating its input document.
    controller = WorkspaceController(
        WorkspaceState(atoms=Atoms("Fe"), directory=str(tmp_path))
    )
    history = controller.result_store
    record = history.add_external_result(
        loaded,
        atoms=None,
        input_parameters=loaded.input_parameters,
        directory=str(tmp_path),
        output_path=output_path,
        potential_path=None,
        persistent_ref=run.id,
    )
    assert record.task_name == "spleed"
    assert record.input_parameters.definition_name.upper() == "ARPES"
    assert record.input_parameters.task_name.upper() == "SPLEED"

    edited = record.input_parameters.copy(copy_values=True)
    structure_generation = controller.structure_generation
    input_parameters_generation = controller.input_parameters_generation
    structure_id, state_id = controller.structure_id, controller.state_id
    editor_calls = []

    def edit(parameters, *, parent, atoms, directory, recent_files):
        editor_calls.append((parameters, parent, atoms, directory, recent_files))
        return edited

    monkeypatch.setattr(calculation_flow, "edit_input_parameters", edit)
    monkeypatch.setattr(
        calculation_flow,
        "GuidedInputParametersDialog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("effective SPLEED must use the Expert editor")
        ),
    )
    launched = object()
    launch_calls = []

    def fake_run(*args, **kwargs):
        launch_calls.append((args, kwargs))
        return launched

    monkeypatch.setattr(calculation_flow, "run_calculation", fake_run)

    recent_files = object()
    parent = object()
    result = calculation_flow.recompute_result(
        controller, recent_files, parent, record.id,
    )

    assert result is launched
    assert len(editor_calls) == 1
    editor_parameters, editor_parent, editor_atoms, editor_directory, editor_recent = editor_calls[0]
    assert editor_parameters is not record.input_parameters
    assert editor_parameters.definition_name.upper() == "ARPES"
    assert editor_parameters.task_name.upper() == "SPLEED"
    assert editor_parent is parent
    assert editor_atoms is controller.atoms
    assert editor_directory == str(tmp_path)
    assert editor_recent is recent_files

    assert len(launch_calls) == 1
    (launch_controller, launch_recent, launch_parent), launch_kwargs = launch_calls[0]
    assert launch_controller is controller
    assert launch_recent is recent_files
    assert launch_parent is parent
    assert launch_kwargs["position_like_window"] == "expert-input"
    request = launch_kwargs["request_override"]
    assert request.atoms is controller.atoms
    assert request.input_parameters is not edited
    assert request.input_parameters.definition_name.upper() == "ARPES"
    assert request.input_parameters.task_name.upper() == "SPLEED"
    assert request.directory == str(tmp_path)
    assert (request.structure_id, request.state_id) == (structure_id, state_id)

    # Recompute must not publish historical inputs or change the live structure.
    assert controller.input_parameters is None
    assert controller.structure_generation == structure_generation
    assert controller.input_parameters_generation == input_parameters_generation
    assert (controller.structure_id, controller.state_id) == (structure_id, state_id)
