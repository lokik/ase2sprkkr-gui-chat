"""Reusable INFO widget for tasks reporting formatted text parser events."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget


class InfoProgressPage(QWidget):
    """Render formatted parser events; parsing and wording belong to ase2sprkkr."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        parser_factory: Callable[..., Any],
        initial_info: str,
    ) -> None:
        super().__init__(parent)
        self.parser = parser_factory(on_info=self._on_info)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Latest INFO", self))
        self.last_info = QLabel(initial_info, self)
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
        if text and kind.lower() not in {"err", "stderr"}:
            self.parser.feed(text)
