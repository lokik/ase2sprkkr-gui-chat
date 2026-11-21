"""Live ARPES INFO page using native SPEC.out parser events from ase2sprkkr."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from ase2sprkkr.outputs.readers.arpes import ArpesProgressParser


class ArpesProgressPage(QWidget):
    """Display observed ARPES setup and completion, not estimated percentages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.parser = ArpesProgressParser(on_info=self._on_info)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Latest INFO", self))
        self.last_info = QLabel("Preparing ARPES calculation...", self)
        self.last_info.setWordWrap(True)
        self.last_info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.last_info)

        self.events = QPlainTextEdit(self)
        self.events.setReadOnly(True)
        self.events.setMaximumBlockCount(1024)
        layout.addWidget(self.events, 1)

    def _on_info(self, *, kind: str, data: object) -> None:
        line = self.parser.format_info(kind, data)
        if line is not None:
            self.last_info.setText(line)
            self.events.appendPlainText(line)

    def consume(self, text: str, kind: str) -> None:
        if kind.lower() not in {"err", "stderr"} and text:
            self.parser.feed(text)
