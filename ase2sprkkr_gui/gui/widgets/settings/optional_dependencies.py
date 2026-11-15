"""Optional feature status and installation entry point in Settings."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ase2sprkkr_gui.gui.application.optional_dependencies import (
    EXTRA_LABELS, install_extra, missing_modules,
)


class OptionalDependenciesPage(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Optional scientific dependencies are installed in the Python "
            "environment running this GUI. Matplotlib is required for structure "
            "previews; additional plotting, NeXus, and Mendeleev packages are "
            "optional. Nothing is installed without confirmation.",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        grid.addWidget(QLabel("Feature"), 0, 0)
        grid.addWidget(QLabel("Status"), 0, 1)
        self.status_labels = {}
        self.install_buttons = {}
        for i, extra in enumerate(("plot", "nexus", "mendeleev"), 1):
            grid.addWidget(QLabel(EXTRA_LABELS[extra]), i, 0)
            status = QLabel(self)
            self.status_labels[extra] = status
            grid.addWidget(status, i, 1)
            button = QPushButton("Install…", self)
            button.clicked.connect(
                lambda _checked=False, name=extra: self._install(name)
            )
            self.install_buttons[extra] = button
            grid.addWidget(button, i, 2)
        layout.addLayout(grid)
        layout.addStretch()
        self.refresh()

    def refresh(self):
        for extra, label in self.status_labels.items():
            missing = missing_modules(extra)
            label.setText("Installed" if not missing else "Missing: " + ", ".join(missing))
            self.install_buttons[extra].setEnabled(bool(missing))

    def _install(self, extra: str):
        install_extra(extra, self)
        self.refresh()
