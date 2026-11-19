"""Detailed, read-only overview with linked cluster/host site selection."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QRadioButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ase2sprkkr_gui.gui.widgets.structures.overview import structure_facts
from ase2sprkkr_gui.physics.composition import site_composition


def _symmetry_description(atoms):
    """Use the scientific symmetry API, never guessed space groups."""
    if atoms is None or not all(atoms.pbc) or not len(atoms):
        return "Not applicable to this nonperiodic structure"
    from ase2sprkkr.sprkkr.symmetry import analyze_symmetry
    try:
        dataset = analyze_symmetry(atoms, equivalence="occupation").dataset
    except (ValueError, TypeError, AttributeError) as error:
        return f"Unavailable: {error}"
    international = getattr(dataset, "international", "?")
    number = getattr(dataset, "number", "?")
    hall = getattr(dataset, "hall", "?")
    hall_number = getattr(dataset, "hall_number", "?")
    return (
        f"Space group: {international} (No. {number})\n"
        f"Hall symbol: {hall} (No. {hall_number})"
    )


def _section(title, parent):
    """Use identical bold section titles without bolding their contents."""
    box = QGroupBox(title, parent)
    box.setStyleSheet("QGroupBox::title { font-weight: 700; }")
    return box


class EditorOverviewWidget(QWidget):
    """Structure facts and a table linked to an external cluster preview.

    The table always uses direct atom indices; its Host mode is a separate
    coordinate system whose rows are mapped to cluster atoms via embedding data.
    """

    siteSelectionChanged = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._atoms = None
        self._host_atoms = None
        root = QVBoxLayout(self)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        root.addWidget(self.scroll, 1)
        content = QWidget(self.scroll)
        self.scroll.setWidget(content)
        layout = QVBoxLayout(content)

        self.info_box = _section("Structure information", content)
        info_layout = QVBoxLayout(self.info_box)
        self.main = QLabel(self.info_box)
        self.main.setWordWrap(True)
        self.main.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(self.main)
        self.symmetry = QLabel(self.info_box)
        self.symmetry.setWordWrap(True)
        self.symmetry.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(self.symmetry)
        layout.addWidget(self.info_box)

        self.host_box = _section("Host structure", content)
        host_layout = QFormLayout(self.host_box)
        self.host_facts = QLabel(self.host_box)
        self.host_facts.setWordWrap(True)
        self.host_facts.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        host_layout.addRow(self.host_facts)
        layout.addWidget(self.host_box)

        self.bulk_boxes = {}
        for side in ("left", "right"):
            box = _section(f"{side.title()} bulk", content)
            section = QFormLayout(box)
            summary = QLabel(box)
            summary.setWordWrap(True)
            summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            section.addRow(summary)
            layout.addWidget(box)
            self.bulk_boxes[side] = (box, summary)

        self.table_box = _section("Atomic sites", content)
        table_layout = QVBoxLayout(self.table_box)
        table_header = QHBoxLayout()
        table_header.addStretch(1)
        self.site_scope = QWidget(self.table_box)
        scope_layout = QHBoxLayout(self.site_scope)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        self.cluster_radio = QRadioButton("Cluster", self.site_scope)
        self.host_radio = QRadioButton("Host", self.site_scope)
        self.cluster_radio.setChecked(True)
        self.cluster_radio.toggled.connect(self._scope_changed)
        self.host_radio.toggled.connect(self._scope_changed)
        scope_layout.addWidget(self.cluster_radio)
        scope_layout.addWidget(self.host_radio)
        table_header.addWidget(self.site_scope)
        table_layout.addLayout(table_header)

        self.site_table = QTableWidget(0, 8, self.table_box)
        self.site_table.setHorizontalHeaderLabels([
            "Site", "Occupation", "x (Å)", "y (Å)", "z (Å)",
            "x (fractional)", "y (fractional)", "z (fractional)",
        ])
        self.site_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.site_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.site_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.site_table.setMinimumHeight(240)
        self.site_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.site_table.currentCellChanged.connect(self._selection_changed)
        table_layout.addWidget(self.site_table)
        layout.addWidget(self.table_box, 1)
        layout.addStretch(1)

    @property
    def site_scope_name(self):
        if self._host_atoms is not None and self.host_radio.isChecked():
            return "host"
        return "cluster"

    def _scope_changed(self, checked):
        if checked:
            self._populate_sites()

    def _selection_changed(self, row, _column, _old_row, _old_column):
        self.siteSelectionChanged.emit(self.site_scope_name, int(row))

    def _populate_sites(self):
        scope = self.site_scope_name
        atoms = self._host_atoms if scope == "host" else self._atoms
        # Preserve a selected atom when only the underlying structure draft
        # changes, but begin at the first site when switching table scope.
        previous_row = (
            self.site_table.currentRow()
            if getattr(self, "_populated_scope", None) == scope else 0
        )
        self._populated_scope = scope
        self.site_table.blockSignals(True)
        self.site_table.setUpdatesEnabled(False)
        try:
            if atoms is None:
                self.site_table.setRowCount(0)
                return
            positions = atoms.get_positions()
            scaled = atoms.get_scaled_positions(wrap=False)
            labels = atoms.arrays.get("labels")
            self.site_table.setRowCount(len(atoms))
            for i in range(len(atoms)):
                name = str(labels[i]) if labels is not None else str(i + 1)
                try:
                    occupation = site_composition(atoms, i)
                except (ValueError, TypeError, KeyError, AttributeError):
                    occupation = atoms[i].symbol
                cells = [name, str(occupation)]
                cells += [f"{value:.5f}" for value in positions[i]]
                cells += [f"{value:.5f}" for value in scaled[i]]
                for column, value in enumerate(cells):
                    item = QTableWidgetItem(value)
                    if column >= 2:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                    self.site_table.setItem(i, column, item)
            if len(atoms):
                self.site_table.setCurrentCell(
                    min(max(previous_row, 0), len(atoms) - 1), 0,
                )
        finally:
            self.site_table.setUpdatesEnabled(True)
            self.site_table.blockSignals(False)
        self.siteSelectionChanged.emit(
            self.site_scope_name, self.site_table.currentRow()
        )

    def set_structure(self, atoms):
        self._atoms = atoms
        if atoms is None:
            self._host_atoms = None
            self.main.clear()
            self.symmetry.clear()
            self.host_box.hide()
            self.site_scope.hide()
            self._populate_sites()
            return
        is_cluster = "sprkkr_embedding" in getattr(atoms, "arrays", {})
        info = atoms.info
        host = info.get("sprkkr_embedding_host") if is_cluster else None
        self._host_atoms = getattr(host, "atoms", host)
        self.site_scope.setVisible(self._host_atoms is not None)
        if self._host_atoms is None and self.host_radio.isChecked():
            self.cluster_radio.setChecked(True)
        self.main.setText(structure_facts(atoms))
        self.symmetry.setText(
            "Cluster symmetry: refer to the host symmetry below"
            if is_cluster else
            _symmetry_description(atoms) if all(atoms.pbc) else
            "Space groups of the two bulk regions are shown separately"
        )
        self.host_box.setVisible(self._host_atoms is not None)
        if self._host_atoms is not None:
            self.host_facts.setText(
                structure_facts(self._host_atoms) + "\n"
                + _symmetry_description(self._host_atoms)
            )
        regions = getattr(
            self._host_atoms if is_cluster else atoms, "regions", {},
        )
        for name, (box, label) in self.bulk_boxes.items():
            region = regions.get(name)
            box.setVisible(region is not None)
            if region is None:
                continue
            try:
                part = region.create_atoms()
                label.setText(structure_facts(part) + "\n" + _symmetry_description(part))
            except (ValueError, TypeError, AttributeError) as error:
                label.setText(f"Unavailable: {error}")
        self._populate_sites()
