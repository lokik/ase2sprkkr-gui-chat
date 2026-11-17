"""Reusable site-occupation editor for bulk and embedded structures.

The widget owns only transient row controls, not the accepted physical structure.
Bulk assignment supplies mutation callbacks for its ElementAssignmentDraft;
embedding supplies detached OccupancyEntry rows and commits on Apply.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStyle, QVBoxLayout, QWidget,
)
from ase2sprkkr_gui.ase.element_assignment import (
    ElementAssignmentDraft, OccupancyEntry,
)
from ase2sprkkr_gui.gui.dialogs.structures.element_selector import select_element


class OccupationEditor(QWidget):
    """Element rows, picker, fractions, normalization and common validation."""

    validityChanged = pyqtSignal(bool)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        require_full: bool = False,
        allow_duplicates: bool = True,
        on_add: Callable[[], OccupancyEntry] | None = None,
        on_remove: Callable[[OccupancyEntry], bool] | None = None,
        on_symbol: Callable[[OccupancyEntry, str], None] | None = None,
        on_value: Callable[[OccupancyEntry, float], None] | None = None,
        on_normalize: Callable[[], None] | None = None,
        on_validation: Callable[[str | None], None] | None = None,
        on_focus: Callable[[], None] | None = None,
        selector: Callable | None = select_element,
    ):
        super().__init__(parent)
        self.require_full = require_full
        self.allow_duplicates = allow_duplicates
        self._on_add = on_add
        self._on_remove = on_remove
        self._on_symbol = on_symbol
        self._on_value = on_value
        self._on_normalize = on_normalize
        self._on_validation = on_validation
        self._on_focus = on_focus
        self._selector = selector
        self.entries: list[OccupancyEntry] = []
        self.rows: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        controls.addStretch(1)
        self.normalize_btn = QPushButton("Normalize")
        self.normalize_btn.clicked.connect(self.normalize)
        controls.addWidget(self.normalize_btn)
        layout.addLayout(controls)
        self.rows_box = QVBoxLayout()
        layout.addLayout(self.rows_box)
        self.add_btn = QPushButton("＋ Add element")
        self.add_btn.clicked.connect(self.add_row)
        layout.addWidget(self.add_btn)
        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)

    def set_entries(self, entries: Iterable[OccupancyEntry]) -> None:
        for row in self.rows:
            row["w"].setParent(None)
            row["w"].deleteLater()
        self.rows.clear()
        self.entries = list(entries)
        for entry in self.entries:
            self._append_row(entry)
        self._validate()

    def set_occupation(self, occupation: dict[str, float]) -> None:
        """Load an independent editing draft for a single embedding site."""
        self.set_entries([
            OccupancyEntry("Vc" if key in ("X", "Vc") else str(key), float(value))
            for key, value in occupation.items()
        ])

    def add_row(self) -> None:
        entry = self._on_add() if self._on_add is not None else OccupancyEntry("", 0.0)
        self.entries.append(entry)
        self._append_row(entry)
        self._validate()

    def _append_row(self, entry: OccupancyEntry) -> None:
        widget = QWidget(self)
        row_layout = QHBoxLayout(widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        row_layout.addWidget(QLabel("Element"))
        element = QLineEdit()
        element.setPlaceholderText("Element")
        element.setMaxLength(3)
        element.setFixedWidth(55)
        element.setText(entry.symbol)
        row_layout.addWidget(element)

        picker = QPushButton("⚛")
        picker.setToolTip("Pick element")
        picker.setFixedWidth(34)
        picker.setEnabled(self._selector is not None)
        row_layout.addWidget(picker)
        row_layout.addWidget(QLabel("occupation"))

        fraction = QDoubleSpinBox()
        fraction.setRange(0.0, 1.0)
        fraction.setDecimals(3)
        fraction.setSingleStep(0.05)
        fraction.setFixedWidth(70)
        fraction.setValue(entry.value)
        row_layout.addWidget(fraction)
        row_layout.addStretch(1)

        delete = QPushButton()
        delete.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        delete.setToolTip("Remove this element row")
        delete.setFixedWidth(34)
        row_layout.addWidget(delete)

        row = {"w": widget, "elem": element, "occ": fraction,
               "pick": picker, "del": delete, "entry": entry}
        self.rows.append(row)
        self.rows_box.addWidget(widget)
        picker.clicked.connect(
            lambda _checked=False, edit=element: self._pick(edit)
        )
        element.textChanged.connect(
            lambda value, item=entry: self._symbol_changed(item, value)
        )
        fraction.valueChanged.connect(
            lambda value, item=entry: self._value_changed(item, value)
        )
        delete.clicked.connect(
            lambda _checked=False, item=entry: self.remove_row(item)
        )
        for control in (element, picker, fraction, delete):
            control.installEventFilter(self)

    def _pick(self, element: QLineEdit) -> None:
        if self._selector is not None:
            symbol = self._selector(self)
            if symbol:
                element.setText(symbol)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if event.type() == QEvent.Type.FocusIn and self._on_focus is not None:
            self._on_focus()
        return super().eventFilter(obj, event)

    def _symbol_changed(self, entry: OccupancyEntry, value: str) -> None:
        if self._on_symbol is not None:
            self._on_symbol(entry, value)
        else:
            entry.symbol = value.strip()
        self._validate()

    def _value_changed(self, entry: OccupancyEntry, value: float) -> None:
        if self._on_value is not None:
            self._on_value(entry, value)
        else:
            entry.value = value
        self._validate()

    def remove_row(self, entry: OccupancyEntry) -> None:
        if len(self.entries) <= 1:
            return
        if self._on_remove is not None and not self._on_remove(entry):
            return
        self.entries.remove(entry)
        row = next(row for row in self.rows if row["entry"] is entry)
        self.rows.remove(row)
        row["w"].setParent(None)
        row["w"].deleteLater()
        self._validate()

    def normalize(self) -> None:
        if self._on_normalize is not None:
            self._on_normalize()
        else:
            total = sum(e.value for e in self.entries)
            if total > 0:
                for entry in self.entries:
                    entry.value /= total
        for row in self.rows:
            row["occ"].blockSignals(True)
            row["occ"].setValue(row["entry"].value)
            row["occ"].blockSignals(False)
        self._validate()

    def _validation_error(self) -> str | None:
        nonempty = [entry for entry in self.entries if entry.symbol.strip()]
        if not nonempty:
            return "At least one element is required"
        symbols = [
            "Vc" if entry.symbol.strip() == "X" else entry.symbol.strip()
            for entry in nonempty
        ]
        if any(not ElementAssignmentDraft.symbol_is_valid(symbol) for symbol in symbols):
            return "Invalid element symbol"
        if not self.allow_duplicates and len(set(symbols)) != len(symbols):
            return "Duplicate element"
        if any(entry.value < 0 or entry.value > 1 for entry in self.entries):
            return "Occupation component must be between 0 and 1"
        total = sum(entry.value for entry in self.entries)
        if total <= 0:
            return "Occupation must be positive"
        if total > 1 + 1e-6:
            return "Occupation exceeds 1"
        if self.require_full and abs(total - 1) > 1e-6:
            return "Embedding occupation must sum to 1"
        return None

    def _validate(self) -> None:
        message = self._validation_error()
        for row in self.rows:
            symbol = row["entry"].symbol.strip()
            invalid = bool(symbol and not ElementAssignmentDraft.symbol_is_valid(
                "Vc" if symbol == "X" else symbol
            ))
            row["elem"].setStyleSheet("QLineEdit { border: 1px solid #cc3333; }"
                                     if invalid else "")
            row["occ"].setStyleSheet("QDoubleSpinBox { border: 1px solid #cc3333; }"
                                    if message and "Occupation" in message else "")
            row["del"].setEnabled(len(self.rows) > 1)
        self.validation_label.setText(message or "")
        self.validityChanged.emit(message is None)
        if self._on_validation is not None:
            self._on_validation(message)

    def is_valid(self) -> bool:
        return self._validation_error() is None

    def occupation(self) -> dict[str, float]:
        error = self._validation_error()
        if error is not None:
            raise ValueError(error)
        result: dict[str, float] = {}
        for entry in self.entries:
            if entry.symbol.strip():
                symbol = "X" if entry.symbol.strip() == "Vc" else entry.symbol.strip()
                if symbol in result:
                    raise ValueError("Multiple atomic types of the same element cannot be combined")
                result[symbol] = entry.value
        return result
