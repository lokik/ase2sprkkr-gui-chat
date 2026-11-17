"""Compact, read-only structure information in the Guided workflow sidebar."""
from __future__ import annotations

import numpy as np
from ase.data.colors import jmol_colors
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr_gui.gui.presentation import structure_kind_label
from ase2sprkkr_gui.gui.widgets.structures.embedding_preview import EmbeddingPreview
from ase2sprkkr_gui.physics.composition import structure_formula
from ase2sprkkr_gui.physics.lattice import detect_structure_kind

# Keep a compact preview responsive even for a large supercell or cluster.
_MAX_PREVIEW_SITES = 400
_CELL_CORNERS = np.array(
    [[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)],
    dtype=float,
)


def _host_atoms(host):
    """Resolve a periodic host Potential or geometrical host reference."""
    return getattr(host, "atoms", host) if host is not None else None


def _host_status(host) -> str | None:
    try:
        status = host.SCF_INFO.SCFSTATUS()
    except (AttributeError, TypeError, KeyError):
        return None
    return str(status).strip().upper() if status is not None else None


def _periodicity(atoms) -> str:
    axes = [axis for axis, enabled in zip("xyz", atoms.get_pbc()) if enabled]
    if not axes:
        return "None (finite cluster)"
    return ", ".join(axes) + f" ({len(axes)}D)"


def structure_facts(atoms, *, kind=None, status=None) -> str:
    """Plain-text structure facts, including meaningful cell data if periodic."""
    if atoms is None:
        return ""
    kind = detect_structure_kind(atoms) if kind is None else kind
    lines = [
        f"Type: {structure_kind_label(kind) or 'Unknown structure'}",
        f"Composition: {structure_formula(atoms)}",
        "Elements: " + ", ".join(sorted(set(atoms.get_chemical_symbols()))),
        f"Sites: {len(atoms)}",
        f"Periodicity: {_periodicity(atoms)}",
    ]
    if any(atoms.get_pbc()):
        # The third length in a 2D slab may denote vacuum/lead separation;
        # report the actual simulation cell, not an inferred bulk lattice.
        a, b, c, alpha, beta, gamma = atoms.cell.cellpar()
        lines.extend((
            f"Cell (Å): a={a:.3f}, b={b:.3f}, c={c:.3f}",
            f"Angles (°): α={alpha:.1f}, β={beta:.1f}, γ={gamma:.1f}",
        ))
    if status:
        lines.append(f"SCF status: {status}")
    return "\n".join(lines)


class StructureOverviewWidget(QFrame):
    """Current cluster/bulk summary and, when applicable, its periodic host.

    The plot only uses positions/numbers/cell; it never materializes a Potential,
    modifies an Atoms object, or reclassifies its host as a cluster.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("guidedStructureOverview")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._atoms = None
        self._host = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(7)

        title = QLabel("Structure", self)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        outer.addWidget(title)

        self.preview_source = QComboBox(self)
        self.preview_source.setObjectName("guidedStructurePreviewSource")
        self.preview_source.currentIndexChanged.connect(self._render_preview)
        outer.addWidget(self.preview_source)
        self.preview = EmbeddingPreview(self, compact=True)
        self.preview.setObjectName("guidedStructurePreview")
        self.preview.setMinimumHeight(190)
        self.preview.setMaximumHeight(240)
        outer.addWidget(self.preview)

        info_scroll = QScrollArea(self)
        info_scroll.setWidgetResizable(True)
        info_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._info_widget = QWidget(info_scroll)
        info = QVBoxLayout(self._info_widget)
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(7)

        self.details = QLabel(self._info_widget)
        self.details.setObjectName("guidedStructureFacts")
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        info.addWidget(self.details)

        self.host_heading = QLabel("Host structure", self._info_widget)
        host_font = self.host_heading.font()
        host_font.setBold(True)
        self.host_heading.setFont(host_font)
        info.addWidget(self.host_heading)

        self.host_details = QLabel(self._info_widget)
        self.host_details.setObjectName("guidedHostFacts")
        self.host_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.host_details.setTextFormat(Qt.TextFormat.PlainText)
        self.host_details.setWordWrap(True)
        info.addWidget(self.host_details)
        info.addStretch(1)

        info_scroll.setWidget(self._info_widget)
        outer.addWidget(info_scroll, 1)

    def set_structure(self, atoms, *, kind=None, scf_status=None) -> None:
        """Refresh latest structure and its host, including in-place SCF updates."""
        self._atoms = atoms
        self._host = getattr(atoms, "embedding_host", None)
        self.details.setText(structure_facts(atoms, kind=kind, status=scf_status))
        host_atoms = _host_atoms(self._host)
        has_host = host_atoms is not None
        self.host_heading.setVisible(has_host)
        self.host_details.setVisible(has_host)
        self.host_details.setText(
            structure_facts(host_atoms, status=_host_status(self._host))
            if has_host else ""
        )

        current = self.preview_source.currentData()
        self.preview_source.blockSignals(True)
        self.preview_source.clear()
        if atoms is not None:
            self.preview_source.addItem("Current structure", "current")
            if has_host:
                self.preview_source.addItem("Host structure", "host")
            if current == "host" and has_host:
                self.preview_source.setCurrentIndex(1)
        self.preview_source.blockSignals(False)
        self.preview_source.setVisible(has_host)
        self.preview.setVisible(atoms is not None)
        self._render_preview()

    def _render_preview(self, *_args) -> None:
        atoms = (
            _host_atoms(self._host)
            if self.preview_source.currentData() == "host"
            else self._atoms
        )
        if atoms is None:
            self.preview.set_points([], [], [], cell_corners=np.empty((0, 3)))
            return

        positions = np.asarray(atoms.get_positions(), dtype=float)
        atomic_numbers = np.asarray(atoms.get_atomic_numbers(), dtype=int)
        if len(positions) > _MAX_PREVIEW_SITES:
            keep = np.linspace(0, len(positions) - 1, _MAX_PREVIEW_SITES, dtype=int)
            positions = positions[keep]
            atomic_numbers = atomic_numbers[keep]
        colors = [
            jmol_colors[int(number)] if 0 <= number < len(jmol_colors)
            else (0.55, 0.55, 0.55)
            for number in atomic_numbers
        ]
        sizes = np.full(len(positions), 48)
        corners = (
            _CELL_CORNERS @ np.asarray(atoms.cell, dtype=float)
            if any(atoms.get_pbc()) else np.empty((0, 3))
        )
        self.preview.set_points(positions, colors, sizes, cell_corners=corners)
