"""GUI XAS INFO tracks completed energies from actual XRAYSPEC stdout."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.dialogs.run_monitor import RunMonitorWindow


def loop(index):
    return (
        f" LOOP{1:2d}{index:5d} E={0.735:6.3f}{0.001:6.3f}"
        f"  IQ={1:2d}  IT={1:2d}\n"
    )


def test_xas_info_tab_uses_native_progress_and_completion():
    app = QApplication.instance() or QApplication([])
    monitor = RunMonitorWindow(
        lambda cancellation: object(),
        task_name="XAS", xas_total_points=180, autostart=False,
    )
    assert [monitor._tabs.tabText(i) for i in range(2)] == ["INFO", "Output"]
    page = monitor._info_page
    assert page is not None
    assert page.progress.isHidden()

    monitor._append_output("RATE-FILE : ( 7) Fe_L23.rat\n", "stdout")
    monitor._append_output("CORE QUANTUM-NUMBERS FOR IT=1: N=2 L=1\n", "stdout")
    source = loop(75)
    monitor._append_output(source[:9], "stdout")
    monitor._append_output(source[9:], "stdout")
    assert page.progress.value() == 74
    assert page.progress.maximum() == 180
    assert page.progress.format() == "%v / %m energies (%p%)"
    assert page.last_info.text() == "Calculating XAS energy 75/180..."
    assert "Core shell: n=2, l=1, atom type 1" in page.events.toPlainText()

    monitor._append_output(
        " X-RAY ABSORPTION-RATE CALCULATED FOR  180 ENERGY-POINTS"
        "   >>> FEIERABEND\n",
        "stdout",
    )
    assert page.progress.value() == 180
    monitor._append_output("finished in subroutine <XRAYSPEC>\n", "stdout")
    assert page.last_info.text() == "XAS calculation finished"
    monitor.close()


def test_xas_generic_stop_is_not_a_completed_spectrum():
    app = QApplication.instance() or QApplication([])
    monitor = RunMonitorWindow(
        lambda cancellation: object(),
        task_name="xas", xas_total_points=180, autostart=False,
    )
    monitor._append_output(loop(1), "stderr")
    page = monitor._info_page
    assert page.progress.isHidden()

    monitor._append_output("program stopped regularly via STOP_REGULAR\n", "stdout")
    assert page.progress.isHidden()
    assert page.last_info.text() == "Preparing XAS calculation..."
    monitor.close()


def test_xas_completion_without_known_input_ne():
    app = QApplication.instance() or QApplication([])
    monitor = RunMonitorWindow(
        lambda cancellation: object(), task_name="XAS", autostart=False,
    )
    page = monitor._info_page
    monitor._append_output(loop(3), "stdout")
    assert page.progress.isHidden()
    monitor._append_output(
        "X-RAY ABSORPTION-RATE CALCULATED FOR   30 ENERGY-POINTS"
        "   >>> FEIERABEND\n",
        "stdout",
    )
    assert page.progress.value() == 30
    assert page.progress.maximum() == 30
    monitor.close()
