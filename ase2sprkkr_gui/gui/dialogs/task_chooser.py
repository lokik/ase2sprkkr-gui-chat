"""Task chooser for custom SPR-KKR input and calculation workflows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ase2sprkkr import InputParameters
from ase2sprkkr.sprkkr.executables import executables
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QAbstractItemView,
)

from ase2sprkkr_gui.physics.lattice import detect_structure_kind


@dataclass(frozen=True)
class TaskChoice:
    name: str
    experimental: bool
    applicable: str
    info: str
    reason: str = ""
    details: str = ""


def applicability(task: Any, structure_kind: str | None) -> tuple[str, str]:
    """Task/geometry support only, not potential readiness or convergence."""
    if structure_kind is None:
        return "Unknown", "No structure is loaded."
    if structure_kind == "0d":
        executable = executables.get(task.executable) if hasattr(executables, "get") else (
            executables[task.executable] if task.executable in executables else None
        )
        if not task.supports_embedded_cluster:
            return "No", "This task is not enabled for embedded-cluster calculations."
        if executable is None or executable.cluster_variant is None:
            return "No", "No embedded-cluster executable is available for this task."
        return "Yes", "The task explicitly supports embedded clusters."
    if structure_kind == "2d":
        supported = getattr(task, "supports_2d", None)
        if supported is None:
            return "Unknown", "Two-dimensional support has not been declared for this task."
        return ("Yes", "2D geometry is supported.") if supported else (
            "No", "This task does not support 2D calculations."
        )
    if structure_kind == "3d":
        supported = getattr(task, "supports_3d", None)
        if supported is False:
            return "No", "This task does not support 3D calculations."
        return "Yes", "The task supports periodic 3D calculations."
    return "Unknown", f"Unknown structure type: {structure_kind}."


def task_choices(atoms: Any = None) -> tuple[TaskChoice, ...]:
    """Discover registered native tasks, including experimental tasks."""
    kind = detect_structure_kind(atoms) if atoms is not None else None
    rows = []
    for name in InputParameters.task_registry.names():
        task = InputParameters.task_registry[name]
        status, reason = applicability(task, kind)
        try:
            definition = InputParameters.input_parameters_registry.definition_for_task(task)
            # Backend info is the short summary; description is detailed help.
            info = getattr(definition, "info", None)
            details = getattr(definition, "description", None)
            if callable(info):
                info = info()
            if callable(details):
                details = details()
            if not isinstance(info, str):
                info = ""
            if not isinstance(details, str):
                details = ""
        except (AttributeError, KeyError, ValueError):
            info, details = "", ""
        if not info.strip() and details.strip():
            # Only a short lead sentence when no dedicated summary exists.
            info = " ".join(details.split()).split(". ", 1)[0].rstrip(".") + "."
        rows.append(TaskChoice(
            task.name.upper(), task.experimental, status,
            " ".join(info.split()) or f"SPR-KKR {task.executable} calculation",
            reason, details.strip(),
        ))
    return tuple(rows)


def order_choices(
    rows: tuple[TaskChoice, ...] | list[TaskChoice],
    *,
    primary: str | None = None,
    descending: bool = False,
    has_structure: bool = True,
) -> list[TaskChoice]:
    """Stable multi-key sort; remaining default keys always break ties."""
    default = (("applicable", "experimental", "name") if has_structure
               else ("experimental", "name"))
    keys = ((primary,) + tuple(key for key in default if key != primary)
            if primary in ("applicable", "experimental", "name", "info")
            else default)

    def value(row: TaskChoice, key: str):
        if key == "applicable":
            return {"Yes": 0, "No": 1, "Unknown": 2}.get(row.applicable, 3)
        if key == "experimental":
            return 1 if row.experimental else 0
        return getattr(row, key).casefold()

    ordered = list(rows)
    for key in reversed(keys[1:]):
        ordered.sort(key=lambda row, k=key: value(row, k))
    ordered.sort(key=lambda row: value(row, keys[0]), reverse=descending)
    return ordered


class TaskChooserDialog(QDialog):
    """Filterable, sortable task picker; unsupported tasks remain selectable."""

    def __init__(self, atoms: Any = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Choose SPR-KKR task")
        self.resize(880, 560)
        self._has_structure = atoms is not None
        self._records = task_choices(atoms)
        self._primary: str | None = None
        self._descending = False
        self.selected_task: str | None = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Choose a native task. Unsupported or unverified tasks can still be edited "
            "and saved; calculation availability is checked separately.", self
        ))
        toolbar = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filter by name or description…")
        toolbar.addWidget(self.search, 1)
        self.experimental_filter = QComboBox(self)
        self.experimental_filter.addItems(("All tasks", "Experimental", "Non-experimental"))
        toolbar.addWidget(self.experimental_filter)
        self.applicability_filter = None
        if self._has_structure:
            self.applicability_filter = QComboBox(self)
            self.applicability_filter.addItems(("Any applicability", "Yes", "No", "Unknown"))
            toolbar.addWidget(self.applicability_filter)
        root.addLayout(toolbar)

        self._fields = ["name", "experimental"]
        if self._has_structure:
            self._fields.append("applicable")
        self._fields.append("info")
        self.table = QTableWidget(0, len(self._fields), self)
        self.table.setObjectName("customTaskTable")
        self.table.setHorizontalHeaderLabels([
            {"name": "Name", "experimental": "Experimental",
             "applicable": "Applicable to structure", "info": "Info"}[key]
            for key in self._fields
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self._fields.index("info"), QHeaderView.ResizeMode.Stretch)
        header.sectionClicked.connect(self._sort_by_column)
        self.table.cellDoubleClicked.connect(lambda *_args: self._accept_selection())
        root.addWidget(self.table, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.search.textChanged.connect(self._refresh)
        self.experimental_filter.currentIndexChanged.connect(self._refresh)
        if self.applicability_filter is not None:
            self.applicability_filter.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _sort_by_column(self, index: int) -> None:
        field = self._fields[index]
        self._descending = not self._descending if self._primary == field else False
        self._primary = field
        self.table.horizontalHeader().setSortIndicator(
            index, (Qt.SortOrder.DescendingOrder if self._descending
                    else Qt.SortOrder.AscendingOrder)
        )
        self._refresh()

    def _refresh(self, *_args) -> None:
        previously = self._selected_name()
        text = self.search.text().casefold().strip()
        experimental = self.experimental_filter.currentIndex()
        applicable = (self.applicability_filter.currentText()
                      if self.applicability_filter is not None else "Any applicability")
        rows = [
            row for row in self._records
            if (not text or text in (row.name + " " + row.info + " " + row.details).casefold())
            and (experimental == 0 or row.experimental == (experimental == 1))
            and (applicable == "Any applicability" or row.applicable == applicable)
        ]
        rows = order_choices(
            rows, primary=self._primary, descending=self._descending,
            has_structure=self._has_structure,
        )
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = {
                "name": row.name, "experimental": "Yes" if row.experimental else "No",
                "applicable": row.applicable, "info": row.info,
            }
            for column, key in enumerate(self._fields):
                item = QTableWidgetItem(values[key])
                item.setData(Qt.ItemDataRole.UserRole, row.name)
                item.setToolTip(
                    row.reason if key == "applicable"
                    else (row.details or row.info) if key == "info"
                    else row.info
                )
                self.table.setItem(index, column, item)
        target = next((i for i, row in enumerate(rows) if row.name == previously), 0)
        if rows:
            self.table.selectRow(target)

    def _selected_name(self) -> str | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _accept_selection(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        selected = next((row for row in self._records if row.name == name), None)
        if selected is not None and selected.applicable != "Yes" and self._has_structure:
            answer = QMessageBox.warning(
                self, "Task applicability",
                f"{name}: {selected.reason}\n\n"
                "You may edit and save this input even if the calculation is unsupported. "
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.selected_task = name
        self.accept()
