"""The browser preloads project results without changing the live workspace."""
from __future__ import annotations

import os
from threading import Event, get_ident
from ase2sprkkr import ResultLoader, TaskResult

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from ase2sprkkr_gui.gui.misc.modal_task import _ModalTaskDialog
from PyQt6.QtWidgets import QApplication, QMessageBox

import pytest

from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJob
from ase2sprkkr_gui.gui.controllers.persistent_jobs import PersistentJobsController
from ase2sprkkr_gui.gui.widgets.persistent_jobs import PersistentJobsWidget


@pytest.fixture(autouse=True)
def _fail_on_warning(monkeypatch):
    """Turn unexpected modal errors into test failures, not blocked dialogs."""
    def fail(_parent, title, message, *_args, **_kwargs):
        pytest.fail(f"{title}: {message}")

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fail))


def _job(job_id: str, updated: str = "2026-11-13T10:00:00+00:00") -> PersistentJob:
    return PersistentJob(
        id=job_id, state="finished", created_at=updated, updated_at=updated,
        finished_at=updated, task="DOS", directory="/tmp", dataset=job_id,
        target="Local", return_code=0, message=None,
    )


class _Source:
    history_warning = None

    def __init__(self, directory):
        self.directory = directory
        self.metadata = {"first": _job("first"), "second": _job("second")}
        self.results = {}
        for job_id in self.metadata:
            (directory / f"{job_id}.out").write_text(job_id)
        self.started = Event()
        self.release = Event()
        self.calls = []
        self.listeners = []

    def subscribe(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def jobs(self):
        return tuple(self.metadata.values())

    def job(self, job_id):
        return self.metadata.get(job_id)

    def check_result_files(self, job_id):
        return self.job(job_id)

    def result(self, job_id):
        result = ResultLoader().associate_result(TaskResult(
            None, None, self.directory, output_file=self.directory / f"{job_id}.out", task_name="DOS",
        ))
        self.results[job_id] = result
        return result

    def load_result(self, job_id, *, restore_missing=True):
        self.calls.append((job_id, get_ident()))
        self.started.set()
        assert self.release.wait(timeout=5)
        return self.result(job_id)


def _spin_until(app, predicate, timeout=5):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Timed out waiting for background result"


def test_preloading_stays_off_gui_thread_and_never_adopts(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    source = _Source(tmp_path)
    adopted = []
    controller = PersistentJobsController(source, result_adopter=lambda *args: adopted.append(args))
    view = PersistentJobsWidget(lambda *_: None, auto_load_results=True)
    shown = []
    monkeypatch.setattr(view.result_widget, "set_result", shown.append)
    controller.bind_view(view)
    controller.preload_results(("first",))
    assert source.started.wait(timeout=5)
    assert source.calls[0][1] != get_ident()
    assert not adopted
    source.release.set()
    _spin_until(app, lambda: source.results.get("first") is not None
                and source.results["first"] in shown)
    assert adopted == []

    # Switching away and back reuses the same cached parsed value.
    second = next(
        view.tree.topLevelItem(i).child(j)
        for i in range(view.tree.topLevelItemCount())
        for j in range(view.tree.topLevelItem(i).childCount())
        if view.tree.topLevelItem(i).child(j).data(0, int(Qt.ItemDataRole.UserRole)) == "second"
    )
    view.tree.setCurrentItem(second)
    first = next(
        view.tree.topLevelItem(i).child(j)
        for i in range(view.tree.topLevelItemCount())
        for j in range(view.tree.topLevelItem(i).childCount())
        if view.tree.topLevelItem(i).child(j).data(0, int(Qt.ItemDataRole.UserRole)) == "first"
    )
    view.tree.setCurrentItem(first)
    _spin_until(app, lambda: shown and shown[-1] is source.results["first"])
    assert [name for name, _thread in source.calls].count("first") == 1
    view.close()
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_background_errors_are_not_cached_and_manual_loading_can_retry(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    source = _Source(tmp_path)
    source.release.set()
    attempts = []

    def flaky(job_id, *, restore_missing=True):
        attempts.append(job_id)
        if len(attempts) == 1:
            raise ValueError("invalid output")
        return source.result(job_id)

    source.load_result = flaky
    controller = PersistentJobsController(source)
    view = PersistentJobsWidget(lambda *_: None, auto_load_results=True)
    controller.bind_view(view)
    controller.preload_results(("first",))
    _spin_until(app, lambda: "Result unavailable:" in view.result_widget._empty_label.text())
    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.controllers.persistent_jobs.run_modal_task",
        lambda _view, operation, **kwargs: (
            kwargs["on_success"](operation())
            if "on_success" in kwargs else operation()
        ),
    )
    adopted = []
    controller._result_adopter = lambda *args: adopted.append(args)
    controller._load_result_requested(view, "first")
    assert adopted == [("first", source.results["first"], True)]
    view.close()
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_refresh_reparses_files_without_adopting_but_load_opens_cached_result(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    source = _Source(tmp_path)
    source.release.set()
    adopted = []
    parsed = []
    source.load_result = lambda job_id, **kwargs: parsed.append(job_id) or source.result(job_id)
    controller = PersistentJobsController(
        source, result_adopter=lambda *args: adopted.append(args)
    )
    view = PersistentJobsWidget(lambda *_: None, auto_load_results=True)
    controller.bind_view(view)
    shown = []
    monkeypatch.setattr(view.result_widget, "set_result", shown.append)
    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.controllers.persistent_jobs.run_modal_task",
        lambda _view, operation, **kwargs: (
            kwargs["on_success"](operation())
            if "on_success" in kwargs else operation()
        ),
    )
    opened = []
    controller.resultOpened.connect(opened.append)

    controller._refresh_result_requested(view, "first")
    assert parsed == ["first"]
    first_result = source.results["first"]
    assert first_result in shown
    assert not adopted and not opened
    controller._load_result_requested(view, "first")
    assert parsed == ["first"]  # Load uses already-parsed result.
    assert adopted == [("first", first_result, True)]
    assert opened == ["first"]

    # Refresh rereads after a previous result has been cached.
    controller._refresh_result_requested(view, "first")
    assert parsed == ["first", "first"]
    assert adopted == [("first", first_result, True)]
    assert opened == ["first"]
    controller._load_result_requested(view, "first")
    assert parsed == ["first", "first"]  # Load uses the refreshed cache.
    view.close()
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_failed_result_parse_is_not_requeued_by_project_reprojection(tmp_path):
    from dataclasses import replace

    app = QApplication.instance() or QApplication([])
    source = _Source(tmp_path)
    source.release.set()
    attempts = []

    def fail_parse(job_id, *, restore_missing=True):
        attempts.append(job_id)
        raise EOFError("Unexpected end of output")

    source.load_result = fail_parse
    controller = PersistentJobsController(source)
    view = PersistentJobsWidget(lambda *_: None, auto_load_results=True)
    controller.bind_view(view)
    controller.preload_results(("first",))
    _spin_until(app, lambda: "Result unavailable:" in view.result_widget._empty_label.text())
    assert attempts.count("first") == 1

    # Project projection and status timestamps do not change a captured result.
    source.metadata["first"] = replace(
        source.metadata["first"], updated_at="2026-11-13T10:30:00+00:00"
    )
    for _ in range(5):
        controller._source_changed("first")
        controller.preload_results(("first",))
        app.processEvents()
    assert attempts.count("first") == 1
    assert "Unexpected end of output" in view.result_widget._empty_label.text()

    # A result-relevant revision change permits one new attempt.
    source.metadata["first"] = replace(source.metadata["first"], return_code=139)
    controller._source_changed("first")
    _spin_until(app, lambda: attempts.count("first") == 2)
    controller.unbind_view(view)
    view.close()
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_unbinding_browser_stops_followup_preloads_and_displays(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = _Source(tmp_path)
    controller = PersistentJobsController(source)
    view = PersistentJobsWidget(lambda *_: None, auto_load_results=True)
    shown = []
    original_set_result = view.result_widget.set_result
    view.result_widget.set_result = lambda result: (
        shown.append(result), original_set_result(result)
    )
    controller.bind_view(view)
    controller.preload_results(("first", "second"))
    assert source.started.wait(timeout=5)

    controller.unbind_view(view)
    assert controller._preload_ids == ()
    assert controller._selected_preload_id is None
    assert not any(bound() is view for bound in controller._views)
    source.release.set()
    _spin_until(app, lambda: not controller._loads)
    assert not any(isinstance(result, TaskResult) for result in shown)

    view.close()
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_persistent_load_and_refresh_present_before_working_disappears(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    source = _Source(tmp_path)
    source.release.set()
    events = []
    observations = []
    view = PersistentJobsWidget(lambda *_: None, auto_load_results=False)

    def check(label):
        from PyQt6.QtCore import QThread

        # Earlier modal tasks may leave hidden children until Qt destroys them.
        # Observe the currently visible dialog and assert outside Qt slots.
        observations.append((
            QThread.currentThread() is app.thread(),
            any(dialog.isVisible() for dialog in view.findChildren(_ModalTaskDialog)),
        ))
        events.append(label)

    controller = PersistentJobsController(
        source, result_adopter=lambda *_args: check("adopt"),
    )
    controller.bind_view(view)
    controller.resultOpened.connect(lambda _job_id: check("opened"))
    original_set_result = view.set_result

    def set_result(job_id, result):
        check("present")
        return original_set_result(job_id, result)

    monkeypatch.setattr(view, "set_result", set_result)
    controller._load_result_requested(view, "first")
    assert "adopt" in events and "opened" in events and "present" in events
    assert all(on_gui and visible for on_gui, visible in observations)
    assert source.calls[0][1] != get_ident()

    events.clear()
    observations.clear()
    controller._refresh_result_requested(view, "first")
    assert "present" in events
    assert "adopt" not in events
    assert all(on_gui and visible for on_gui, visible in observations)
    assert [job for job, _thread in source.calls].count("first") == 2
    view.close()
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_changed_preloaded_result_is_not_published_or_adopted(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = _Source(tmp_path)
    result = source.result("first")
    output = tmp_path / "first.out"
    adopted, shown = [], []
    controller = PersistentJobsController(source, result_adopter=lambda *args: adopted.append(args))
    view = PersistentJobsWidget(lambda *_: None, auto_load_results=True)
    monkeypatch.setattr(view.result_widget, "set_result", shown.append)
    controller.bind_view(view)
    source.load_result = lambda _, **kwargs: result
    output.write_text("changed after reading")
    controller.preload_results(("first",))
    _spin_until(app, lambda: "Result unavailable:" in view.result_widget._empty_label.text())
    assert result not in shown and not adopted
    assert controller._cached_result("first") == (False, None)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.controllers.persistent_jobs.run_modal_task",
        lambda _parent, operation, **kwargs: kwargs["on_success"](operation()),
    )
    controller._load_result_requested(view, "first")
    assert warnings and not adopted
    view.close()
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_cached_result_is_reloaded_after_missing_potential_appears(tmp_path, monkeypatch):
    source = _Source(tmp_path)
    potential = tmp_path / "missing.pot"
    calls = []

    def load(job_id, *, restore_missing=True):
        calls.append(job_id)
        result = TaskResult(None, None, tmp_path, output_file=tmp_path / "first.out", task_name="DOS")
        result.files.add_file("potential", str(potential))
        return ResultLoader().associate_result(result)

    source.load_result = load
    controller = PersistentJobsController(source)
    first = controller.load_result("first")
    assert controller.load_result("first") is first
    potential.write_text("available")
    second = controller.load_result("first")
    assert second is not first
    assert calls == ["first", "first"]
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_cached_result_validated_once_for_synchronous_adoption(tmp_path, monkeypatch):
    source = _Source(tmp_path)
    source.release.set()
    controller = PersistentJobsController(source)
    result = controller.load_result("first")
    checks = []
    validate = ResultLoader.sources_are_current

    def counted(loader, value):
        checks.append(value)
        return validate(loader, value)

    monkeypatch.setattr(ResultLoader, "sources_are_current", counted)
    assert controller.load_result("first") is result
    assert checks == [result]
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_restored_file_availability_does_not_obsolete_result(tmp_path):
    from dataclasses import replace

    app = QApplication.instance() or QApplication([])
    source = _Source(tmp_path)
    source.metadata["first"] = replace(source.metadata["first"], result_files_state="missing")
    adopted = []
    controller = PersistentJobsController(source, result_adopter=lambda *args: adopted.append(args))

    def load(job_id, *, restore_missing=True):
        # Restoring a saved file updates availability, not the identity of
        # the completed calculation being reconstructed.
        source.metadata[job_id] = replace(source.metadata[job_id], result_files_state="current")
        return source.result(job_id)

    source.load_result = load
    result = controller.load_result("first")
    assert adopted == [("first", result, True)]
    assert controller.load_result("first") is result
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_preload_never_restores_missing_result_but_explicit_open_can(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = _Source(tmp_path)
    calls = []

    def load(job_id, *, restore_missing=True):
        calls.append(restore_missing)
        if not restore_missing:
            raise OSError("Local OUT missing")
        return source.result(job_id)

    source.load_result = load
    controller = PersistentJobsController(source)
    controller.preload_results(("first",))
    _spin_until(app, lambda: not controller._loads)
    assert calls == [False]
    assert controller.load_result("first") is source.results["first"]
    assert calls == [False, True]
    controller._executor.shutdown(wait=True, cancel_futures=True)


def test_file_check_status_does_not_invalidate_cached_result(tmp_path):
    from dataclasses import replace

    source = _Source(tmp_path)
    source.release.set()
    controller = PersistentJobsController(source)
    result = controller.load_result("first")
    for state, checked in (("current", True), (None, False)):
        source.metadata["first"] = replace(
            source.metadata["first"], result_files_state=state,
            result_files_verified=checked,
        )
        controller._source_changed("first")
        assert controller.load_result("first") is result
    assert len(source.calls) == 1
    controller._executor.shutdown(wait=True, cancel_futures=True)
