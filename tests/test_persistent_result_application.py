from __future__ import annotations
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from ase import Atoms
from ase2sprkkr import InputParameters
from kkrunner.projects import ProjectRunManager, ProjectRepository
from kkrunner.files import FileEntry, FileManifest, FileVersion
from kkrunner import RunState, RunStore
from PyQt6.QtWidgets import QApplication, QDialog, QWidget
from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJobSource
from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.dialogs.guided_input import GuidedInputParametersDialog
from ase2sprkkr_gui.gui.flows import calculation as calculation_flow
from ase2sprkkr_gui.main import GuiApplication

def _finished_dos_run(tmp_path):
    parameters = InputParameters.create('DOS')
    parameters.CONTROL.DATASET = 'persistent-application'
    input_path = tmp_path / 'persistent-application.inp'
    output_path = tmp_path / 'persistent-application.out'
    dos_path = tmp_path / 'persistent-application_DOS.dos'
    with input_path.open('w') as stream:
        parameters.save_to_file(stream, validate=False)
    dos_path.write_text('')
    output_path.write_text(f'          *       KKRSCF  VERSION  8.5.test      (C) 2026  *\n          programm execution  on    15/10/2026    at   18:59:12\n          FILES:\n\n          input: {input_path.name}\n          DOS: {dos_path.name}\n\n')
    projects = ProjectRepository(tmp_path / 'projects')
    project = projects.create('Test')
    manager = ProjectRunManager(store=RunStore(tmp_path / 'runs'), project_repository=projects, project=project)
    run = manager.new_record(directory=tmp_path, files=FileManifest(inputs=(FileEntry(local_path=str(input_path)),), stdout=FileEntry(local_path=str(output_path)), outputs=(FileEntry(local_path=str(dos_path)),), input_versions=(FileVersion.capture(input_path, require_digest=True),)), metadata={'ase2sprkkr': {'task': str('DOS').upper(), 'input_definition': str('DOS').upper()}}, processing_required=True)
    manager.store.save(run)
    run = manager.complete(run, state=RunState.FINISHED, return_code=0, artifacts_available=True)
    return (PersistentJobSource(manager), run)

def test_persisted_result_enters_application_history_and_recompute(tmp_path, monkeypatch):
    """Exercise the production persistence -> workspace -> recompute boundaries."""
    application = QApplication.instance() or QApplication([])
    source, run = _finished_dos_run(tmp_path)
    gui = GuiApplication(workspace=WorkspaceState(atoms=Atoms('Fe', cell=(3, 3, 3), pbc=True), directory=str(tmp_path)), recent_files_path=tmp_path / 'recent.json', settings_path=tmp_path / 'settings.json', persistent_jobs=source)
    loaded = gui.persistent_jobs_controller.load_result(run.id)
    assert loaded.task_name.lower() == 'dos'
    assert loaded.input_parameters.task_name.lower() == 'dos'
    record = gui.controller.result_store.result_for_external_source(tmp_path / 'persistent-application.out', persistent_ref=run.id)
    assert record is not None
    assert record.persistent_ref == run.id
    assert record.task_name == 'dos'
    assert record.input_parameters.task_name.lower() == 'dos'
    parent = QWidget()
    original_parameters = gui.controller.input_parameters
    seen_dialogs, requests = [], []

    def accept_parameters(dialog):
        seen_dialogs.append(dialog)
        return QDialog.DialogCode.Accepted

    def capture_request(*_args, **kwargs):
        requests.append(kwargs["request_override"])
        return None

    monkeypatch.setattr(GuidedInputParametersDialog, "exec", accept_parameters)
    monkeypatch.setattr(calculation_flow, "run_calculation", capture_request)
    launched = calculation_flow.recompute_result(gui.controller, gui.recent_files, parent, record.id)
    assert launched is None
    assert len(seen_dialogs) == 1
    assert len(requests) == 1
    assert requests[0].input_parameters.task_name.lower() == 'dos'
    assert requests[0].atoms is gui.controller.atoms
    # Recompute is a one-off request, not a workspace input document.
    assert gui.controller.input_parameters is original_parameters


def test_reopening_persistent_run_does_not_reinterpret_its_potential(tmp_path, monkeypatch):
    from ase2sprkkr_gui import main as gui_main

    _application = QApplication.instance() or QApplication([])
    source, run = _finished_dos_run(tmp_path)
    gui = GuiApplication(
        recent_files_path=tmp_path / "recent.json",
        settings_path=tmp_path / "settings.json",
        persistent_jobs=source,
    )
    gui.persistent_jobs_controller.load_result(run.id)
    first = gui.controller.result_store.result_for_external_source(
        None, persistent_ref=run.id,
    )
    assert first is not None

    def should_not_reinterpret(*_args, **_kwargs):
        raise AssertionError("already adopted result must not reparse its potential")

    monkeypatch.setattr(gui_main, "interpret_result", should_not_reinterpret)
    gui.persistent_jobs_controller.load_result(run.id)
    again = gui.controller.result_store.result_for_external_source(
        None, persistent_ref=run.id,
    )
    assert again is first
    assert len(gui.controller.result_store.results) == 1
