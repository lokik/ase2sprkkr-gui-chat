"""Contract for the shared text-only INFO page."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.dialogs.info_progress import InfoProgressPage


def test_info_page_filters_stderr_and_only_displays_formatted_parser_events():
    app = QApplication.instance() or QApplication([])

    class Parser:
        def __init__(self, *, on_info):
            self.on_info = on_info
            self.received = []

        def feed(self, text):
            self.received.append(text)
            if text != "ignored":
                self.on_info(kind="event", data=text)

        def format_info(self, kind, data):
            assert kind == "event"
            return f"Info: {data}"

    page = InfoProgressPage(parser_factory=Parser, initial_info="Preparing...")
    assert page.last_info.text() == "Preparing..."
    page.consume("not parsed", "stderr")
    page.consume("ignored", "out")
    assert page.last_info.text() == "Preparing..."
    page.consume("start", "stdout")
    assert page.parser.received == ["ignored", "start"]
    assert page.last_info.text() == "Info: start"
    assert page.events.toPlainText() == "Info: start"
    assert page.events.maximumBlockCount() == 1024
    page.close()

def test_common_progress_widget_handles_exact_approximate_rank_and_unknown():
    from ase2sprkkr.outputs.progress import ProgressInfo

    app = QApplication.instance() or QApplication([])
    class Parser:
        def __init__(self, on_info):
            self.on_info = on_info
        def feed(self, text):
            pass
        def format_info(self, kind, data):
            return None

    page = InfoProgressPage(parser_factory=Parser, initial_info="Preparing...")
    assert page.progress.isHidden()
    page._on_info(kind="progress", data=ProgressInfo(3, None))
    assert page.progress.isHidden()
    page._on_info(kind="progress", data=ProgressInfo(2, 10, estimated=True))
    assert not page.progress.isHidden()
    assert page.progress.value() == 2
    assert page.progress.maximum() == 10
    assert page.progress.format() == "≈%p%"
    page._on_info(
        kind="progress",
        data=ProgressInfo(3, 10, label="Reporting MPI rank: energy loops"),
    )
    assert page.progress_label.text() == "Reporting MPI rank: energy loops"
    assert not page.progress_label.isHidden()
    assert page.progress.format() == "%p%"
    page._on_info(kind="progress", data=ProgressInfo(10, 10, unit="energies"))
    assert page.progress.value() == 10
    assert page.progress.format() == "%v / %m energies (%p%)"
    assert page.progress_label.isHidden()
    assert page.last_info.text() == "Preparing..."  # progress is not a duplicate log line
    page.close()
