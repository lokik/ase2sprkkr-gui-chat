"""Exit requests keep the Qt event loop alive and respect native lifetime."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from ase2sprkkr_gui.main import GuiApplication


def _application(monitors, parent):
    return SimpleNamespace(
        persistent_jobs_controller=SimpleNamespace(monitor_windows=lambda: tuple(monitors)),
        _workflow_window=parent,
    )


def test_declining_exit_does_not_cancel_any_monitor(monkeypatch):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    stopped = []
    monitor = SimpleNamespace(active_thread=object(), shutdown_cancels_job=True,
                              request_shutdown=lambda: stopped.append(True))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    assert not GuiApplication._stop_run_monitors_for_exit(_application([monitor], parent))
    assert stopped == []
    parent.close()
    assert app is QApplication.instance()


def test_shutdown_waits_for_all_monitor_signals_without_blocking_qt(monkeypatch):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    threads = [QThread(parent), QThread(parent)]
    delivered = []
    monitors = []
    for thread in threads:
        def stop(thread=thread):
            # A queued GUI callback must be able to run while exit is waiting.
            QTimer.singleShot(0, lambda: (delivered.append(thread), thread.quit()))
        monitors.append(SimpleNamespace(active_thread=thread, shutdown_cancels_job=False,
                                        request_shutdown=stop))
        thread.start()

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    try:
        assert GuiApplication._stop_run_monitors_for_exit(_application(monitors, parent))
        assert set(delivered) == set(threads)
        assert not warnings
    finally:
        for thread in threads:
            thread.quit()
            assert thread.wait(2000)
        parent.close()
    assert app is QApplication.instance()
