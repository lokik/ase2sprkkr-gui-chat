"""Reading job metadata never creates observers or recovers native execution."""
from types import SimpleNamespace
import ase2sprkkr_gui.gui.application.persistent_jobs as module
from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJobSource
from kkrunner.runs import RunState, ExecutionMetadata, WorkingDirectory
from kkrunner.files import FileManifest
from kkrunner.transports import LocalTransport
from kkrunner.backends import BackendConfig


def test_source_has_no_implicit_recovery_on_construction_reload_or_refresh(monkeypatch, make_job_source):
    record = SimpleNamespace(
        id="run-1", state=RunState.POSTPROCESSING, store=None, project_id=None,
        created_at="2026-10-19T10:00:00+00:00",
        updated_at="2026-10-19T10:01:00+00:00",
        finished_at=None, metadata={"ase2sprkkr": {"task": "DOS"}},
        return_code=0, message=None, failure_phase=None,
        files=FileManifest(),
        execution=ExecutionMetadata(LocalTransport(), WorkingDirectory("/tmp/calc"),
                                    "pid-42", backend=BackendConfig("kkrunner")),
    )
    store = SimpleNamespace(runs=lambda **kwargs: (record,), get=lambda _id: record)
    calls = []
    manager = SimpleNamespace(store=store, attach=lambda *a, **k: None,
                              refresh=lambda **kwargs: calls.append("refresh"))

    def unexpected_attach(*args, **kwargs):
        raise AssertionError("a metadata source must not start a runner")

    monkeypatch.setattr(module, "attach_project_run", unexpected_attach)
    source = make_job_source(manager)
    assert source.job("run-1").state == "postprocessing"
    source.job_changed("run-1")
    source.reload()
    source.refresh()
    assert calls == ["refresh"]
    assert source.job("run-1").state == "postprocessing"
