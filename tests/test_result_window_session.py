"""Result-window session persistence and restore routing."""
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QWidget

from ase2sprkkr_gui.gui.application.result_window_session import (
    load_window_session, save_window_session,
)
from ase2sprkkr_gui.main import GuiApplication


def test_window_session_round_trip_and_clear(tmp_path):
    path = tmp_path / "session.json"
    output = str(tmp_path / "Fe.out")
    assert load_window_session(path) == ()
    save_window_session(path, [("job", "run-1"), ("output", output), ("job", "run-1")])
    assert load_window_session(path) == (("job", "run-1"), ("output", output))
    save_window_session(path, [])
    assert load_window_session(path) == ()
    path.write_text("invalid json")
    assert load_window_session(path) == ()


def test_save_only_open_windows_and_restore_existing_sources(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    kwargs = dict(recent_files_path=tmp_path / "recent.json",
                  settings_path=tmp_path / "settings.json")
    gui = GuiApplication(**kwargs)
    output = tmp_path / "Fe.out"
    output.write_text("stdout")
    records = {
        42: SimpleNamespace(id=42, persistent_ref=None, output_path=output),
        43: SimpleNamespace(id=43, persistent_ref="closed", output_path=None),
    }
    visible, closed = QWidget(), QWidget()
    visible.show()
    gui._result_windows = {42: visible, 43: closed}
    monkeypatch.setattr(gui.controller.result_store, "result", records.__getitem__)
    monkeypatch.setattr(gui.persistent_jobs_controller, "open_window_references",
                        lambda: (("job", "run-1"),))
    gui._save_result_windows()
    assert load_window_session(gui._result_session_path) == (
        ("job", "run-1"), ("output", str(output)),
    )
    visible.close()

    restarted = GuiApplication(**kwargs)
    parent = QWidget()
    monkeypatch.setattr(restarted, "create_workflow_window", lambda: parent)
    restored = []
    monkeypatch.setattr(restarted.persistent_jobs_controller, "restore_window",
                        lambda key, _parent: restored.append(("job", key)) or True)
    monkeypatch.setattr(
        "ase2sprkkr_gui.main.file_flows.load_output",
        lambda *_args: True,
    )
    monkeypatch.setattr(restarted.controller.result_store, "result_for_external_source",
                        lambda _path: records[42])
    monkeypatch.setattr(restarted, "_show_result_window",
                        lambda key: restored.append(("output", key)))
    restarted.restore_result_windows()
    assert restored == [("job", "run-1"), ("output", 42)]


def test_controller_restores_final_and_attachable_jobs(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    gui = GuiApplication(recent_files_path=tmp_path / "recent.json",
                         settings_path=tmp_path / "settings.json")
    controller = gui.persistent_jobs_controller
    jobs = {
        "done": SimpleNamespace(is_final=True, can_retry_download=False, attachable=False),
        "running": SimpleNamespace(is_final=False, can_retry_download=False, attachable=True),
    }
    monkeypatch.setattr(controller, "_source", SimpleNamespace(
        job=jobs.get, supports_results=True, supports_attach=True,
    ))
    calls = []
    monkeypatch.setattr(controller, "open_result_window",
                        lambda key, _parent: calls.append(("result", key)))
    monkeypatch.setattr(controller, "open_monitor_window",
                        lambda key: calls.append(("monitor", key)))
    parent = QWidget()
    assert controller.restore_window("done", parent)
    assert controller.restore_window("running", parent)
    assert not controller.restore_window("missing", parent)
    assert calls == [("result", "done"), ("monitor", "running")]
