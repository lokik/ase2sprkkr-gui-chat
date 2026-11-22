"""Reusable INFO widget for tasks reporting formatted text parser events."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget


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

        self.progress_label = QLabel(self)
        self.progress_label.hide()
        layout.addWidget(self.progress_label)

        self.progress = QProgressBar(self)
        self.progress.setTextVisible(True)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.events = QPlainTextEdit(self)
        self.events.setReadOnly(True)
        self.events.setMaximumBlockCount(1024)
        layout.addWidget(self.events, 1)

    def _on_info(self, *, kind: str, data: object) -> None:
        if kind == "progress":
            self._set_progress(data)
            return
        line = self.parser.format_info(kind, data)
        if line is not None:
            self.last_info.setText(line)
            self.events.appendPlainText(line)

    def _set_progress(self, data: object) -> None:
        """The shared numerical progress event has no task-specific GUI logic."""
        label = data.label
        self.progress_label.setVisible(bool(label))
        if label:
            self.progress_label.setText(label)

        # No known total means that a percentage would be fabricated.
        if data.total is None:
            self.progress.hide()
            return
        self.progress.setRange(0, data.total)
        prefix = "≈" if data.estimated else ""
        if data.unit:
            self.progress.setFormat(f"{prefix}%v / %m {data.unit} (%p%)")
        else:
            self.progress.setFormat(f"{prefix}%p%")
        self.progress.setValue(data.current)
        self.progress.show()

    def consume(self, text: str, kind: str) -> None:
        if text and kind.lower() not in {"err", "stderr"}:
            self.parser.feed(text)
