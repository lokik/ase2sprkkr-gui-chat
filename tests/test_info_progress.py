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
