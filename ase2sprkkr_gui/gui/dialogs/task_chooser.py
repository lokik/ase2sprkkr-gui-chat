"""Task chooser for custom SPR-KKR input and calculation workflows."""
from __future__ import annotations

from typing import Any

from ase2sprkkr_gui.gui.input_parameters.task_choices import (
    TaskChoice, order_choices, task_choices, task_match_priority,
)
from ase2sprkkr_gui.gui.misc.icons import task_icon
from PyQt6.QtCore import QPointF, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPalette, QTextCharFormat, QTextLayout
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QAbstractItemView, QApplication, QStyle, QStyleOptionViewItem,
    QStyledItemDelegate,
)


def _matching_spans(value: str, query: str) -> list[tuple[int, int]]:
    """Return case-insensitive match ranges in Qt (UTF-16) character offsets."""
    needle = query.casefold()
    if not needle:
        return []
    folded = []
    starts, ends = [], []
    offset = 0
    for char in value:
        part = char.casefold()
        folded.append(part)
        starts.extend([offset] * len(part))
        offset += 2 if ord(char) > 0xFFFF else 1
        ends.extend([offset] * len(part))
    text = "".join(folded)
    spans = []
    position = 0
    while (match := text.find(needle, position)) >= 0:
        start, end = starts[match], ends[match + len(needle) - 1]
        if not spans or start >= spans[-1][0] + spans[-1][1]:
            spans.append((start, end - start))
        position = match + len(needle)
    return spans


class MatchHighlightDelegate(QStyledItemDelegate):
    """Highlight search hits without replacing table items or selection styling."""

    def __init__(self, search: QLineEdit, parent: QWidget):
        super().__init__(parent)
        self._search = search

    def paint(self, painter, option, index):
        query = self._search.text().strip()
        value = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not query or not _matching_spans(value, query):
            super().paint(painter, option, index)
            return

        visual = QStyleOptionViewItem(option)
        self.initStyleOption(visual, index)
        style = visual.widget.style() if visual.widget else QApplication.style()
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, visual, visual.widget,
        )
        full_text = visual.text
        visual.text = ""
        painter.save()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, visual, painter, visual.widget)
        if text_rect.width() > 0:
            painter.setClipRect(text_rect)
            shown = visual.fontMetrics.elidedText(
                full_text, visual.textElideMode, text_rect.width(),
            )
            layout = QTextLayout(shown, visual.font)
            selected = bool(visual.state & QStyle.StateFlag.State_Selected)
            color = visual.palette.color(
                QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
            )
            normal = QTextCharFormat()
            normal.setForeground(QBrush(color))
            base = QTextLayout.FormatRange()
            base.start, base.length, base.format = 0, len(shown.encode("utf-16-le")) // 2, normal
            formats = [base]
            for start, length in _matching_spans(shown, query):
                match_format = QTextCharFormat()
                match_format.setBackground(QBrush(QColor("#ffe083")))
                match_format.setForeground(QBrush(QColor("#242424")))
                match_format.setFontWeight(QFont.Weight.Bold)
                span = QTextLayout.FormatRange()
                span.start, span.length, span.format = start, length, match_format
                formats.append(span)
            layout.setFormats(formats)
            layout.beginLayout()
            line = layout.createLine()
            if line.isValid():
                line.setLineWidth(text_rect.width())
            layout.endLayout()
            if line.isValid():
                layout.draw(painter, QPointF(
                    text_rect.x(), text_rect.y() + (text_rect.height() - line.height()) / 2,
                ))
        painter.restore()


class TaskChooserDialog(QDialog):
    """Filterable, sortable task picker; unsupported tasks remain selectable."""

    def __init__(
        self, atoms: Any = None, parent: QWidget | None = None, *, settings=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Choose SPR-KKR task")
        self.resize(880, 560)
        self._has_structure = atoms is not None
        if settings is None:
            settings = getattr(parent, "settings", None)
        preferred = settings.effective_preferred_tasks() if settings is not None else None
        self._records = (
            task_choices(atoms) if preferred is None
            else task_choices(atoms, preferred=preferred)
        )
        self._primary: str | None = None
        self._descending = False
        self.selected_task: str | None = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Choose a task. Unsupported tasks can still be edited "
            "and saved; calculation availability is checked separately.", self
        ))
        toolbar = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filter by task name or info…")
        toolbar.addWidget(self.search, 1)
        self.experimental_filter = QComboBox(self)
        self.experimental_filter.addItems(("All tasks", "Experimental", "Non-experimental"))
        toolbar.addWidget(self.experimental_filter)
        self.applicability_filter = None
        if self._has_structure:
            self.applicability_filter = QComboBox(self)
            self.applicability_filter.addItems(("Any applicability", "Yes", "No"))
            toolbar.addWidget(self.applicability_filter)
        root.addLayout(toolbar)

        self._fields = ["icon", "name"]
        if self._has_structure:
            self._fields.append("applicable")
        self._fields.extend(("preferred", "supported", "experimental", "info"))
        self.table = QTableWidget(0, len(self._fields), self)
        self.table.setObjectName("customTaskTable")
        self.table.setHorizontalHeaderLabels([
            {"icon": "", "name": "Name", "experimental": "Experimental",
             "applicable": "Possible", "preferred": "Preferred",
             "supported": "Supported", "info": "Info"}[key]
            for key in self._fields
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self._match_delegate = MatchHighlightDelegate(self.search, self.table)
        for field in ("name", "info"):
            self.table.setItemDelegateForColumn(self._fields.index(field), self._match_delegate)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setIconSize(QSize(24, 24))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 30)
        header.setSectionResizeMode(self._fields.index("info"), QHeaderView.ResizeMode.Stretch)
        header.setSortIndicatorShown(False)
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
        if field == "icon":
            return
        if self._primary == field:
            self._descending = not self._descending
        else:
            # First click on Experimental: No, then Yes (descending labels).
            self._descending = field == "experimental"
        self._primary = field
        header = self.table.horizontalHeader()
        header.setSortIndicator(
            index, (Qt.SortOrder.DescendingOrder if self._descending
                    else Qt.SortOrder.AscendingOrder)
        )
        header.setSortIndicatorShown(True)
        self._refresh()

    def _refresh(self, *_args) -> None:
        previously = self._selected_name()
        text = self.search.text().casefold().strip()
        experimental = self.experimental_filter.currentIndex()
        applicable = (self.applicability_filter.currentText()
                      if self.applicability_filter is not None else "Any applicability")
        rows = [
            row for row in self._records
            if task_match_priority(row, text) is not None
            and (experimental == 0 or row.experimental == (experimental == 1))
            and (applicable == "Any applicability" or row.applicable == applicable)
        ]
        rows = order_choices(
            rows, primary=self._primary, descending=self._descending,
            has_structure=self._has_structure,
        )
        if text:
            # Preserve the existing column order within each relevance tier.
            rows.sort(key=lambda row: task_match_priority(row, text))
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = {
                "name": row.name, "experimental": "Yes" if row.experimental else "No",
                "applicable": row.applicable, "info": row.info,
                "preferred": "—" if row.name == "SCF" else "Yes" if row.preferred else "No",
                "supported": "Yes" if row.supported else "No",
            }
            for column, key in enumerate(self._fields):
                item = QTableWidgetItem() if key == "icon" else QTableWidgetItem(values[key])
                item.setData(Qt.ItemDataRole.UserRole, row.name)
                item.setToolTip(
                    row.reason if key == "applicable"
                    else "SCF is shown automatically when convergence is required."
                    if key == "preferred" and row.name == "SCF"
                    else (row.details or row.info) if key == "info"
                    else row.info
                )
                if key == "icon":
                    icon = task_icon(row.name)
                    if not icon.isNull():
                        item.setIcon(icon)
                self.table.setItem(index, column, item)
        target = next((i for i, row in enumerate(rows) if row.name == previously), 0)
        if rows:
            self.table.selectRow(target)

    def _selected_name(self) -> str | None:
        row = self.table.currentRow()
        item = self.table.item(row, self._fields.index("name")) if row >= 0 else None
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
