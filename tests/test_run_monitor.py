"""Tests for the generic run monitor and persistent attach adapter."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPlainTextEdit

from ase2sprkkr_gui.gui.application.execution_cancellation import RunCancellation
from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJob
from ase2sprkkr_gui.gui.dialogs.attached_run import (
    AttachedRunWorker,
    create_attached_run_monitor_window,
)
from ase2sprkkr_gui.gui.dialogs.run_calculation import SprkkrRunWindow
from ase2sprkkr_gui.gui.dialogs.run_display import MAX_LOGICAL_LINE_LENGTH, MAX_OUTPUT_BLOCKS
from ase2sprkkr_gui.gui.dialogs.run_monitor import RunMonitorWindow


class _Source:

    def __init__(self, result):
        self.result = result
        self.attach_calls = []

    def job_changed(self, job_id):
        pass

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


def test_detached_monitor_close_hides_without_cancelling():
    app = QApplication.instance() or QApplication([])

    class RunningThread:
        @staticmethod
        def isRunning():
            return True

    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)
    window._cancellation.register(SimpleNamespace(detached=True))
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


def test_monitor_header_aligns_two_label_value_groups_per_row():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda _cancellation: object(),
        header_fields=(
            ("Directory", "/tmp/Project/New"),
            ("Execution", "Local execution · direct"),
            ("Executables", "/home/user/kkr/bin"),
            ("MPI", "automatic"),
        ),
        initial_status="Running",
        autostart=False,
    )
    grid = window._header_grid
    assert [grid.itemAtPosition(0, col).widget().text() for col in range(4)] == [
        "Directory:", "/tmp/Project/New",
        "Execution:", "Local execution · direct",
    ]
    assert [grid.itemAtPosition(1, col).widget().text() for col in range(4)] == [
        "Executables:", "/home/user/kkr/bin", "MPI:", "automatic",
    ]
    assert [grid.itemAtPosition(2, col).widget().text() for col in range(4)] == [
        "Job ID:", "", "Status:", "Running",
    ]
    assert grid.itemAtPosition(0, 1).widget().wordWrap()
    assert window._job_id_title.isHidden()
    window.set_job_id("run-123")
    assert not window._job_id_title.isHidden()
    assert window._job_id_label.text() == "run-123"
    window._set_status("Finished")
    assert window._status.text() == "Finished"
    window.close()


def test_attached_monitor_displays_selectable_persistent_job_id():
    app = QApplication.instance() or QApplication([])
    job = _job()
    window = create_attached_run_monitor_window(job, _Source(None), autostart=False)

    assert window._job_id_label.text() == job.id
    assert not window._job_id_label.isHidden()
    grid = window._header_grid
    assert grid.itemAtPosition(0, 0).widget().text() == "Directory:"
    assert grid.itemAtPosition(0, 2).widget().text() == "Execution:"
    window.close()


def test_new_run_displays_job_id_when_persistent_id_arrives():
    app = QApplication.instance() or QApplication([])
    window = SprkkrRunWindow(SimpleNamespace(directory="/tmp/calc"))
    ids = []
    window.persistentJobChanged.connect(ids.append)

    assert window._job_id_label.isHidden()
    window._persistent_job_changed_received(SimpleNamespace(id="run-17", store=None))
    assert window._job_id_label.text() == "run-17"
    assert not window._job_id_label.isHidden()
    assert ids == ["run-17"]
    window.close()


def test_attached_scf_monitor_shows_info_and_accepts_replayed_iterations():
    app = QApplication.instance() or QApplication([])
    window = create_attached_run_monitor_window(_job(), _Source(None), autostart=False)
    assert window._info_page is not None

    line = " 5 ERR 0.211E-02 0.208E-01 EF 0.76199 0.00307 D 14.537 M 2.1740 0.0536\n"
    completed = line + " ETOT -42.5 SCF - cycle not converged - continue\n"
    window._append_output(completed, "out")
    assert window._info_page.tracker.iterations[0].iteration == 5
    assert window._output.toPlainText().strip() == completed.strip()
    window.close()



def test_jxc_monitor_uses_task_specific_info_events():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="JXC", autostart=False
    )
    assert window._info_page is not None
    assert [window._tabs.tabText(i) for i in range(2)] == ["INFO", "Output"]
    window._append_output(
        "  number of TAU(I,J)'s to be calculated: 89\n",
        "out",
    )
    assert window._info_page.last_info.text() == (
        "Calculating 89 TAU(I,J) pairs"
    )
    window._append_output(
        "results written to file:Fe_JXC_XCPLTEN_Jij.dat\n",
        "out",
    )
    assert "JXC results written" in window._info_page.last_info.text()
    assert "number of TAU(I,J)" in window._output.toPlainText()
    window.close()



def test_dos_monitor_uses_structured_progress_and_preserves_raw_output():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="DOS", autostart=False
    )
    assert window._info_page is not None
    assert [window._tabs.tabText(i) for i in range(2)] == ["INFO", "Output"]
    assert window._info_page.progress.isHidden()

    window._append_output("13/300 do", "out")
    assert window._info_page.last_info.text() == "Preparing DOS calculation..."
    window._append_output("ne.\n14/300 done.\n", "out")
    assert window._info_page.last_info.text() == "14/300 done."
    assert window._info_page.progress.maximum() == 300
    assert window._info_page.progress.value() == 14
    assert window._output.toPlainText() == "13/300 done.\n14/300 done.\n"

    window._append_output("15/300 done.\n", "stderr")
    assert window._info_page.progress.value() == 14
    window.close()


def test_run_monitor_copy_and_save_export_complete_raw_output(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)
    assert not window._copy_output_btn.isEnabled()
    assert not window._save_output_btn.isEnabled()
    assert not window._copy_output_btn.icon().isNull()
    assert not window._save_output_btn.icon().isNull()

    # Screen limits and synthetic line wrapping must not affect exports.
    initial = "first line\n" + "".join(
        f"line {n}\n" for n in range(MAX_OUTPUT_BLOCKS + 10)
    ) + "x" * (MAX_LOGICAL_LINE_LENGTH + 5)
    window._append_output(initial, "out")
    window._append_output("\nstderr ✓\n", "stderr")
    expected = initial + "\nstderr ✓\n"
    assert "first line" not in window._output.toPlainText()
    assert window._copy_output_btn.isEnabled()
    assert window._save_output_btn.isEnabled()

    window._copy_output_btn.click()
    assert app.clipboard().text() == expected

    destination = tmp_path / "command-output.txt"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *args, **kwargs: (str(destination), "Text files (*.txt)"),
    )
    window._save_output_btn.click()
    assert destination.read_text(encoding="utf-8") == expected

    # Saving must restore the append position for later live output.
    window._append_output("later\n", "out")
    window._save_output_btn.click()
    assert destination.read_text(encoding="utf-8") == expected + "later\n"
    window.close()


def test_run_monitor_save_cancel_and_error_do_not_discard_output(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)
    window._append_output("kept\n", "out")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    window._save_output_btn.click()
    assert window._save_output_btn.isEnabled()

    missing = tmp_path / "missing" / "command-output.txt"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(missing), ""),
    )
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    window._save_output_btn.click()
    assert len(warnings) == 1
    assert warnings[0][0] == "Could Not Save Output"
    window._append_output("more\n", "out")
    window._copy_output_btn.click()
    assert app.clipboard().text() == "kept\nmore\n"
    window.close()



def test_dos_info_native_energy_blocks_count_points_not_atomic_components():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(),
        task_name="DOS",
        dos_total_points=300,
        autostart=False,
    )
    window._append_output("13 E= 0.4061 0.0100 IT= 1 Ti\n", "out")
    window._append_output("13 E= 0.4061 0.0100 IT= 2 Co\n", "out")
    assert window._info_page.last_info.text() == "Calculating density of states..."
    assert window._info_page.progress.isHidden()

    window._append_output("14 E= 0.4081 0.0100 IT= 1 Ti\n", "out")
    assert window._info_page.last_info.text() == "13/300 done."
    assert window._info_page.progress.maximum() == 300
    assert window._info_page.progress.value() == 13
    assert not window._info_page.progress.isHidden()
    window._append_output("14 E= 0.4081 0.0100 IT= 2 Co\n", "out")
    assert window._info_page.progress.value() == 13
    window.close()


def test_dos_info_unknown_total_does_not_show_percentage():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="DOS", autostart=False
    )
    window._append_output("13 E= 0.4061 0.0100 IT= 1 Ti\n", "out")
    window._append_output("14 E= 0.4081 0.0100 IT= 1 Ti\n", "out")
    assert window._info_page.last_info.text() == "13 energy points done."
    assert window._info_page.progress.isHidden()
    window.close()


def test_finished_monitor_adds_file_named_tabs_and_renders_selected_plot(_qt_application):
    from types import SimpleNamespace
    from matplotlib import pyplot as plt
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    from ase2sprkkr_gui.gui.dialogs.run_results import ResultFilePage, ResultPlotPage

    calls = []

    class PlotFile:
        name = "dos"
        display_name = "Density of states"
        info = "DOS data"

        def value_label(self):
            return "<dos file>"

        def actions(self):
            return ("plot",)

        def path(self):
            return "/tmp/example.dos"

        def parsed_file(self):
            return self

        def plot(self, *, show):
            calls.append(show)
            plt.figure()

    value = PlotFile()
    result = SimpleNamespace(
        output_values={"dos": value}, result_values={"dos": value}
    )
    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Output"
    ]
    from ase2sprkkr_gui.gui.application.result_loading import LoadedResult

    loaded = LoadedResult(
        result=result, output_path=None, potential_path=None, atoms=None,
        potential_error=None, directory=None,
    )
    window._on_finished(loaded)

    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Output", "Result Overview", "example.dos"
    ]
    page = window._tabs.widget(2)
    assert isinstance(page, ResultFilePage)
    assert isinstance(page.plot_page, ResultPlotPage)
    assert window._tabs.currentWidget() is page
    assert not calls  # only render after the event loop can show the new tab

    _qt_application.processEvents()
    assert calls == [False]
    assert page.findChildren(FigureCanvasQTAgg)
    window._tabs.setCurrentIndex(0)
    window._tabs.setCurrentIndex(2)
    _qt_application.processEvents()
    assert calls == [False]  # a second selection must not regenerate the plot
    window.close()


def test_monitor_formats_native_run_state_and_history_label():
    from kkrunner import RunState
    from ase2sprkkr_gui.gui.dialogs.run_monitor import state_label

    assert state_label(RunState.POSTPROCESSING) == "Postprocessing"
    assert state_label("imported") == "Imported"


def test_finished_monitor_keeps_non_plottable_outputs_as_file_tabs(_qt_application):
    from types import SimpleNamespace

    from ase2sprkkr_gui.gui.dialogs.run_results import ResultFilePage

    class File:
        name = "output"
        display_name = "Output file"
        info = ""

        def value_label(self):
            return "example.out"

        def actions(self):
            return ("open",)

    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)
    value = File()
    window._on_finished(SimpleNamespace(
        output_values={"output": value}, result_values={"output": value}
    ))
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Output", "Result Overview", "example.out"
    ]
    page = window._tabs.widget(2)
    assert isinstance(page, ResultFilePage)
    assert page.plot_page is None
    assert window._tabs.currentWidget() is page
    window.close()


def test_all_output_files_get_one_tab_only_after_completion(_qt_application):
    from types import SimpleNamespace

    from ase2sprkkr_gui.gui.dialogs.run_results import ResultFilePage, ResultPlotPage

    class File:
        def __init__(self, filename, *, can_plot):
            self.filename = filename
            self.name = filename
            self.display_name = filename
            self.info = ""
            self.can_plot = can_plot

        def value_label(self):
            return self.filename

        def actions(self):
            return ("plot",) if self.can_plot else ("open",)

        def path(self):
            return "/tmp/" + self.filename

    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="JXC", autostart=False
    )
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "INFO", "Output"
    ]
    result = SimpleNamespace(output_values={
        "first": File("first.agr", can_plot=True),
        "second": File("second.dat", can_plot=True),
        "third": File("third.log", can_plot=False),
    })
    result.result_values = result.output_values
    window._on_finished(result)
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "INFO", "Output", "Result Overview", "first.agr", "second.dat", "third.log"
    ]
    assert window._tabs.tabText(window._tabs.currentIndex()) == "first.agr"
    assert all(isinstance(window._tabs.widget(i), ResultFilePage) for i in (3, 4, 5))
    assert all(
        isinstance(window._tabs.widget(i).plot_page, ResultPlotPage)
        for i in (3, 4)
    )
    assert window._tabs.widget(5).plot_page is None
    assert not window._tabs.widget(3).plot_page._rendered
    assert not window._tabs.widget(4).plot_page._rendered
    window.close()


def test_finished_without_output_values_does_not_create_results_tab(_qt_application):
    from types import SimpleNamespace

    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)
    window._on_finished(SimpleNamespace(output_values={}, result_values={}))
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Output"
    ]
    assert window._completed_pages.execution_widget is None
    window.close()


def test_task_metadata_selects_meaningful_result_tabs_only(_qt_application):
    from types import SimpleNamespace

    class File:
        info = ""
        def __init__(self, filename):
            self.name = filename
            self.display_name = filename
            self.filename = filename

        def value_label(self):
            return self.filename

        def actions(self):
            return ("open",)

        def path(self):
            return "/tmp/" + self.filename

    scientific = File("Fe_DOS.dos")
    auxiliary = File("Fe.pot_new")
    log = File("Fe_DOS.out")
    result = SimpleNamespace(
        output_values={"DOS": scientific, "potential": auxiliary, "output": log},
        result_values={"DOS": scientific},
    )

    window = RunMonitorWindow(lambda _: object(), autostart=False)
    window._on_finished(result)
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Output", "Result Overview", "Fe_DOS.dos"
    ]
    assert window._tabs.tabText(window._tabs.currentIndex()) == "Fe_DOS.dos"
    all_files = window._completed_pages.execution_widget._current_presentation
    assert len(all_files.values) == 3
    assert {value.summary for value in all_files.values} == {
        "Fe_DOS.dos", "Fe.pot_new", "Fe_DOS.out"
    }
    window.close()


def test_task_with_auxiliary_outputs_only_gets_no_result_tabs(_qt_application):
    from types import SimpleNamespace

    class File:
        name = "new potential"
        display_name = "new potential"
        info = ""

        def value_label(self):
            return "Fe.pot_new"

        def actions(self):
            return ("open",)

    result = SimpleNamespace(
        output_values={"potential": File()},
        result_values={},
    )
    window = RunMonitorWindow(lambda _: object(), autostart=False)
    window._on_finished(result)
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Output", "Result Overview"
    ]
    assert window._completed_pages.execution_widget is not None
    window.close()


def test_native_auxiliary_files_are_not_scientific_result_tabs(_qt_application, tmp_path):
    from ase2sprkkr import TaskResult

    (tmp_path / "Fe.pot_new").touch()
    result = TaskResult(None, None, tmp_path, task_name="SCF")
    result.files.add_file("converged", "Fe.pot_new")
    window = RunMonitorWindow(lambda _: object(), autostart=False)
    window._on_finished(result)
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Output", "Result Overview"
    ]
    assert window._completed_pages.execution_widget._row_count == 1
    window.close()


def test_saved_result_window_reuses_completed_run_pages(_qt_application):
    from ase2sprkkr_gui.gui.dialogs.run_results import CompletedResultWindow

    class OutputFile:
        name = "DOS"
        info = "Density of states"
        display_name = "DOS"

        def value_label(self):
            return "DOS file"

        def actions(self):
            return ()

        def path(self):
            return "/tmp/example.dos"

    value = OutputFile()
    result = SimpleNamespace(
        task_name="unknown", output_values={"DOS": value},
        result_values={"DOS": value},
    )
    window = CompletedResultWindow(result, title="Saved DOS results")

    assert window.windowTitle() == "Saved DOS results"
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Output", "Result Overview", "example.dos",
    ]
    assert "not been loaded" in window._output.toPlainText()
    window.show()
    _qt_application.processEvents()
    window.close()
    _qt_application.processEvents()


def test_reopened_spleed_result_restores_info_and_stdout(tmp_path, _qt_application):
    from ase2sprkkr_gui.gui.application.saved_output import read_saved_output
    from ase2sprkkr_gui.gui.dialogs.run_results import CompletedResultWindow

    output = tmp_path / "Fe2_SPLEED.out"
    row = "  ".join(f"{value:12.5E}" for value in (11., .5, 0., 0., 0., 0.))
    output.write_text(
        row + "\nnext output section\n"
        "program stopped regularly via STOP_REGULAR\n",
        encoding="utf-8",
    )
    from ase2sprkkr import TaskResult
    result = TaskResult(None, None, tmp_path, output_file=output, task_name="SPLEED")
    saved_output = read_saved_output(result)
    assert saved_output.warning is None

    window = CompletedResultWindow(result, saved_output=saved_output)
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "INFO", "Output", "Result Overview",
    ]
    assert "SPLEED calculation finished" in window._info_page.last_info.text()
    assert "spectral output" in window._info_page.events.toPlainText()
    assert window._output.toPlainText().splitlines()[0] == row
    window.close()


def test_reopened_spleed_shows_warning_instead_of_replaying_missing_output(
    tmp_path, _qt_application,
):
    from ase2sprkkr_gui.gui.application.saved_output import read_saved_output
    from ase2sprkkr_gui.gui.dialogs.run_results import CompletedResultWindow

    from ase2sprkkr import TaskResult
    result = TaskResult(None, None, tmp_path, output_file="missing.out", task_name="SPLEED")
    window = CompletedResultWindow(result, saved_output=read_saved_output(result))
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "INFO", "Output", "Result Overview",
    ]
    assert "unavailable" in window._info_page.text()
    assert "unavailable" in window._output.toPlainText()
    window.close()


def test_reopening_hidden_active_monitor_restores_completion_presentation():
    app = QApplication.instance() or QApplication([])

    class RunningThread:
        @staticmethod
        def isRunning():
            return True

    window = RunMonitorWindow(lambda _: object(), autostart=False, observation_only=True)
    window._thread = RunningThread()
    window.show()
    app.processEvents()
    window.close()
    assert window._close_requested

    window.show()
    app.processEvents()
    assert not window._close_requested
    assert window.isVisible()
    window._thread = None
    window.close()


def test_saved_dos_replay_matches_live_monitor_with_mpi_energy_total(_qt_application):
    """Restored ENERGY.NE must yield the same progress events as live stdout."""
    from ase2sprkkr_gui.gui.application.saved_output import SavedOutput
    from ase2sprkkr_gui.gui.application.result_store import ResultRecord
    from ase2sprkkr_gui.gui.dialogs.run_results import CompletedResultWindow
    from ase2sprkkr_gui.gui.dialogs.run_display import info_page_settings

    parameters = SimpleNamespace(ENERGY=SimpleNamespace(NE=lambda: 300))
    result = SimpleNamespace(
        task_name="DOS", output_values={}, result_values={},
    )
    record = ResultRecord(
        id=1, result=result, task_name="DOS", structure_id=None,
        input_state_id=None, output_state_id=None, input_parameters=parameters,
        directory=None, result_files={},
    )
    stdout = (
        "MPI calculation with NPROCS = 4\n"
        " 226 E= 0.4061 0.0100 IT= 1 Fe\n"
        " 227 E= 0.4081 0.0100 IT= 1 Fe\n"
        " 228 E= 0.4101 0.0100 IT= 1 Fe\n"
        "program stopped regularly via STOP_REGULAR\n"
    )
    live = RunMonitorWindow(
        lambda _: object(), task_name="DOS",
        **info_page_settings("DOS", parameters), autostart=False,
    )
    live._append_output(stdout, "out")
    reopened = CompletedResultWindow(
        result, record=record, saved_output=SavedOutput(text=stdout),
    )
    assert reopened._info_page.progress.maximum() == 300
    assert reopened._info_page.progress.value() == 300
    assert reopened._info_page.progress.isHidden() is False
    assert reopened._info_page.last_info.text() == "300/300 done."
    assert reopened._info_page.events.toPlainText() == live._info_page.events.toPlainText()
    live.close()
    reopened.close()


def test_saved_xas_replay_recovers_input_energy_total(_qt_application):
    from ase2sprkkr_gui.gui.application.saved_output import SavedOutput
    from ase2sprkkr_gui.gui.dialogs.run_results import CompletedResultWindow

    parameters = SimpleNamespace(ENERGY=SimpleNamespace(NE=lambda: 180))
    result = SimpleNamespace(
        task_name="XAS", input_parameters=parameters,
        output_values={}, result_values={},
    )
    stdout = " LOOP 1   75 E= 0.735 0.001  IQ= 1  IT= 1\n"
    window = CompletedResultWindow(result, saved_output=SavedOutput(text=stdout))
    assert window._info_page.progress.maximum() == 180
    assert window._info_page.progress.value() == 74
    assert window._info_page.last_info.text() == "Calculating XAS energy 75/180..."
    window.close()


def test_saved_dos_partial_output_displays_info_warning(_qt_application):
    from ase2sprkkr_gui.gui.application.saved_output import SavedOutput
    from ase2sprkkr_gui.gui.dialogs.run_results import CompletedResultWindow

    params = SimpleNamespace(ENERGY=SimpleNamespace(NE=lambda: 300))
    result = SimpleNamespace(
        task_name="DOS", input_parameters=params,
        output_values={}, result_values={},
    )
    window = CompletedResultWindow(
        result,
        saved_output=SavedOutput(
            text="MPI calculation with NPROCS = 4\n"
                 " 226 E= 0.4061 0.0100 IT= 1 Fe\n",
            truncated=True,
        ),
    )
    assert window._info_page.progress.maximum() == 300
    assert "Partial INFO" in window._info_page.layout().itemAt(0).widget().text()
    assert "Earlier output was omitted" in window._output.toPlainText()
    window.close()


def test_closing_monitor_cancels_pending_plot_render(_qt_application, monkeypatch):
    from PyQt6.QtCore import QCoreApplication, QEvent
    from ase2sprkkr.outputs import TaskResult
    from ase2sprkkr_gui.gui.dialogs.run_results import ResultFilePage

    result = TaskResult(None, None, None, task_name="DOS")
    result.files.add_file("DOS", "plot.dos", "dos")
    value = result.files["DOS"]
    monkeypatch.setattr(value, "actions", lambda: ("plot",))
    monkeypatch.setattr(TaskResult, "result_values", property(lambda self: {"DOS": value}))
    calls = []
    monkeypatch.setattr(ResultFilePage, "render", lambda self: calls.append(self))
    window = RunMonitorWindow(lambda _: object(), autostart=False)
    window._on_finished(result)
    assert window._tabs.tabText(window._tabs.currentIndex()) == "plot.dos"
    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _qt_application.processEvents()
    assert not calls


def test_closing_result_window_cancels_pending_plot(_qt_application):
    from PyQt6.QtCore import QCoreApplication, QEvent
    from ase2sprkkr_gui.gui.dialogs.run_results import CompletedResultWindow

    calls = []

    class PlotFile:
        name = "DOS"
        display_name = "DOS"
        info = ""

        def value_label(self):
            return "DOS"

        def actions(self):
            return ("plot",)

        def path(self):
            return "/tmp/example.dos"

        def parsed_file(self):
            calls.append("parsed")
            raise AssertionError("A closed window must not parse a plot")

    value = PlotFile()
    window = CompletedResultWindow(SimpleNamespace(
        task_name="unknown", output_values={"DOS": value}, result_values={"DOS": value},
    ))
    window.close()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    _qt_application.processEvents()
    assert not calls


def test_closing_owned_running_monitor_requires_confirmation_and_stops(monkeypatch, _qt_application):
    class RunningThread:
        running = True

        def isRunning(self):
            return self.running

    window = RunMonitorWindow(lambda _: object(), autostart=False)
    thread = RunningThread()
    window._thread = thread
    window.show()
    answers = iter((QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes))
    blocked = []

    def confirm(*_args):
        blocked.append(window.shutdown_blocked)
        return next(answers)

    monkeypatch.setattr(QMessageBox, "question", confirm)
    assert not window.close()
    assert window.isVisible()
    assert not window._cancellation.requested

    assert not window.close()
    assert window.isVisible()
    assert window._cancellation.requested
    assert blocked == [True, True]
    assert not window.shutdown_blocked

    thread.running = False
    window._thread_finished()
    assert not window.isVisible()


def test_close_waits_for_refresh_result_before_destroying_monitor(_qt_application):
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6 import sip

    pending = []

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            pending.append(self.target)

    window = RunMonitorWindow(
        lambda _: object(), autostart=False, background_thread_factory=DeferredThread,
    )
    window._cancellation.register(SimpleNamespace(state="running", detached=True))
    window.show()
    received = []
    window.refreshed.connect(received.append)
    window._refresh_btn.click()
    assert not window._refresh_btn.isEnabled()

    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not sip.isdeleted(window)
    assert not window.isVisible()
    pending.pop()()
    assert received == ["Running"]
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(window)


def test_exit_waits_for_pending_refresh_even_without_run_thread(_qt_application):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QWidget
    from ase2sprkkr_gui.main import GuiApplication

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            QTimer.singleShot(0, self.target)

    parent = QWidget()
    window = RunMonitorWindow(
        lambda _: object(), autostart=False, background_thread_factory=DeferredThread,
    )
    window._cancellation.register(SimpleNamespace(state="finished", detached=True))
    received = []
    window.refreshed.connect(received.append)
    window._refresh_btn.click()
    gui = SimpleNamespace(
        persistent_jobs_controller=SimpleNamespace(monitor_windows=lambda: (window,)),
        _workflow_window=parent,
    )
    assert received == []
    assert GuiApplication._stop_run_monitors_for_exit(gui)
    assert received == ["Finished"]
    window._on_refresh()
    assert not window._background_operations
    window.close()
    parent.close()


def test_failed_refresh_releases_monitor_and_allows_exit(_qt_application):
    from asyncio import CancelledError
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QWidget
    from ase2sprkkr_gui.main import GuiApplication

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            QTimer.singleShot(0, self.target)

    class Runner:
        detached = True

        @property
        def state(self):
            raise CancelledError("status request cancelled")

    parent = QWidget()
    window = RunMonitorWindow(
        lambda _: object(), autostart=False, background_thread_factory=DeferredThread,
    )
    window._cancellation.register(Runner())
    received = []
    window.refreshed.connect(received.append)
    window._refresh_btn.click()
    gui = SimpleNamespace(
        persistent_jobs_controller=SimpleNamespace(monitor_windows=lambda: (window,)),
        _workflow_window=parent,
    )
    assert GuiApplication._stop_run_monitors_for_exit(gui)
    assert received == ["Refresh failed: status request cancelled"]
    assert not window._background_operations
    window.close()
    parent.close()
