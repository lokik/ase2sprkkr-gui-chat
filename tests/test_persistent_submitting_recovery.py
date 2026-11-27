from __future__ import annotations

from dataclasses import replace
from kkrunner.runs import ExecutionMetadata, WorkingDirectory, RunRecord, RunState
from kkrunner.transports import LocalTransport
from kkrunner.backends import BackendConfig
from kkrunner.files import FileManifest

from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJobSource


def _record(state: str):
    return RunRecord(
        id="run-id", store=None, project_id=None,
        state=RunState(state),
        created_at="2026-10-17T08:00:00+00:00",
        updated_at="2026-10-17T08:00:00+00:00",
        finished_at=None,
        metadata={"ase2sprkkr": {"task": "SCF"}},
        execution=ExecutionMetadata(LocalTransport(), WorkingDirectory("/tmp/calc"), backend=BackendConfig("kkrunner")),
        files=FileManifest(), failure_phase=None,

        return_code=None,
        message=None,

    )


class _Store:
    def __init__(self, record):
        self.record = record

    def runs(self, *, on_error=None):
        return (self.record,)

    def get(self, _job_id):
        return self.record


def test_refresh_delegates_submission_recovery_to_manager(make_job_source):
    store = _Store(_record("submitting"))
    calls = []

    class Manager:
        def __init__(self):
            self.store = store

        def recover_submitting(self, record):
            raise AssertionError("only RunManager decides when to recover submission")

        def refresh(self, *, on_error=None):
            calls.append("refresh")
            recovered = _record("running")
            self.store.record = replace(recovered, execution=ExecutionMetadata(
                LocalTransport(), WorkingDirectory("/tmp/calc"), "pid-42",
                backend=BackendConfig("kkrunner"),
            ))

    source = make_job_source(Manager())
    jobs = source.refresh()

    assert calls == ["refresh"]
    assert jobs[0].state == "running"
    assert jobs[0].attachable


def test_refresh_does_not_recover_non_submitting_runs(make_job_source):
    store = _Store(_record("running"))
    calls = []

    class Manager:
        def __init__(self):
            self.store = store

        def recover_submitting(self, record):
            calls.append(("recover", record.state.value))

        def refresh(self, *, on_error=None):
            calls.append(("refresh", self.store.record.state.value))

    source = make_job_source(Manager())

    source.refresh()

    assert calls == [("refresh", "running")]
