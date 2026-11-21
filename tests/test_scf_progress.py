"""SCF live INFO monitoring from the native ERR/EF output stream."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTabWidget

from ase2sprkkr_gui.gui.dialogs.run_calculation import SprkkrRunWindow
from ase2sprkkr_gui.gui.dialogs.run_monitor import RunMonitorWindow
from ase2sprkkr_gui.gui.dialogs.scf_progress import (
    ScfProgressTracker,
    parse_scf_iteration,
)


def _native_line(number=6, fermi="0.76199", field="0.208E-01"):
    return (
        f" {number} ERR 0.211E-02 {field} EF {fermi} "
        "0.00307 D 14.537 M 2.1740 0.0536\n"
    )


def test_native_scf_error_is_worst_of_two_convergence_components():
    item = parse_scf_iteration(_native_line().strip())
    assert item.number == 6
    assert item.fermi_energy == 0.76199
    assert item.error == 0.0208
    assert parse_scf_iteration("normal stdout") is None
    assert parse_scf_iteration("6 ERR ******* 0.02 EF 0.7") is None


def test_scf_parser_supports_fortran_exponents_and_stream_fragments():
    tracker = ScfProgressTracker()
    line = _native_line(1, "0.76199D+00", "0.208D-01")
    assert not tracker.feed(line[:12], "out")
    assert not tracker.iterations
    assert tracker.feed(line[12:], "out")
    assert tracker.iterations[0].number == 1
    assert tracker.iterations[0].fermi_energy == 0.76199
    assert tracker.iterations[0].error == 0.0208
    assert tracker.last_info_line == line.strip()


def test_scf_parser_ignores_stderr_and_replaces_replayed_iteration():
    tracker = ScfProgressTracker()
    assert not tracker.feed(_native_line(1), "stderr")
    assert not tracker.iterations
    assert tracker.feed(_native_line(1), "out")
    assert tracker.feed(_native_line(1, "0.9"), "out")
    assert [(x.number, x.fermi_energy) for x in tracker.iterations] == [(1, 0.9)]
    assert tracker.feed(_native_line(2, "0.8"), "out")
    assert [x.number for x in tracker.iterations] == [1, 2]
    assert tracker.feed(_native_line(1, "0.7"), "out")
    assert [x.number for x in tracker.iterations] == [1]


def test_formatted_info_line_updates_status_without_erasing_plot_history():
    tracker = ScfProgressTracker()
    tracker.feed(_native_line(3), "out")
    assert tracker.feed("Iteration     3 error 2.11e-3 spin moment: 0.1\n", "info")
    assert tracker.last_info_line.startswith("Iteration")
    assert len(tracker.iterations) == 1


def test_scf_run_has_info_page_and_raw_output_is_preserved():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), scf=True, autostart=False
    )
    assert isinstance(window._tabs, QTabWidget)
    assert [window._tabs.tabText(i) for i in range(2)] == ["INFO", "Output"]
    assert window._tabs.currentIndex() == 0

    window._append_output(_native_line(6), "out")
    assert window._output.toPlainText().strip() == _native_line(6).strip()
    progress = window._scf_progress
    assert progress.tracker.iterations[0].fermi_energy == 0.76199
    assert "6 ERR" in progress.last_info.text()
    progress._redraw()
    assert list(progress.fermi_line.get_xdata()) == [6]
    assert list(progress.error_line.get_ydata()) == [0.0208]
    window.close()


def test_non_scf_run_keeps_original_output_only_layout():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(lambda cancellation: object(), autostart=False)
    assert window._scf_progress is None
    assert not hasattr(window, "_tabs")
    window.close()


def test_new_scf_calculation_enables_info_page_before_start():
    app = QApplication.instance() or QApplication([])
    request = SimpleNamespace(
        directory="/tmp/scf", input_parameters=SimpleNamespace(task_name="SCF")
    )
    window = SprkkrRunWindow(request)
    assert window._scf_progress is not None
    window.close()
