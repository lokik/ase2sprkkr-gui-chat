"""Unsupported execution stays visible; corrupt history is reported and skipped."""

import json

import pytest
from kkrunner import RunManager, RunState, RunStore

from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJobSource


@pytest.mark.parametrize("component", ("backend", "transport"))
@pytest.mark.parametrize("state", (RunState.NEW, RunState.RUNNING, RunState.SUBMITTING))
def test_gui_keeps_unsupported_execution_visible_without_controls(tmp_path, caplog, component, state, make_job_source):
    store = RunStore(tmp_path / "history")
    manager = RunManager(store, submitting_grace=0)
    for run_id in ("good", "obsolete"):
        record = manager.new_record(
            directory=tmp_path / run_id,
            run_id=run_id,
            metadata={"ase2sprkkr": {"task": "SCF"}},
        )
        if run_id == "obsolete":
            record = manager.transition(record, state=state)
        else:
            store.save(record)

    path = store._path(store.get("obsolete"))
    data = json.loads(path.read_text())
    if component == "backend":
        data["execution"]["backend"]["name"] = "process"
    else:
        data["execution"]["transport"] = {"kind": "legacy", "host": "cluster"}
    path.write_text(json.dumps(data))
    unchanged = path.read_bytes()

    source = make_job_source(manager)
    assert sorted(job.id for job in source.jobs()) == ["good", "obsolete"]
    job = source.job_changed("obsolete")
    assert job is not None
    assert job.state == state.value
    assert "unavailable" in job.target
    assert not job.attachable
    assert not job.cancelable
    assert "unavailable" in job.reattach_unavailable_reason
    assert "unavailable" in job.cancel_unavailable_reason

    assert sorted(job.id for job in source.reload()) == ["good", "obsolete"]
    assert sorted(job.id for job in source.refresh()) == ["good", "obsolete"]
    assert source.history_warning is None
    assert "Skipping unreadable persistent run" not in caplog.text
    assert path.read_bytes() == unchanged


def test_gui_loads_healthy_runs_and_warns_about_corrupt_record(tmp_path, caplog, make_job_source):
    store = RunStore(tmp_path / "history")
    manager = RunManager(store)
    for run_id in ("good", "obsolete"):
        record = manager.new_record(
            directory=tmp_path / run_id,
            run_id=run_id,
            metadata={"ase2sprkkr": {"task": "SCF"}},
        )
        store.save(record)

    path = store._path(store.get("obsolete"))
    data = json.loads(path.read_text())
    data["state"] = "invalid-state"
    path.write_text(json.dumps(data))
    unchanged = path.read_bytes()

    source = make_job_source(manager)
    assert [job.id for job in source.jobs()] == ["good"]
    assert source.history_warning is not None
    assert "1 stored run record" in source.history_warning
    assert "not a valid RunState" in caplog.text
    assert str(path) in caplog.text
    assert path.read_bytes() == unchanged

    assert source.job_changed("obsolete") is None
    assert source.history_warning is not None
    assert [job.id for job in source.reload()] == ["good"]
    assert [job.id for job in source.refresh()] == ["good"]

    # Once the invalid record is repaired, both entries become visible,
    # and the warning disappears without restarting the GUI.
    data["state"] = "new"
    path.write_text(json.dumps(data))
    assert sorted(job.id for job in source.reload()) == ["good", "obsolete"]
    assert source.history_warning is None
