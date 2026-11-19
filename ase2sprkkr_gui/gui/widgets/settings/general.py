"""General application settings: visualization and SPR-KKR defaults."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QHBoxLayout,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr_gui.gui.application.settings import ApplicationSettings
from ase2sprkkr_gui.gui.application.viewers import viewer_unavailability
from ase2sprkkr.physics.winger_seitz_radii import winger_seitz_radii


_DATASET_MODE_LABELS = (
    ("empty", "Leave unset (ase2sprkkr automatic)"),
    ("datetime", "Structure + date/time"),
    ("uuid", "Structure + UUID"),
)


def available_viewers() -> tuple[str, ...]:
    """Return viewer names understood by ASE."""
    from ase.visualize.viewers import VIEWERS

    return tuple(sorted(str(name) for name in VIEWERS))



class _AtomSizeExample(QWidget):
    """Static WSR illustration; no potential is available in Settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent = 100
        self._relative = True
        self.setMinimumHeight(100)

    def set_values(self, percent, relative):
        self._percent, self._relative = percent, relative
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mean = (winger_seitz_radii["C"] + winger_seitz_radii["Au"]) / 2
        y = self.height() / 2 - 12
        for x_fraction, symbol, color in (
            (.25, "C", "#55595d"), (.75, "Au", "#d8ab36")
        ):
            radius = 17.0 * self._percent / 100
            if self._relative:
                radius *= winger_seitz_radii[symbol] / mean
            x = self.width() * x_fraction
            painter.setPen(QColor("#444444"))
            painter.setBrush(QColor(color))
            painter.drawEllipse(
                int(x - radius), int(y - radius),
                int(2 * radius), int(2 * radius),
            )
            painter.setPen(self.palette().windowText().color())
            painter.drawText(
                int(x - 25), self.height() - 26, 50, 22,
                Qt.AlignmentFlag.AlignCenter, symbol,
            )
        painter.end()


class GeneralSettingsPage(QFrame):
    """Common application options grouped on one settings tab."""

    def __init__(
        self, settings: ApplicationSettings, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.visualization_group = QGroupBox("Visualization", self)
        visualization_form = QFormLayout(self.visualization_group)
        self.viewer = QComboBox(self.visualization_group)
        viewers = available_viewers()
        self.viewer.addItems(viewers)
        if settings.viewer not in viewers:
            self.viewer.addItem(settings.viewer)
        self.viewer.setCurrentText(settings.viewer)
        self.viewer.setToolTip("ASE viewer used for atomic structures")
        for index in range(self.viewer.count()):
            name = self.viewer.itemText(index)
            reason = viewer_unavailability(name)
            if reason:
                self.viewer.setItemData(
                    index, QColor("#cc3333"), Qt.ItemDataRole.ForegroundRole
                )
                self.viewer.setItemData(
                    index, reason, Qt.ItemDataRole.ToolTipRole
                )
        visualization_form.addRow("Structure viewer:", self.viewer)
        self.viewer_warning = QLabel(self.visualization_group)
        self.viewer_warning.setWordWrap(True)
        self.viewer_warning.setStyleSheet("color: #cc3333;")
        visualization_form.addRow("", self.viewer_warning)

        size_row = QWidget(self.visualization_group)
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        self.atom_size_slider = QSlider(Qt.Orientation.Horizontal, size_row)
        self.atom_size_slider.setRange(25, 200)
        self.atom_size_slider.setSingleStep(5)
        self.atom_size_slider.setValue(settings.atom_size_percent)
        self.atom_size_label = QLabel(size_row)
        self.atom_size_label.setMinimumWidth(48)
        size_layout.addWidget(self.atom_size_slider, 1)
        size_layout.addWidget(self.atom_size_label)
        visualization_form.addRow("Atom size:", size_row)
        self.atom_relative_sizes = QCheckBox(
            "Use element-dependent Wigner–Seitz radii", self.visualization_group
        )
        self.atom_relative_sizes.setChecked(settings.atom_relative_sizes)
        visualization_form.addRow(self.atom_relative_sizes)
        self.atom_potential_radii = QCheckBox(
            "Use potential radii when available", self.visualization_group
        )
        self.atom_potential_radii.setToolTip(
            "Only use RWS from an ITERATING or CONVERGED potential. "
            "START or invalid radii fall back to static values."
        )
        self.atom_potential_radii.setChecked(settings.atom_potential_radii)
        self.atom_potential_radii.setEnabled(self.atom_relative_sizes.isChecked())
        visualization_form.addRow(self.atom_potential_radii)
        self.atom_relative_sizes.toggled.connect(
            self.atom_potential_radii.setEnabled
        )
        self.atom_size_example = _AtomSizeExample(self.visualization_group)
        visualization_form.addRow("Example (C / Au):", self.atom_size_example)
        note = QLabel(
            "Preview uses static WSR. Potential radii are site-dependent "
            "and used only for eligible loaded potentials.",
            self.visualization_group,
        )
        note.setWordWrap(True)
        visualization_form.addRow("", note)
        self.atom_size_slider.valueChanged.connect(self._update_atom_preview)
        self.atom_relative_sizes.toggled.connect(self._update_atom_preview)
        self._update_atom_preview()
        self.viewer.currentTextChanged.connect(self._update_viewer_warning)
        self._update_viewer_warning()
        layout.addWidget(self.visualization_group)

        self.sprkkr_group = QGroupBox("SPR-KKR", self)
        sprkkr_form = QFormLayout(self.sprkkr_group)
        self.dataset_default_mode = QComboBox(self.sprkkr_group)
        for value, label in _DATASET_MODE_LABELS:
            self.dataset_default_mode.addItem(label, value)
        index = self.dataset_default_mode.findData(settings.dataset_default_mode)
        self.dataset_default_mode.setCurrentIndex(max(index, 0))
        self.dataset_default_mode.setToolTip(
            "Default DATASET for newly created calculations. An explicit value "
            "entered in the calculation editor always takes precedence."
        )
        sprkkr_form.addRow("Default DATASET:", self.dataset_default_mode)
        layout.addWidget(self.sprkkr_group)

        self.python_shell_group = QGroupBox("Python shell", self)
        shell_form = QFormLayout(self.python_shell_group)
        self.share_python_shell_history = QCheckBox(
            "Share command history with regular Python",
            self.python_shell_group,
        )
        self.share_python_shell_history.setChecked(
            settings.share_python_shell_history
        )
        self.share_python_shell_history.setToolTip(
            "ase2sprkkr always saves its own command history. When enabled, "
            "also load the ordinary Python REPL history and append new "
            "Ase2sprkkr commands to it."
        )
        shell_form.addRow(self.share_python_shell_history)
        layout.addWidget(self.python_shell_group)
        layout.addStretch(1)

    def _update_atom_preview(self, *_args) -> None:
        percent = self.atom_size_slider.value()
        self.atom_size_label.setText(f"{percent}%")
        self.atom_size_example.set_values(
            percent, self.atom_relative_sizes.isChecked()
        )

    def selected_viewer_unavailability(self) -> str | None:
        """Return the availability warning for the selected viewer."""
        return viewer_unavailability(self.viewer.currentText())

    def _update_viewer_warning(self) -> None:
        reason = self.selected_viewer_unavailability()
        self.viewer_warning.setText(
            f"Viewer is not available: {reason}" if reason else ""
        )
        self.viewer_warning.setVisible(bool(reason))
