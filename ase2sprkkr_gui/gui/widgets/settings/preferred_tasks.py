"""Orderable preferred-task list. Task metadata and sorting come from the chooser."""
from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ase2sprkkr_gui.gui.application.settings import ApplicationSettings
from ase2sprkkr_gui.gui.dialogs.task_chooser import (
    TaskChoice, order_choices, task_choices,
)
from ase2sprkkr_gui.gui.input_parameters.task_preferences import supported_task_names
from ase2sprkkr_gui.gui.misc.icons import task_icon


class PreferredTasksPage(QFrame):
    """Select and order task shortcuts without mutating saved settings until OK."""

    def __init__(self, settings: ApplicationSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._preferred = list(settings.effective_preferred_tasks())
        self._records = task_choices(preferred=tuple(self._preferred))
        root = QVBoxLayout(self)
        explanation = QLabel(
            "Choose task shortcuts for the Calculate column. Tasks with a "
            "dedicated editor are marked Supported; other tasks open the custom editor.",
            self,
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        columns = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("Available tasks", self))
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filter tasks…")
        left.addWidget(self.search)
        self.available = QTableWidget(0, 5, self)
        self.available.setObjectName("availablePreferredTasks")
        self.available.setHorizontalHeaderLabels(
            ("Name", "Preferred", "Supported", "Experimental", "Info")
        )
        self.available.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.available.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.available.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.available.verticalHeader().hide()
        self.available.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.available.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        left.addWidget(self.available, 1)
        columns.addLayout(left, 3)

        transfer = QVBoxLayout()
        transfer.addStretch()
        self.add_button = QPushButton("Add →", self)
        self.remove_button = QPushButton("← Remove", self)
        self.add_button.setToolTip("Append the selected task to Preferred tasks")
        self.remove_button.setToolTip("Remove the selected preferred task")
        transfer.addWidget(self.add_button)
        transfer.addWidget(self.remove_button)
        transfer.addStretch()
        columns.addLayout(transfer)

        right = QVBoxLayout()
        right.addWidget(QLabel("Preferred tasks (in Guided order)", self))
        self.preferred = QListWidget(self)
        self.preferred.setObjectName("orderedPreferredTasks")
        right.addWidget(self.preferred, 1)
        arrows = QHBoxLayout()
        self.up_button = QPushButton("↑ Up", self)
        self.down_button = QPushButton("↓ Down", self)
        arrows.addWidget(self.up_button)
        arrows.addWidget(self.down_button)
        right.addLayout(arrows)
        columns.addLayout(right, 2)
        root.addLayout(columns, 1)

        self.restore_button = QPushButton("Restore defaults", self)
        self.restore_button.setToolTip(
            "Use all GUI-supported tasks, ordered alphabetically. "
            "The stored preference override will be cleared."
        )
        root.addWidget(self.restore_button, alignment=Qt.AlignmentFlag.AlignRight)

        self.search.textChanged.connect(self._refresh_available)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.restore_button.clicked.connect(self._restore)
        self.available.cellDoubleClicked.connect(lambda *_: self._add())
        self.preferred.itemDoubleClicked.connect(lambda *_: self._remove())
        self.available.itemSelectionChanged.connect(self._update_buttons)
        self.preferred.itemSelectionChanged.connect(self._update_buttons)
        self._refresh_all()

    def selection(self) -> tuple[str, ...]:
        return tuple(self._preferred)

    def preference_override(self) -> tuple[str, ...] | None:
        defaults = supported_task_names()
        return None if tuple(self._preferred) == defaults else tuple(self._preferred)

    def _selected_available(self) -> str | None:
        row = self.available.currentRow()
        item = self.available.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selected_preferred(self) -> str | None:
        item = self.preferred.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _refresh_available(self, *_args):
        selected = self._selected_available()
        term = self.search.text().strip().casefold()
        rows = order_choices(
            [replace(r, preferred=r.name in self._preferred) for r in self._records
             if not term or term in (r.name + " " + r.info + " " + r.details).casefold()],
            has_structure=False,
        )
        self.available.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for col, value in enumerate((
                row.name,
                "Yes" if row.preferred else "No",
                "Yes" if row.supported else "No",
                "Yes" if row.experimental else "No",
                row.info,
            )):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row.name)
                item.setToolTip(row.details or row.info)
                if col == 0:
                    icon = task_icon(row.name)
                    if not icon.isNull():
                        item.setIcon(icon)
                self.available.setItem(i, col, item)
        for i, row in enumerate(rows):
            if row.name == selected:
                self.available.selectRow(i)
                break
        self._update_buttons()

    def _refresh_preferred(self, selected: str | None = None):
        self.preferred.clear()
        for name in self._preferred:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            icon = task_icon(name)
            if not icon.isNull():
                item.setIcon(icon)
            self.preferred.addItem(item)
            if name == selected:
                self.preferred.setCurrentItem(item)
        self._update_buttons()

    def _refresh_all(self, selected: str | None = None):
        self._refresh_preferred(selected)
        self._refresh_available()

    def _update_buttons(self):
        available = self._selected_available()
        current = self.preferred.currentRow()
        self.add_button.setEnabled(available is not None and available not in self._preferred)
        self.remove_button.setEnabled(current >= 0)
        self.up_button.setEnabled(current > 0)
        self.down_button.setEnabled(0 <= current < len(self._preferred) - 1)

    def _add(self):
        name = self._selected_available()
        if name is None or name in self._preferred:
            return
        self._preferred.append(name)
        self._refresh_all(name)

    def _remove(self):
        name = self._selected_preferred()
        if name is None:
            return
        if name == "SCF":
            answer = QMessageBox.question(
                self, "Remove SCF from Preferred tasks?",
                "SCF is needed for almost every calculation workflow. "
                "Remove it from Preferred tasks anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        index = self._preferred.index(name)
        self._preferred.remove(name)
        self._refresh_all(
            self._preferred[min(index, len(self._preferred) - 1)]
            if self._preferred else None
        )

    def _move(self, offset: int):
        index = self.preferred.currentRow()
        target = index + offset
        if index < 0 or not 0 <= target < len(self._preferred):
            return
        self._preferred[index], self._preferred[target] = (
            self._preferred[target], self._preferred[index]
        )
        self._refresh_all(self._preferred[target])

    def _restore(self):
        self._preferred = list(supported_task_names())
        self._refresh_all()
