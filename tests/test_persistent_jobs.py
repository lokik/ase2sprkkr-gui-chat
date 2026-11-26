"""Persistent history adapters use current project-backed ASE2SPRKKR API."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import os
from types import SimpleNamespace
import pytest
from kkrunner.runs import RunState, FailurePhase, ExecutionMetadata, WorkingDirectory
from kkrunner.transports import SshTransport
from kkrunner.backends import BackendConfig
from kkrunner.files import FileManifest, FileEntry
import ase2sprkkr_gui.gui.application.persistent_jobs as module
from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJob, PersistentJobSource


def record(state=RunState.RUNNING, *, backend_id="pid-42", failure_phase=None, return_code=None):
    return SimpleNamespace(
        id="run-1", state=state, store=None, project_id=None,
        created_at="2026-10-19T10:00:00+00:00",
        updated_at="2026-10-19T11:00:00+00:00", finished_at=None,
        metadata={"ase2sprkkr": {"task": "DOS", "dataset": "Fe-DOS"}},
        return_code=return_code,
        message=None, failure_phase=failure_phase,
        files=FileManifest(),
        execution=ExecutionMetadata(
            SshTransport(host="cluster"),
            WorkingDirectory("/tmp/calc", "/remote/calc"), backend_id=backend_id,
            backend=BackendConfig(name="psij", options={"executor_name": "slurm"}),
        ),
    )


class Store:
    def __init__(self, value):
        self.current = value
        self.calls = []
    def runs(self, *, on_error=None):
        return (self.current,)
    def get(self, run_id):
        self.calls.append(run_id)
        return self.current


def test_record_metadata_maps_canonical_backend_contract():
    job = PersistentJob.from_run_record(record())
    assert job.target == "SSH · cluster · PSI/J slurm"
    assert job.directory == "/tmp/calc"
    assert job.attachable
    assert not job.is_final


@pytest.mark.parametrize("state", (RunState.FINISHED, RunState.FAILED, RunState.CANCELLED))
def test_terminal_runs_cannot_be_attached(state):
    job = PersistentJob.from_run_record(record(state))
    assert job.is_final
    assert not job.attachable


def test_submitting_run_without_backend_identity_cannot_be_attached():
    assert not PersistentJob.from_run_record(record(RunState.SUBMITTING, backend_id=None)).attachable


def test_download_retry_is_explicit_and_requires_successful_execution():
    job = PersistentJob.from_run_record(record(RunState.FAILED, failure_phase=FailurePhase.DOWNLOAD, return_code=0))
    assert job.can_retry_download
    assert not job.attachable
    assert not PersistentJob.from_run_record(record(RunState.FAILED, failure_phase=FailurePhase.EXECUTION, return_code=1)).can_retry_download


def test_source_tracks_authoritative_changes(make_job_source):
    initial = record()
    store = Store(initial)
    source = make_job_source(SimpleNamespace(store=store))
    assert source.jobs()[0].state == "running"
    updates = []
    source.subscribe(updates.append)
    store.current = record(RunState.FINISHED)
    source.job_changed(initial.id)
    assert updates == [initial.id]
    assert store.calls == [initial.id]
    assert source.jobs()[0].state == "finished"


def test_attach_and_load_delegate_to_backend_api(monkeypatch, make_job_source):
    item = record(RunState.FINISHED)
    store = Store(item)
    calls = []
    result = SimpleNamespace(run_record=item)
    manager = SimpleNamespace(
        store=store,
        attach=lambda *args, **kwargs: None,
        retrieve_result_files=lambda *_args: None,
    )
    monkeypatch.setattr(module, "attach_project_run", lambda _manager, ref, **kwargs: calls.append("attach") or object())
    monkeypatch.setattr(module, "load_project_result", lambda _manager, ref, **kwargs: calls.append(("load", kwargs)) or result)
    source = make_job_source(manager)
    source.attach(item.id)
    assert source.load_result(item.id) is result
    assert calls == ["attach", ("load", {"allow_unparsed": True})]

def test_postprocessing_retry_requires_successful_execution():
    ready = PersistentJob.from_run_record(
        record(RunState.FAILED, failure_phase=FailurePhase.POSTPROCESSING, return_code=0)
    )
    assert ready.can_retry_postprocessing
    assert not PersistentJob.from_run_record(
        record(RunState.FAILED, failure_phase=FailurePhase.EXECUTION, return_code=1)
    ).can_retry_postprocessing
    assert not PersistentJob.from_run_record(record(RunState.FINISHED)).can_retry_postprocessing


def test_history_excludes_records_from_other_applications(make_job_source):
    foreign = record(RunState.FINISHED)
    foreign.metadata = {"other_application": {"task": "DOS"}}
    source = make_job_source(SimpleNamespace(store=Store(foreign)))
    assert source.jobs() == ()
    assert source.job_changed(foreign.id) is None


def test_explicit_postprocessing_retry_delegates_to_application(monkeypatch, make_job_source):
    item = record(RunState.FAILED, failure_phase=FailurePhase.POSTPROCESSING, return_code=0)
    store = Store(item)
    manager = SimpleNamespace(store=store, retry_postprocessing=lambda *_args: None)
    calls = []
    expected = object()

    def retry(received_manager, run_id):
        calls.append((received_manager, run_id))
        return expected

    monkeypatch.setattr(module, "retry_project_postprocessing", retry)
    source = make_job_source(manager)
    assert source.retry_postprocessing(item.id) is expected
    assert calls == [(manager, item.id)]
    assert store.calls == [item.id]

def test_unknown_supervised_ssh_run_can_be_reattached_after_restart():
    item = record(RunState.UNKNOWN)
    item.execution = replace(item.execution, backend=BackendConfig("kkrunner"))
    restored = PersistentJob.from_run_record(item)
    assert restored.attachable
    assert restored.reattach_unavailable_reason is None
    assert not restored.is_final


def test_unknown_direct_ssh_run_explains_why_it_cannot_be_reattached():
    item = record(RunState.UNKNOWN)
    item.execution = replace(item.execution, backend=BackendConfig("direct"))
    restored = PersistentJob.from_run_record(item)
    assert not restored.attachable
    assert "no saved output" in restored.reattach_unavailable_reason.lower()
    assert not restored.cancelable
    assert "verified PID" in restored.cancel_unavailable_reason


def test_submitting_run_explains_missing_backend_identity():
    restored = PersistentJob.from_run_record(
        record(RunState.SUBMITTING, backend_id=None)
    )
    assert not restored.attachable
    assert "Refresh statuses" in restored.reattach_unavailable_reason


def test_profile_comes_from_ase2sprkkr_metadata():
    item = record()
    item.metadata["ase2sprkkr"]["profile"] = "deleted-cluster"
    assert PersistentJob.from_run_record(item).profile == "deleted-cluster"

    # Explicitly unnamed profiles and legacy records have the same representation.
    item.metadata["ase2sprkkr"]["profile"] = None
    assert PersistentJob.from_run_record(item).profile is None
    item.metadata["ase2sprkkr"].pop("profile")
    assert PersistentJob.from_run_record(item).profile is None


def test_direct_run_with_saved_output_can_monitor_and_safely_cancel():
    item = record(RunState.UNKNOWN, backend_id="direct:42:137:00000000-0000-0000-0000-000000000000")
    item.execution = replace(item.execution, backend=BackendConfig("direct"))
    item.files = FileManifest(stdout=FileEntry(
        local_path="/tmp/calc/stdout.txt", remote_path="/remote/calc/stdout.txt",
    ))
    job = PersistentJob.from_run_record(item)
    assert job.attachable
    assert job.cancelable
    assert job.reattach_unavailable_reason is None


def test_source_cancel_and_forget_delegate_to_manager(make_job_source):
    item = record(RunState.UNKNOWN)
    store = Store(item)
    calls = []

    def forget(job_id, *, force=False):
        calls.append(("forget", job_id, force))
        store.current = None
        return item

    store.runs = lambda **kwargs: (() if store.current is None else (store.current,))

    def get(job_id):
        if store.current is None:
            raise FileNotFoundError(job_id)
        return store.current

    store.get = get
    manager = SimpleNamespace(
        store=store,
        cancel=lambda job_id: calls.append(("cancel", job_id)),
        forget=forget,
    )
    source = make_job_source(manager)
    assert source.cancel(item.id) is not None
    source.forget(item.id, force=True)
    assert source.job(item.id) is None
    assert calls == [("cancel", item.id), ("forget", item.id, True)]


def test_file_only_results_join_project_history_without_fake_run(tmp_path, monkeypatch, make_job_source):
    from kkrunner.projects import ProjectRepository
    from ase2sprkkr_gui.gui.application.imported_project_results import assign

    projects = ProjectRepository(tmp_path / "projects")
    first = projects.create("First")
    second = projects.create("Second")
    output = tmp_path / "old-calculation.out"
    output.write_text("external calculation")
    finished = datetime(2025, 7, 18, 13, 42, 25, tzinfo=timezone.utc)
    os.utime(output, (finished.timestamp(), finished.timestamp()))
    assert assign(
        projects, [SimpleNamespace(output_path=output, output_version=None)], first.id
    ) == 1

    manager = SimpleNamespace(store=Store(record(RunState.FINISHED)))
    source = make_job_source(manager, projects=projects)
    imported = next(job for job in source.jobs() if job.imported_path is not None)
    assert imported.id.startswith("external:")
    assert imported.dataset == "old-calculation.out"
    assert imported.project_id == first.id
    assert imported.state == "imported"
    assert imported.finished_at == finished.isoformat()
    assert imported.created_at == finished.isoformat()
    assert imported.is_final
    assert imported.return_code is None
    assert not imported.attachable
    assert not imported.cancelable

    expected_result = object()
    monkeypatch.setattr(
        module, "load_result_file",
        lambda path: SimpleNamespace(result=expected_result),
    )
    assert source.load_result(imported.id) is expected_result

    source.reassign_runs(
        projects, (imported.id,), second.id,
        expected_project_ids={imported.id: first.id},
    )
    assert source.job(imported.id).project_id == second.id

    source.forget(imported.id)
    assert source.job(imported.id) is None
    assert output.exists()


def test_missing_imported_file_remains_visible_for_removal(tmp_path, make_job_source):
    from kkrunner.projects import ProjectRepository
    from ase2sprkkr_gui.gui.application.imported_project_results import assign

    projects = ProjectRepository(tmp_path / "projects")
    project = projects.create("Project")
    path = tmp_path / "missing.out"
    path.write_text("temporary")
    assign(projects, [SimpleNamespace(output_path=path, output_version=None)], project.id)
    path.unlink()

    source = make_job_source(
        SimpleNamespace(store=Store(record(RunState.FINISHED))), projects=projects,
    )
    imported = next(job for job in source.jobs() if job.imported_path is not None)
    assert imported.result_files_state == "missing"
    assert imported.finished_at is None
    assert imported.is_final
    source.forget(imported.id)
    assert all(job.imported_path is None for job in source.jobs())


def test_one_source_routes_equal_run_ids_to_their_own_store(tmp_path, monkeypatch, make_job_source):
    from kkrunner import MemoryRunStore, RunManager, RunStore
    disk = RunManager(RunStore(tmp_path / "history"))
    session = RunManager(MemoryRunStore())
    for manager in (disk, session):
        manager.store.save(manager.new_record(
            run_id="same", directory=tmp_path, metadata={"ase2sprkkr": {"task": "DOS"}},
        ))
    source = make_job_source(disk, session_manager=session)
    assert {job.id for job in source.jobs()} == {"same", "session/same"}
    changes = []
    source.subscribe(changes.append)
    record = session.store.get("same")
    record = session.transition(record, state=RunState.RUNNING)
    assert source.job_changed(record).id == "session/same"
    assert changes == ["session/same"]
    assert source.job("same").state == "new"
    calls = []
    monkeypatch.setattr(module, "attach_project_run",
                        lambda manager, run_id, **kwargs: calls.append((manager, run_id)))
    source.attach("session/same")
    source.attach("same")
    assert calls == [(session, "same"), (disk, "same")]
    source.forget("session/same", force=True)
    assert source.job("session/same") is None
    assert source.job("same") is not None
    assert disk.store.get("same").state == RunState.NEW


def test_reload_cannot_overwrite_concurrent_record_notification(tmp_path, monkeypatch, make_job_source):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from kkrunner import RunManager, RunStore

    store = RunStore(tmp_path / "runs")
    manager = RunManager(store)
    initial = manager.transition(manager.new_record(
        directory=tmp_path, metadata={"ase2sprkkr": {"task": "DOS"}},
    ), state=RunState.RUNNING)
    source = make_job_source(manager)
    entered, release, notified, reading = Event(), Event(), Event(), Event()
    original = store.runs
    original_get = store.get

    def read(run_id):
        reading.set()
        return original_get(run_id)

    def paused_snapshot(**kwargs):
        snapshot = original(**kwargs)
        entered.set()
        assert release.wait(5)
        return snapshot

    def notify():
        notified.set()
        source.job_changed(initial.id)

    monkeypatch.setattr(store, "runs", paused_snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reload_future = pool.submit(source.reload)
        try:
            assert entered.wait(5)
            manager.transition(initial, state=RunState.FAILED)
            monkeypatch.setattr(store, "get", read)
            notify_future = pool.submit(notify)
            assert notified.wait(5)
            assert not reading.wait(0.1), "notification must wait for the in-flight snapshot"
            # Readers do not block behind a filesystem reload.
            assert source.job(initial.id).state == "running"
        finally:
            release.set()
        reload_future.result(timeout=5)
        notify_future.result(timeout=5)
    assert source.job(initial.id).state == "failed"


def test_result_loading_does_not_restore_stale_record(tmp_path, monkeypatch, make_job_source):
    from kkrunner import RunManager, RunStore
    manager = RunManager(RunStore(tmp_path / "runs"))
    initial = manager.transition(manager.new_record(
        directory=tmp_path, metadata={"ase2sprkkr": {"task": "DOS"}},
    ), state=RunState.RUNNING)
    source = make_job_source(manager)
    result = SimpleNamespace(run_record=initial)

    def load(*args, **kwargs):
        manager.transition(initial, state=RunState.FAILED)
        source.job_changed(initial.id)
        return result

    monkeypatch.setattr(module, "load_project_result", load)
    assert source.load_result(initial.id) is result
    assert source.job(initial.id).state == "failed"


@pytest.mark.parametrize("state", [RunState.DOWNLOADING, RunState.POSTPROCESSING])
def test_finalizing_run_can_be_observed_but_not_cancelled(state):
    job = PersistentJob.from_run_record(record(state, return_code=0))
    assert job.attachable
    assert not job.cancelable
    assert "Execution has finished" in job.cancel_unavailable_reason
