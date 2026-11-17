"""Interactive editor for a finite cluster embedded in an infinite SPR-KKR host."""
from __future__ import annotations

from itertools import product

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ase2sprkkr_gui.ase.embedding import (
    EmbeddingDraft, HostSite, embedding_source, occupation_label, parse_occupation,
)


class BuildEmbeddingDialog(QDialog):
    """Build/edit a cluster without mutating its periodic host."""

    def __init__(self, atoms, parent: QWidget | None = None):
        super().__init__(parent)
        host, cluster = embedding_source(atoms)
        self.draft = EmbeddingDraft(host, cluster)
        self.setWindowTitle("Edit Embedded Cluster" if cluster is not None else "Build Embedded Cluster")
        self.resize(1250, 850)
        self.result_atoms = None
        self._selected_cell = (0,) * self.draft.dimension
        self._selected_site: HostSite | None = None
        self._site_rows: list[HostSite] = []
        self._grid_updating = False
        self._site_updating = False
        self._highlighted: set[HostSite] = set()
        self._rendered_sites: list[HostSite] = []
        self._scatter = None
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render)
        self._build_ui()
        self._refresh_cells()
        self._refresh_sites()
        self._refresh_summary()

    def _build_ui(self):
        root = QVBoxLayout(self)
        hint = QLabel(
            "Select a repeated host cell, then include or modify individual sites. "
            "Only included sites are written to the cluster; the host remains unchanged."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.centre_label = QLabel()
        root.addWidget(self.centre_label)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        root.addWidget(splitter, 3)

        left = QGroupBox("Host cells", splitter)
        left_v = QVBoxLayout(left)
        row = QHBoxLayout()
        row.addWidget(QLabel("In-plane cells per axis"))
        self.inplane_cells = QSpinBox()
        self.inplane_cells.setRange(1, 21)
        self.inplane_cells.setValue(5)
        self.inplane_cells.setToolTip("Actual number of cells displayed along each in-plane axis")
        self.inplane_cells.valueChanged.connect(self._refresh_cells)
        row.addWidget(self.inplane_cells)
        row.addWidget(QLabel("Centre n1, n2"))
        self.origin_x, self.origin_y = QSpinBox(), QSpinBox()
        for spin in (self.origin_x, self.origin_y):
            spin.setRange(-100000, 100000)
            spin.valueChanged.connect(self._refresh_cells)
            row.addWidget(spin)
        if self.draft.dimension == 3:
            row.addWidget(QLabel("Layer n3"))
            self.layer = QSpinBox()
            self.layer.setRange(-100, 100)
            self.layer.valueChanged.connect(self._layer_changed)
            row.addWidget(self.layer)
        else:
            self.layer = None
        row.addStretch(1)
        left_v.addLayout(row)
        self.cells = QTableWidget(left)
        self.cells.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cells.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.cells.horizontalHeader().setDefaultSectionSize(58)
        self.cells.verticalHeader().setDefaultSectionSize(46)
        self.cells.itemSelectionChanged.connect(self._cell_changed)
        left_v.addWidget(self.cells, 1)
        explanation = QLabel(
            "Blue: included sites   •   Orange: changed occupation or position   "
            "•   Purple: symmetry preview"
        )
        explanation.setWordWrap(True)
        left_v.addWidget(explanation)
        splitter.addWidget(left)

        right = QGroupBox("Sites in selected cell", splitter)
        right_v = QVBoxLayout(right)
        self.cell_label = QLabel()
        right_v.addWidget(self.cell_label)
        self.sites = QTableWidget(right)
        self.sites.setColumnCount(4)
        self.sites.setHorizontalHeaderLabels(
            ["Site", "Host occupation", "Cluster occupation", "Included"]
        )
        self.sites.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sites.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sites.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sites.horizontalHeader().setStretchLastSection(True)
        self.sites.itemSelectionChanged.connect(self._site_changed)
        right_v.addWidget(self.sites, 1)

        selection_row = QHBoxLayout()
        include = QPushButton("Include")
        include.clicked.connect(self._include)
        selection_row.addWidget(include)
        exclude = QPushButton("Exclude")
        exclude.clicked.connect(self._exclude)
        selection_row.addWidget(exclude)
        centre = QPushButton("Set centre")
        centre.clicked.connect(self._set_centre)
        selection_row.addWidget(centre)
        right_v.addLayout(selection_row)

        self.symmetric = QCheckBox("Apply to symmetry-equivalent sites")
        self.symmetric.toggled.connect(self._update_symmetry_preview)
        right_v.addWidget(self.symmetric)
        sym_row = QHBoxLayout()
        self.symmetry_info = QLabel()
        self.symmetry_info.setWordWrap(True)
        sym_row.addWidget(self.symmetry_info, 1)
        equivalents = QPushButton("Show equivalents")
        equivalents.clicked.connect(self._show_equivalents)
        sym_row.addWidget(equivalents)
        right_v.addLayout(sym_row)

        form = QFormLayout()
        self.occupation = QLineEdit()
        self.occupation.setPlaceholderText("Fe  or  Fe:0.7, Ni:0.3")
        form.addRow("Occupation", self.occupation)
        right_v.addLayout(form)
        edit_row = QHBoxLayout()
        assign = QPushButton("Apply occupation")
        assign.clicked.connect(self._assign_occupation)
        edit_row.addWidget(assign)
        reset = QPushButton("Restore host")
        reset.clicked.connect(self._restore_occupation)
        edit_row.addWidget(reset)
        right_v.addLayout(edit_row)

        right_v.addWidget(QLabel("Displacement from host position (Å)"))
        displacement_row = QHBoxLayout()
        self.deltas = []
        for label in ("x", "y", "z"):
            displacement_row.addWidget(QLabel(label))
            spin = QDoubleSpinBox()
            spin.setDecimals(5)
            spin.setRange(-100.0, 100.0)
            spin.setSingleStep(0.01)
            self.deltas.append(spin)
            displacement_row.addWidget(spin)
        right_v.addLayout(displacement_row)
        apply_delta = QPushButton("Apply displacement")
        apply_delta.clicked.connect(self._assign_displacement)
        right_v.addWidget(apply_delta)
        splitter.addWidget(right)
        splitter.setSizes([480, 680])

        options = QGroupBox("Expand cluster around centre")
        opt_row = QHBoxLayout(options)
        opt_row.addWidget(QLabel("Radius (Å):"))
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0, 1000)
        self.radius.setDecimals(2)
        self.radius.setValue(5.0)
        opt_row.addWidget(self.radius)
        add_radius = QPushButton("Add radius")
        add_radius.clicked.connect(self._add_radius)
        opt_row.addWidget(add_radius)
        opt_row.addWidget(QLabel("Coordination shells:"))
        self.shells = QSpinBox()
        self.shells.setRange(0, 50)
        self.shells.setValue(1)
        opt_row.addWidget(self.shells)
        add_shells = QPushButton("Add shells")
        add_shells.clicked.connect(self._add_shells)
        opt_row.addWidget(add_shells)
        opt_row.addStretch(1)
        root.addWidget(options)

        preview_group = QGroupBox("Cluster preview")
        preview_layout = QVBoxLayout(preview_group)
        preview_header = QHBoxLayout()
        self.preview_info = QLabel()
        preview_header.addWidget(self.preview_info)
        preview_header.addStretch(1)
        preview_header.addWidget(QLabel(
            "Blue: included   Orange: modified   Purple: symmetry   Gray: host"
        ))
        preview_layout.addLayout(preview_header)
        self.figure = Figure(figsize=(9, 3.1))
        self.figure.subplots_adjust(left=0, right=1, bottom=0, top=1)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.mpl_connect("pick_event", self._preview_pick)
        self.axes = self.figure.add_subplot(111, projection="3d")
        preview_layout.addWidget(self.canvas, 1)
        root.addWidget(preview_group, 2)

        self.summary = QLabel()
        root.addWidget(self.summary)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def _guard(self, operation):
        try:
            return operation()
        except (ValueError, TypeError, IndexError) as exc:
            QMessageBox.warning(self, "Embedded Cluster", str(exc))
            return None

    def _layer_changed(self, _value):
        self._refresh_cells()

    def _cell_index(self, x, y):
        x += self.origin_x.value()
        y += self.origin_y.value()
        if self.draft.dimension == 3:
            return x, y, self.layer.value()
        return x, y

    def _inplane_offsets(self):
        # Even cell counts are legal. The centre is the left/lower centre
        # cell, so changing 4 to 5 does not unexpectedly move the origin.
        n = self.inplane_cells.value()
        return tuple(range(-(n // 2), n - n // 2))

    def _refresh_cells(self, *_):
        offsets = self._inplane_offsets()
        size = len(offsets)
        self._grid_updating = True
        self.cells.blockSignals(True)
        self.cells.setRowCount(size)
        self.cells.setColumnCount(size)
        self.cells.setHorizontalHeaderLabels([
            str(self.origin_x.value() + x) for x in offsets
        ])
        self.cells.setVerticalHeaderLabels([
            str(self.origin_y.value() + y) for y in reversed(offsets)
        ])
        # The grid only needs included/highlighted identities; avoid
        # enumerating every atom of a large host in every visible cell.
        for r in range(size):
            for c in range(size):
                cell = self._cell_index(offsets[c], offsets[size - 1 - r])
                sites = [
                    site for site in self.draft.entries
                    if site.cell_index[:self.draft.dimension] == cell
                ]
                count = len(sites)
                changed = any(self._is_modified(site) for site in sites)
                highlighted = any(
                    site.cell_index[:self.draft.dimension] == cell
                    for site in self._highlighted
                )
                item = QTableWidgetItem("★" if changed else str(count) if count else "·")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, cell)
                if highlighted:
                    item.setBackground(QBrush(QColor("#bba5e8")))
                elif changed:
                    item.setBackground(QBrush(QColor("#f3bd81")))
                elif count:
                    item.setBackground(QBrush(QColor("#a2c9ed")))
                self.cells.setItem(r, c, item)
        cell = self._selected_cell
        if self.draft.dimension == 3 and self.layer.value() != cell[2]:
            self._selected_cell = (cell[0], cell[1], self.layer.value())
            cell = self._selected_cell
        rel_x = cell[0] - self.origin_x.value()
        rel_y = cell[1] - self.origin_y.value()
        if rel_x in offsets and rel_y in offsets:
            self.cells.setCurrentCell(
                size - 1 - offsets.index(rel_y), offsets.index(rel_x)
            )
        else:
            self.cells.clearSelection()
        self.cells.blockSignals(False)
        self._grid_updating = False
        self._timer.start()

    def _is_modified(self, site):
        entry = self.draft.entries.get(site)
        if entry is None:
            return False
        return (entry.occupation != site.occupation(self.draft.host)
                or np.linalg.norm(entry.displacement) > 1e-8)

    def _cell_changed(self):
        if self._grid_updating:
            return
        item = self.cells.currentItem()
        if item is None:
            return
        cell = item.data(Qt.ItemDataRole.UserRole)
        if cell is None:
            return
        self._selected_cell = tuple(cell)
        self._selected_site = None
        self._refresh_sites()

    def _refresh_sites(self):
        self._site_updating = True
        self.sites.blockSignals(True)
        self._site_rows = list(self.draft.cell_sites(self._selected_cell))
        self.cell_label.setText("Cell " + str(self._selected_cell))
        self.sites.setRowCount(len(self._site_rows))
        for row, site in enumerate(self._site_rows):
            entry = self.draft.entries.get(site)
            labels = (
                f"#{site.original_site + 1}",
                occupation_label(site.occupation(self.draft.host)),
                occupation_label(entry.occupation) if entry else "—",
                "Yes" if entry else "No",
            )
            for col, label in enumerate(labels):
                self.sites.setItem(row, col, QTableWidgetItem(label))
        if self._site_rows:
            if self._selected_site not in self._site_rows:
                self._selected_site = self._site_rows[0]
            self.sites.selectRow(self._site_rows.index(self._selected_site))
        else:
            self._selected_site = None
        self.sites.blockSignals(False)
        self._site_updating = False
        self._display_site()
        self._update_symmetry_preview()
        self._refresh_summary()

    def _site_changed(self):
        if self._site_updating:
            return
        row = self.sites.currentRow()
        if 0 <= row < len(self._site_rows):
            self._selected_site = self._site_rows[row]
            self._display_site()
            self._update_symmetry_preview()

    def _display_site(self):
        site = self._selected_site
        if site is None:
            return
        entry = self.draft.entries.get(site)
        self.occupation.setText(occupation_label(
            entry.occupation if entry else site.occupation(self.draft.host)
        ))
        values = entry.displacement if entry else (0, 0, 0)
        for spin, value in zip(self.deltas, values):
            spin.setValue(float(value))

    def _targets(self):
        site = self._selected_site
        if site is None:
            raise ValueError("Select a host site first")
        return self.draft.targets(site, symmetric=self.symmetric.isChecked())

    def _update_symmetry_preview(self, *_):
        self._highlighted.clear()
        if self._selected_site is not None:
            try:
                targets = self._targets()
            except (ValueError, IndexError) as exc:
                self.symmetry_info.setText(f"Symmetry unavailable: {exc}")
            else:
                self._highlighted.update(targets)
                self.symmetry_info.setText(f"{len(targets)} site(s) affected")
        self._refresh_cells()

    def _show_equivalents(self):
        def action():
            if self._selected_site is None:
                raise ValueError("Select a site first")
            self._highlighted = set(self.draft.equivalents(self._selected_site))
            self.symmetry_info.setText(
                f"{len(self._highlighted)} symmetry-equivalent site(s) highlighted"
            )
            self._refresh_cells()
        self._guard(action)

    def _confirm_overwrite(self, targets, occupation=None):
        conflicts = []
        for site in targets:
            entry = self.draft.entries.get(site)
            if entry is None:
                continue
            if occupation is not None and entry.occupation != occupation and self._is_modified(site):
                conflicts.append(site)
        if not conflicts:
            return True
        response = QMessageBox.question(
            self, "Replace previous edits?",
            f"{len(conflicts)} site(s) already have a modification. Replace them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return response == QMessageBox.StandardButton.Yes

    def _include(self):
        def action():
            targets = self._targets()
            self.draft.include(targets)
            self._changed()
        self._guard(action)

    def _exclude(self):
        def action():
            targets = self._targets()
            self.draft.exclude(targets)
            self._changed()
        self._guard(action)

    def _set_centre(self):
        def action():
            if self._selected_site is None:
                raise ValueError("Select a site first")
            self.draft.set_center(self._selected_site)
            self._changed()
        self._guard(action)

    def _assign_occupation(self):
        def action():
            occupation = parse_occupation(self.occupation.text())
            targets = self._targets()
            if not self._confirm_overwrite(targets, occupation):
                return
            self.draft.set_occupation(targets, occupation)
            self._changed()
        self._guard(action)

    def _restore_occupation(self):
        def action():
            targets = self._targets()
            self.draft.reset_occupation(targets)
            self._changed()
        self._guard(action)

    def _assign_displacement(self):
        def action():
            if self._selected_site is None:
                raise ValueError("Select a site first")
            values = [spin.value() for spin in self.deltas]
            self.draft.set_displacement(
                self._selected_site, values, symmetric=self.symmetric.isChecked()
            )
            self._changed()
        self._guard(action)

    def _add_radius(self):
        self._guard(lambda: (self.draft.add_radius(self.radius.value()), self._changed()))

    def _add_shells(self):
        self._guard(lambda: (self.draft.add_shells(self.shells.value()), self._changed()))

    def _changed(self):
        self._refresh_sites()
        self._refresh_cells()
        self._refresh_summary()

    def _refresh_summary(self):
        center = self.draft.center
        self.centre_label.setText(
            f"Centre: host site #{center.original_site + 1} in {center.cell_index}"
        )
        included = len(self.draft.entries)
        changed = sum(self._is_modified(site) for site in self.draft.entries)
        has_host_pot = hasattr(self.draft.host, "LATTICE")
        suffix = "" if has_host_pot else (
            "  •  No host POTFIL: cluster geometry can be built but native "
            "CLUPOT calculations require a host potential."
        )
        self.summary.setText(f"{included} cluster sites; {changed} modified{suffix}")

    def _render(self):
        ax = self.axes
        ax.clear()
        ax.set_axis_off()
        offsets = self._inplane_offsets()
        layer = self.layer.value() if self.layer else 0
        xs, ys, zs, colors, sizes = [], [], [], [], []
        # Cap context markers; cluster sites are always shown independently.
        displayed_cells = len(offsets) ** 2
        limit = max(1, 20000 // displayed_cells)
        context = (
            HostSite(i, self._cell_index(x, y))
            for i in sorted(self.draft.allowed)[:limit]
            for x, y in product(offsets, repeat=2)
        )
        # Show the entire finite cluster, including selected sites in other
        # layers or outside the currently visible host-cell map.
        self._rendered_sites = list(dict.fromkeys(
            [*context, *sorted(self.draft.entries), *sorted(self._highlighted)]
        ))
        for site in self._rendered_sites:
            position = site.position(self.draft.host)
            entry = self.draft.entries.get(site)
            displaced = position + (entry.displacement if entry is not None else 0)
            xs.append(displaced[0])
            ys.append(displaced[1])
            zs.append(displaced[2])
            if site in self._highlighted:
                colors.append("#a081cf")
                sizes.append(85)
            elif self._is_modified(site):
                colors.append("#d88c49")
                sizes.append(75)
            elif entry is not None:
                colors.append("#4b92cf")
                sizes.append(60)
            else:
                colors.append("#a6aeb6")
                sizes.append(15)
        self._scatter = None
        if xs:
            self._scatter = ax.scatter(
                xs, ys, zs, c=colors, s=sizes, depthshade=False, picker=5
            )
        self.preview_info.setText(
            f"Host layer {layer} · Cluster: {len(self.draft.entries)} sites "
            f"· Displayed: {len(self._rendered_sites)} atoms"
        )
        self.canvas.draw_idle()

    def _preview_pick(self, event):
        """Select a physical host site by clicking its rendered atom."""
        if event.artist is not self._scatter or not len(event.ind):
            return
        index = int(event.ind[0])
        if not 0 <= index < len(self._rendered_sites):
            return
        site = self._rendered_sites[index]
        self._selected_site = site
        # Pan to distant cells, rather than limiting navigation to [-10, 10].
        for spin, value in zip(
            (self.origin_x, self.origin_y), site.cell_index[:2]
        ):
            spin.blockSignals(True)
            spin.setValue(int(value))
            spin.blockSignals(False)
        if self.layer is not None:
            self.layer.blockSignals(True)
            self.layer.setValue(int(site.cell_index[2]))
            self.layer.blockSignals(False)
        self._selected_cell = site.cell_index[:self.draft.dimension]
        self._refresh_cells()
        self._refresh_sites()

    def _accept(self):
        def action():
            radial_reset = self.draft.would_discard_radial_data()
            if radial_reset:
                choice = QMessageBox.warning(
                    self, "Existing Radial Data",
                    "The edited cluster differs from its original calculation. "
                    "Rebuilding the structure will discard the existing radial "
                    "potential, charge and moment data and produce a new START "
                    "structure. The original cluster will not be modified. "
                    "Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if choice != QMessageBox.StandardButton.Yes:
                    return
            self.result_atoms = (
                self.draft.original_cluster
                if self.draft.is_unchanged()
                else self.draft.to_atoms(allow_radial_reset=radial_reset)
            )
            self.accept()
        self._guard(action)


def select_build_embedding_structure(atoms, parent: QWidget | None = None):
    """Return new embedded atoms, or None if editing was cancelled."""
    dialog = BuildEmbeddingDialog(atoms, parent)
    return dialog.result_atoms if dialog.exec() == QDialog.DialogCode.Accepted else None
