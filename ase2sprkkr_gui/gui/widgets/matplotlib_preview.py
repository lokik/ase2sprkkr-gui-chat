"""Expandable Matplotlib previews shared by structure-related GUI widgets.

A fullscreen dialog gets its own figure: a preview must retain its Qt parent,
mouse callbacks and editable state while the user inspects a larger view.
"""
from __future__ import annotations

from io import BytesIO
import pickle

from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg,
    NavigationToolbar2QT,
)
from matplotlib.figure import Figure
from matplotlib.image import imread
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget


def _fullscreen_figure(figure: Figure) -> Figure:
    """Clone plotted artists and 3D axes, without moving the original canvas."""
    try:
        return pickle.loads(pickle.dumps(figure, protocol=pickle.HIGHEST_PROTOCOL))
    except Exception:
        # Custom Matplotlib artists can contain non-pickleable state. The
        # preview must still expand, albeit as a non-interactive image.
        image = BytesIO()
        figure.savefig(image, format="png", dpi=max(150, figure.dpi))
        image.seek(0)
        snapshot = Figure(figsize=figure.get_size_inches())
        axes = snapshot.add_axes((0, 0, 1, 1))
        axes.imshow(imread(image, format="png"))
        axes.set_axis_off()
        return snapshot


class _FullscreenFigureDialog(QDialog):
    """Modal plot viewer with native Matplotlib navigation controls."""

    def __init__(self, figure: Figure, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Matplotlib preview")
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.canvas = FigureCanvasQTAgg(figure)
        controls.addWidget(NavigationToolbar2QT(self.canvas, self))
        controls.addStretch(1)
        close_button = QPushButton("Close (Esc)", self)
        close_button.clicked.connect(self.reject)
        controls.addWidget(close_button)
        layout.addLayout(controls)
        layout.addWidget(self.canvas, 1)

        # The canvas handles keyboard events itself, so do not rely solely on
        # QDialog's default Escape handling when the plot has focus.
        self._close_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._close_shortcut.activated.connect(self.reject)
        self.canvas.setFocus()


class PreviewFigureCanvas(FigureCanvasQTAgg):
    """Embedded Matplotlib canvas with a fullscreen double-click action."""

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            self.open_fullscreen()
            return
        super().mouseDoubleClickEvent(event)

    def open_fullscreen(self) -> None:
        dialog = _FullscreenFigureDialog(
            _fullscreen_figure(self.figure), self.window()
        )
        dialog.showFullScreen()
        dialog.exec()
