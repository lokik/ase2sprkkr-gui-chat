"""SPLEED run monitor gets an INFO page from structured native events."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.dialogs.run_monitor import RunMonitorWindow


def diagram(rows):
    return "i(e)-diagram\n\n" + "".join(
        f"{energy:12.5E}  {intensity:12.5E}\n".replace("E", "D")
        for energy, intensity in rows
    )


def test_spleed_info_tab_tracks_native_diagram_without_percentages():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="SPLEED", autostart=False
    )
    assert [window._tabs.tabText(i) for i in range(2)] == ["INFO", "Output"]
    assert window._spleed_progress is not None
    assert window._scf_progress is None
    assert window._bsf_progress is None

    output = diagram([(11., 2.), (12., 3.)])
    window._append_output(output[:11], "stdout")
    window._append_output(output[11:], "stdout")
    window._append_output("end of table\nprogram stopped regularly via STOP_REGULAR\n", "stdout")
    page = window._spleed_progress

    assert page.last_info.text() == "SPLEED calculation finished"
    assert "SPLEED i(e)-diagram: 1 point read" in page.events.toPlainText()
    assert "SPLEED i(e)-diagram: 2 points read" in page.events.toPlainText()
    assert "%" not in page.events.toPlainText()
    assert "i(e)-diagram" in window._output.toPlainText()
    window.close()


def test_spleed_info_ignores_stderr_lines():
    app = QApplication.instance() or QApplication([])
    window = RunMonitorWindow(
        lambda cancellation: object(), task_name="spleed", autostart=False
    )
    window._append_output(diagram([(11., 2.)]), "stderr")
    assert window._spleed_progress.last_info.text() == "Preparing SPLEED calculation..."
    window.close()
