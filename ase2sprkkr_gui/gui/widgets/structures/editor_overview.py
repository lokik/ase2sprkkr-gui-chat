"""Detailed, read-only overview of the structure being edited."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout, QGroupBox, QHeaderView, QLabel, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ase2sprkkr_gui.gui.widgets.structures.overview import structure_facts
from ase2sprkkr_gui.physics.composition import site_composition


def _symmetry_description(atoms):
    """Use the shared scientific symmetry API, never guessed space groups."""
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


class EditorOverviewWidget(QWidget):
    """Summary + atomic site table; refreshed against the current draft."""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        root.addWidget(self.scroll, 1)
        content = QWidget(self.scroll)
        self.scroll.setWidget(content)
        layout = QVBoxLayout(content)
        self.main = QLabel(content)
        self.main.setWordWrap(True)
        self.main.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.main)
        self.symmetry = QLabel(content)
        self.symmetry.setWordWrap(True)
        self.symmetry.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.symmetry)
        self.host_box = QGroupBox("Host structure", content)
        host_layout = QFormLayout(self.host_box)
        self.host_facts = QLabel(self.host_box)
        self.host_facts.setWordWrap(True)
        self.host_facts.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        host_layout.addRow(self.host_facts)
        layout.addWidget(self.host_box)
        self.bulk_boxes = {}
        for side in ("left", "right"):
            box = QGroupBox(f"{side.title()} bulk", content)
            section = QFormLayout(box)
            summary = QLabel(box)
            summary.setWordWrap(True)
            summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            section.addRow(summary)
            layout.addWidget(box)
            self.bulk_boxes[side] = (box, summary)
        table_box = QGroupBox("Atomic sites", content)
        table_layout = QVBoxLayout(table_box)
        self.site_table = QTableWidget(0, 8, table_box)
        self.site_table.setHorizontalHeaderLabels([
            "Site", "Occupation", "x (Å)", "y (Å)", "z (Å)",
            "x (fractional)", "y (fractional)", "z (fractional)",
        ])
        self.site_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.site_table.setMinimumHeight(240)
        self.site_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table_layout.addWidget(self.site_table)
        layout.addWidget(table_box, 1)
        layout.addStretch(1)

    def set_structure(self, atoms):
        if atoms is None:
            self.main.clear()
            self.symmetry.clear()
            self.site_table.setRowCount(0)
            return
        is_cluster = "sprkkr_embedding" in getattr(atoms, "arrays", {})
        info = atoms.info
        host = info.get("sprkkr_embedding_host") if is_cluster else None
        host_atoms = getattr(host, "atoms", host)
        self.main.setText(structure_facts(atoms))
        self.symmetry.setText(
            "Cluster symmetry: refer to the host symmetry below"
            if is_cluster else
            _symmetry_description(atoms) if all(atoms.pbc) else
            "Space groups of the two bulk regions are shown separately"
        )
        self.host_box.setVisible(host_atoms is not None)
        if host_atoms is not None:
            self.host_facts.setText(
                structure_facts(host_atoms) + "\n" + _symmetry_description(host_atoms)
            )
        regions = getattr(host_atoms if is_cluster else atoms, "regions", {})
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

        self.site_table.setUpdatesEnabled(False)
        try:
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
        finally:
            self.site_table.setUpdatesEnabled(True)
