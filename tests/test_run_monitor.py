"""Tests for the generic run monitor and persistent attach adapter."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPlainTextEdit

from ase2sprkkr_gui.gui.application.execution_cancellation import RunCancellation
from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJob
from ase2sprkkr_gui.gui.dialogs.attached_run import (
    AttachedRunWorker,
    create_attached_run_monitor_window,
)
from ase2sprkkr_gui.gui.dialogs.run_calculation import SprkkrRunWindow
from ase2sprkkr_gui.gui.dialogs.run_monitor import (
    MAX_LOGICAL_LINE_LENGTH,
    MAX_OUTPUT_BLOCKS,
    RunMonitorWindow,
)


class _Source:
    supports_attach = True

    def __init__(self, result):
        self.result = result
        self.attach_calls = []

    def attach(self, job_id, *, callback=None, output_listener=None):
        self.attach_calls.append(job_id)
        output_listener("replayed\n", "out")
        result = self.result

        class Runner:
            state = SimpleNamespace(value="running")

            @staticmethod
            def wait():
                return result

            @staticmethod
            def cancel():
                return None

        return Runner()


def _job():
    return PersistentJob(
        id="0123456789abcdef0123456789abcdef",
        state="running",
        created_at="2026-10-13T08:00:00+00:00",
        updated_at="2026-10-13T08:00:00+00:00",
        finished_at=None,
        task="SCF",
        directory="/tmp/calc",
        dataset="Fe-SCF",
        target="Local",
        return_code=None,
        message=None,
        result_files_state=None,
        attachable=True,
    )


def test_attached_worker_receives_replayed_output_before_wait():
    result = object()
    source = _Source(result)
    worker = AttachedRunWorker(source, _job().id, RunCancellation())
    output = []
    finished = []
    worker.output.connect(lambda text, kind: output.append((text, kind)))
    worker.finished.connect(finished.append)

    worker.run()

    assert source.attach_calls == [_job().id]
    assert output == [("replayed\n", "out")]
    assert finished == [result]


def test_generic_monitor_has_cancellation_state_before_worker_start():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)

    window._on_stop()
    app.processEvents()

    assert window.runner is None
    assert not window._stop_btn.isEnabled()


def test_generic_monitor_close_hides_without_cancelling():
    app = QApplication.instance() or QApplication([])

    class RunningThread:
        @staticmethod
        def isRunning():
            return True

    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)
    window._thread = RunningThread()
    window.show()
    app.processEvents()

    window.close()
    app.processEvents()

    assert window._close_requested
    assert not window.isVisible()
    assert not window._cancellation.requested


def test_generic_monitor_uses_bounded_plain_text_output():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)

    assert app is QApplication.instance()
    assert isinstance(window._output, QPlainTextEdit)
    assert window._output.maximumBlockCount() == MAX_OUTPUT_BLOCKS


def test_generic_monitor_wraps_long_logical_lines_across_output_chunks():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)

    window._append_output("x" * MAX_LOGICAL_LINE_LENGTH, "out")
    window._append_output("y", "out")

    assert app is QApplication.instance()
    assert window._output.toPlainText() == (
        "x" * MAX_LOGICAL_LINE_LENGTH + "\ny"
    )


def test_generic_monitor_does_not_double_wrap_at_real_newline():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)

    window._append_output("x" * MAX_LOGICAL_LINE_LENGTH + "\ny", "out")

    assert app is QApplication.instance()
    assert window._output.toPlainText() == (
        "x" * MAX_LOGICAL_LINE_LENGTH + "\ny"
    )


def test_generic_monitor_discards_old_output_blocks():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)

    text = "".join(f"line {i}\n" for i in range(MAX_OUTPUT_BLOCKS + 10))
    window._append_output(text, "out")

    assert app is QApplication.instance()
    assert window._output.document().blockCount() <= MAX_OUTPUT_BLOCKS
    assert "line 0\n" not in window._output.toPlainText()
    assert f"line {MAX_OUTPUT_BLOCKS + 9}" in window._output.toPlainText()


def test_attached_monitor_stop_button_describes_observation_only():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), observation_only=True, autostart=False,
    )
    assert window._stop_btn.text() == "Stop monitoring"
    assert window._cancellation._observation_only


def test_attached_monitor_displays_selectable_persistent_job_id():
    app = QApplication.instance() or QApplication([])
    job = _job()
    window = create_attached_run_monitor_window(job, _Source(None), autostart=False)

    assert window._job_id_label.text() == f"Job ID: {job.id}"
    assert not window._job_id_label.isHidden()
    window.close()


def test_new_run_displays_job_id_when_persistent_id_arrives():
    app = QApplication.instance() or QApplication([])
    window = SprkkrRunWindow(SimpleNamespace(directory="/tmp/calc"))
    ids = []
    window.persistentJobChanged.connect(ids.append)

    assert window._job_id_label.isHidden()
    window._persistent_job_changed_received("run-17")
    assert window._job_id_label.text() == "Job ID: run-17"
    assert not window._job_id_label.isHidden()
    assert ids == ["run-17"]
    window.close()


def test_attached_scf_monitor_shows_info_and_accepts_replayed_iterations():
    app = QApplication.instance() or QApplication([])
    window = create_attached_run_monitor_window(_job(), _Source(None), autostart=False)
    assert window._scf_progress is not None

    line = " 5 ERR 0.211E-02 0.208E-01 EF 0.76199 0.00307 D 14.537 M 2.1740 0.0536\n"
    completed = line + " ETOT -42.5 SCF - cycle not converged - continue\n"
    window._append_output(completed, "out")
    assert window._scf_progress.tracker.iterations[0].iteration == 5
    assert window._output.toPlainText().strip() == completed.strip()
    window.close()



def test_jxc_monitor_uses_task_specific_info_events():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="JXC", autostart=False
    )
    assert window._jxc_progress is not None
    assert [window._tabs.tabText(i) for i in range(2)] == ["INFO", "Output"]
    window._append_output(
        "  number of TAU(I,J)'s to be calculated: 89\n",
        "out",
    )
    assert window._jxc_progress.last_info.text() == (
        "Calculating 89 TAU(I,J) pairs"
    )
    window._append_output(
        "results written to file:Fe_JXC_XCPLTEN_Jij.dat\n",
        "out",
    )
    assert "JXC results written" in window._jxc_progress.last_info.text()
    assert "number of TAU(I,J)" in window._output.toPlainText()
    window.close()



def test_dos_monitor_uses_structured_progress_and_preserves_raw_output():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="DOS", autostart=False
    )
    assert window._dos_progress is not None
    assert [window._tabs.tabText(i) for i in range(2)] == ["INFO", "Output"]
    assert not window._dos_progress.progress.isVisible()

    window._append_output("13/300 do", "out")
    assert window._dos_progress.last_info.text() == "Preparing DOS calculation..."
    window._append_output("ne.\n14/300 done.\n", "out")
    assert window._dos_progress.last_info.text() == "14/300 done."
    assert window._dos_progress.progress.maximum() == 300
    assert window._dos_progress.progress.value() == 14
    assert window._output.toPlainText() == "13/300 done.\n14/300 done.\n"

    window._append_output("15/300 done.\n", "stderr")
    assert window._dos_progress.progress.value() == 14
    window.close()
