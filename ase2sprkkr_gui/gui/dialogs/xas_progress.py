"""Live XAS INFO page based entirely on native XRAYSPEC parser events."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from ase2sprkkr.outputs.readers.xas import XasProgressParser


class XasProgressPage(QWidget):
    """The energy-loop header marks the START, not the end, of an energy."""

    def __init__(self, parent: QWidget | None = None, *, total_points: int | None = None):
        super().__init__(parent)
        self.parser = XasProgressParser(on_info=self._on_info, total_points=total_points)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Latest INFO", self))

        self.last_info = QLabel("Preparing XAS calculation...", self)
        self.last_info.setWordWrap(True)
        self.last_info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.last_info)

        self.progress = QProgressBar(self)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%v / %m energies (%p%)")
        self.progress.hide()
        layout.addWidget(self.progress)

        self.events = QPlainTextEdit(self)
        self.events.setReadOnly(True)
        self.events.setMaximumBlockCount(1024)
        layout.addWidget(self.events, 1)

    def _on_info(self, *, kind: str, data: object):
        line = self.parser.format_info(kind, data)
        if line is None:
            return
        self.last_info.setText(line)
        self.events.appendPlainText(line)
        if kind == "xas_energy" and data.total is not None:
            self.progress.setRange(0, data.total)
            self.progress.setValue(data.completed)
            self.progress.show()
        elif kind == "xas_complete":
            # Exact completion comes from XRAYSPEC FORMAT 99013, not STOP_REGULAR.
            self.progress.setRange(0, data)
            self.progress.setValue(data)
            self.progress.show()

    def consume(self, text: str, kind: str):
        if kind.lower() not in {"err", "stderr"} and text:
            self.parser.feed(text)
