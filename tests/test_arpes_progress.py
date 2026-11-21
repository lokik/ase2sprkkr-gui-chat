"""ARPES calculation monitor displays only supported native SPEC.out milestones."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.dialogs.run_monitor import RunMonitorWindow


def test_arpes_info_tab_receives_native_spec_setup_and_regular_stop():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="ARPES", autostart=False
    )
    assert [window._tabs.tabText(i) for i in range(2)] == ["INFO", "Output"]
    page = window._arpes_progress
    assert page is not None
    assert window._spleed_progress is None
    assert page.last_info.text() == "Preparing ARPES calculation..."

    stdout = (
        "lattice constant      :     4.6298300\n"
        "vacuum-wavevector of the photonfield:   0.00000000   0.00000000   0.00611428\n"
        "stookes-vector    of the photonfield  0.10000E+01  100.000%    0.000%    0.000%\n"
        "program stopped regularly via STOP_REGULAR\n"
    )
    window._append_output(stdout[:43], "out")
    window._append_output(stdout[43:119], "out")
    window._append_output(stdout[119:], "out")
    assert page.last_info.text() == "ARPES calculation finished"
    output = page.events.toPlainText()
    assert "Surface lattice constant: 4.62983" in output
    assert "Photon wavevector" in output
    assert "Stokes vector" in output
    assert "100%" in output  # polarization, not calculation progress
    assert "completed" not in output
    assert "lattice constant" in window._output.toPlainText()
    window.close()


def test_arpes_info_ignores_stderr_and_does_not_finish_on_eof():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="arpes", autostart=False
    )
    window._append_output(
        "program stopped regularly via STOP_REGULAR\n", "stderr"
    )
    assert window._arpes_progress.last_info.text() == "Preparing ARPES calculation..."
    window._append_output("lattice constant: 5.0\n", "stdout")
    assert window._arpes_progress.last_info.text() == "Surface lattice constant: 5"
    assert "finished" not in window._arpes_progress.events.toPlainText()
    window.close()
