"""The live DOS INFO tab labels rank-zero MPI progress as approximate."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.dialogs.run_monitor import RunMonitorWindow


def test_live_mpi_dos_estimate_is_visibly_approximate():
    app = QApplication.instance() or QApplication([])
    dialog = RunMonitorWindow(
        lambda cancellation: object(),
        task_name="DOS",
        dos_total_points=200,
        autostart=False,
    )
    assert [dialog._tabs.tabText(i) for i in range(2)] == ["INFO", "Output"]
    page = dialog._dos_progress
    assert page is not None

    dialog._append_output("MPI calculation with NPROCS = 4\n", "stdout")
    dialog._append_output(" 151 E= 0.40 0.01 IT= 1 Fe\n", "stdout")
    assert page.progress.value() == 0
    assert page.progress.format() == "≈%p%"
    dialog._append_output(" 176 E= 0.42 0.01 IT= 1 Fe\n", "stdout")
    assert page.progress.value() == 100
    assert page.progress.maximum() == 200
    assert "~50% done" in page.last_info.text()

    dialog._append_output("program stopped regularly via STOP_REGULAR\n", "stdout")
    assert page.progress.value() == 200
    assert page.progress.format() == "%p%"
    dialog.close()


def test_native_serial_dos_progress_remains_exact():
    app = QApplication.instance() or QApplication([])
    dialog = RunMonitorWindow(
        lambda cancellation: object(),
        task_name="dos",
        dos_total_points=10,
        autostart=False,
    )
    dialog._append_output(" 1 E= 0.40 0.01 IT= 1 Fe\n", "stdout")
    dialog._append_output(" 2 E= 0.41 0.01 IT= 1 Fe\n", "stdout")
    page = dialog._dos_progress
    assert page.progress.value() == 1
    assert page.progress.format() == "%p%"
    assert "1/10 done." in page.last_info.text()
    dialog.close()
