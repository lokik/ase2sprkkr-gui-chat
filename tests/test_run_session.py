"""Normal-exit snapshots contain no project history and are consumed once."""
from kkrunner import MemoryRunStore, RunManager, RunState
from kkrunner.files import FileManifest, FileVersion
from kkrunner.runs import RunRecord

from ase2sprkkr_gui.gui.application.run_session import (
    WindowReference, save_session, take_session,
)


def test_session_restores_record_owner_and_consumes_snapshot(tmp_path):
    path = tmp_path / "runstore.json"
    old = MemoryRunStore()
    manager = RunManager(old)
    record = manager.new_record(directory=tmp_path, detached=True)
    record = manager.transition(record, state=RunState.RUNNING,
                                execution=record.execution.submitted("native-42"))
    refs = (WindowReference("memory", record.id), WindowReference("persistent", "project-42"))
    save_session(path, (record,), refs)
    new = MemoryRunStore()
    assert take_session(path, new) == refs
    assert new.get(record.id) == record
    assert new.get(record.id).store is new
    assert take_session(path, MemoryRunStore()) == ()
    assert not path.exists()


def test_session_output_reference_retains_original_version(tmp_path):
    path = tmp_path / "session.json"
    out = tmp_path / "out"
    out.write_text("original")
    ref = WindowReference.output(FileVersion.capture(out, require_digest=True))
    save_session(path, (), (ref,))
    out.write_text("overwritten")
    restored, = take_session(path, MemoryRunStore())
    assert restored == ref
    assert not restored.version.is_current()


def test_invalid_session_entry_does_not_prevent_other_windows(tmp_path):
    import json
    path = tmp_path / "runstore.json"
    path.write_text(json.dumps({"version": 1, "runs": [{"invalid": True}],
                                "windows": [{"kind": "persistent", "value": "ok"}]}))
    assert take_session(path, MemoryRunStore()) == (WindowReference("persistent", "ok"),)


def test_hidden_independent_runs_roundtrip_without_windows(tmp_path):
    path = tmp_path / "session.json"
    old = MemoryRunStore()
    manager = RunManager(old)

    def running(detached):
        record = manager.new_record(directory=tmp_path, detached=detached)
        return manager.transition(record, state=RunState.RUNNING,
                                  execution=record.execution.submitted("native-" + record.id))

    independent, attached = running(True), running(False)
    unaccepted = manager.new_record(directory=tmp_path, detached=True)
    old.save(unaccepted)
    save_session(path, old.runs(), ())
    restored = MemoryRunStore()
    assert take_session(path, restored) == ()
    assert restored.runs() == (independent,)
    # A later clean exit still retains the hidden run even without a monitor.
    save_session(path, restored.runs(), ())
    again = MemoryRunStore()
    assert take_session(path, again) == ()
    assert again.get(independent.id).store is again
    assert len(again.runs()) == 1
