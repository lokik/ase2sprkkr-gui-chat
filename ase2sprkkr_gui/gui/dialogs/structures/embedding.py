"""Interactive editor for a finite cluster embedded in an infinite SPR-KKR host."""
from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ase2sprkkr_gui.ase.embedding import (
    EmbeddingDraft, HostSite, embedding_source, occupation_label,
)
from ase2sprkkr_gui.gui.widgets.structures.occupation import OccupationEditor


class BuildEmbeddingDialog(QDialog):
    """Build/edit a cluster without mutating its periodic host."""

    def __init__(self, atoms, parent: QWidget | None = None):
        super().__init__(parent)
        host, cluster = embedding_source(atoms)
        self.draft = EmbeddingDraft(host, cluster)
        self.setWindowTitle("Edit Embedded Cluster" if cluster is not None else "Build Embedded Cluster")
        self.resize(1450, 950)
        self.result_atoms = None
        self._selected_cell = (0,) * self.draft.dimension
        self._selected_site: HostSite | None = None
        self._occupation_overwrite_approval = None
        self._site_rows: list[HostSite] = []
        self._grid_updating = False
        self._site_updating = False
        self._highlighted: set[HostSite] = set()
        self._rendered_sites: list[HostSite] = []
        self._local_sites: list[HostSite] = []
        self._scatter = None
        self._local_scatter = None
        self._global_timer = QTimer(self)
        self._global_timer.setInterval(120)
        self._global_timer.setSingleShot(True)
        self._global_timer.timeout.connect(self._render_global)
        self._local_timer = QTimer(self)
        self._local_timer.setInterval(80)
        self._local_timer.setSingleShot(True)
        self._local_timer.timeout.connect(self._render_local)
        self._displacement_timer = QTimer(self)
        self._displacement_timer.setSingleShot(True)
        self._displacement_timer.setInterval(350)
        self._displacement_timer.timeout.connect(self._assign_displacement)
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
        # The previous itemSelectionChanged/currentItem pair could read the
        # *previous* current item during a mouse selection transition.
        self.cells.currentCellChanged.connect(self._cell_changed)
        cells_and_actions = QHBoxLayout()
        cells_and_actions.addWidget(self.cells, 1)
        cell_actions = QVBoxLayout()
        include = QPushButton("Include")
        include.clicked.connect(self._include)
        cell_actions.addWidget(include)
        exclude = QPushButton("Exclude")
        exclude.clicked.connect(self._exclude)
        cell_actions.addWidget(exclude)
        centre = QPushButton("Set centre")
        centre.clicked.connect(self._set_centre)
        cell_actions.addWidget(centre)
        cell_actions.addStretch(1)
        cells_and_actions.addLayout(cell_actions)
        left_v.addLayout(cells_and_actions, 1)
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

        self.symmetric = QCheckBox("Apply to symmetry-equivalent sites")
        self.symmetric.toggled.connect(self._update_symmetry_preview)
        right_v.addWidget(self.symmetric)
        sym_row = QHBoxLayout()
        self.symmetry_info = QLabel()
        self.symmetry_info.setWordWrap(True)
        sym_row.addWidget(self.symmetry_info, 1)
        right_v.addLayout(sym_row)

        right_v.addWidget(QLabel("Occupation"))
        self.occupation = OccupationEditor(
            right, require_full=True, allow_duplicates=False
        )
        # An incomplete mixture stays in this control. Once its fractions
        # are valid, publish the edit to the cluster without another button.
        self.occupation.occupationEdited.connect(self._occupation_edited)
        right_v.addWidget(self.occupation)

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
            spin.valueChanged.connect(self._schedule_displacement)
            spin.editingFinished.connect(self._apply_pending_displacement)
            displacement_row.addWidget(spin)
        right_v.addLayout(displacement_row)
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

        preview_splitter = QSplitter(Qt.Orientation.Horizontal, self)

        local_group = QGroupBox("Selected host cell · click to select", preview_splitter)
        local_layout = QVBoxLayout(local_group)
        self.local_info = QLabel()
        local_layout.addWidget(self.local_info)
        self.local_figure = Figure(figsize=(4.5, 3))
        self.local_figure.subplots_adjust(left=0, right=1, bottom=0, top=1)
        self.local_canvas = FigureCanvas(self.local_figure)
        self.local_axes = self.local_figure.add_subplot(111, projection="3d")
        self.local_canvas.mpl_connect("pick_event", self._local_pick)
        local_layout.addWidget(self.local_canvas, 1)
        preview_splitter.addWidget(local_group)

        global_group = QGroupBox("Result / symmetry · view only", preview_splitter)
        global_layout = QVBoxLayout(global_group)
        global_header = QHBoxLayout()
        self.preview_info = QLabel()
        global_header.addWidget(self.preview_info)
        global_header.addStretch(1)
        self.global_scope = QComboBox()
        self.global_scope.addItem("Whole cluster", "whole")
        self.global_scope.addItem(
            "Current layer" if self.draft.dimension == 3 else "Selected cell",
            "layer",
        )
        self.global_scope.currentIndexChanged.connect(self._schedule_global_render)
        global_header.addWidget(self.global_scope)
        global_layout.addLayout(global_header)
        global_layout.addWidget(QLabel(
            "Gold: selected   Purple: symmetry equivalents   "
            "Blue: included   Orange: modified"
        ))
        self.figure = Figure(figsize=(4.5, 3))
        self.figure.subplots_adjust(left=0, right=1, bottom=0, top=1)
        self.canvas = FigureCanvas(self.figure)
        self.axes = self.figure.add_subplot(111, projection="3d")
        global_layout.addWidget(self.canvas, 1)
        preview_splitter.addWidget(global_group)
        preview_splitter.setSizes([650, 650])
        root.addWidget(preview_splitter, 2)

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
        if (
            self._selected_site is not None
            and not self.occupation.is_valid()
            and self._selected_cell[2] != self.layer.value()
        ):
            self.layer.blockSignals(True)
            self.layer.setValue(self._selected_cell[2])
            self.layer.blockSignals(False)
            return
        self._apply_pending_displacement()
        self._refresh_cells()
        self._refresh_sites()

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
                # Selected cell and symmetry-related *other* cells have
                # different semantics; never mark orbit members as selected.
                if cell == self._selected_cell:
                    item.setBackground(QBrush(QColor("#e5bd68")))
                    item.setToolTip("Selected cell")
                elif highlighted and self.symmetric.isChecked():
                    item.setBackground(QBrush(QColor("#bba5e8")))
                    item.setToolTip("Contains symmetry-equivalent sites")
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
    def _is_modified(self, site):
        entry = self.draft.entries.get(site)
        if entry is None:
            return False
        return (entry.occupation != site.occupation(self.draft.host)
                or np.linalg.norm(entry.displacement) > 1e-8)

    def _cell_changed(self, row, column, *_):
        if self._grid_updating or row < 0 or column < 0:
            return
        self._apply_pending_displacement()
        item = self.cells.item(row, column)
        if item is None:
            return
        cell = item.data(Qt.ItemDataRole.UserRole)
        if cell is None:
            return
        if (
            tuple(cell) != self._selected_cell
            and self._selected_site is not None
            and not self.occupation.is_valid()
        ):
            # An unfinished mixture must not disappear when changing cells.
            self._refresh_cells()
            return
        self._selected_cell = tuple(cell)
        self._selected_site = None
        self._occupation_overwrite_approval = None
        self._refresh_cells()
        self._refresh_sites()

    def _refresh_sites(self, *, preserve_occupation=False):
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
        self._display_site(preserve_occupation=preserve_occupation)
        self._update_symmetry_preview()
        self._refresh_summary()

    def _site_changed(self):
        if self._site_updating:
            return
        row = self.sites.currentRow()
        if 0 <= row < len(self._site_rows):
            # A pending displacement commit refreshes the site table. Preserve
            # the user's intended *new* site across that refresh.
            next_site = self._site_rows[row]
            self._apply_pending_displacement()
            if (
                next_site != self._selected_site
                and self._selected_site is not None
                and not self.occupation.is_valid()
            ):
                # Do not silently discard invalid in-progress occupation rows.
                if self._selected_site in self._site_rows:
                    self.sites.blockSignals(True)
                    self.sites.selectRow(self._site_rows.index(self._selected_site))
                    self.sites.blockSignals(False)
                return
            if next_site != self._selected_site:
                self._occupation_overwrite_approval = None
            self._selected_site = next_site
            if next_site in self._site_rows:
                self.sites.blockSignals(True)
                self.sites.selectRow(self._site_rows.index(next_site))
                self.sites.blockSignals(False)
            self._display_site()
            self._update_symmetry_preview()

    def _display_site(self, *, preserve_occupation=False):
        site = self._selected_site
        if site is None:
            return
        entry = self.draft.entries.get(site)
        if not preserve_occupation:
            self.occupation.set_occupation(
                entry.occupation if entry else site.occupation(self.draft.host)
            )
            # The host occupation is the original value even when starting
            # a brand-new embedded cluster. The shared widget owns Restore.
            self.occupation.set_initial_occupation(site.occupation(self.draft.host))
        values = entry.displacement if entry else (0, 0, 0)
        for spin, value in zip(self.deltas, values):
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)

    def _targets(self):
        site = self._selected_site
        if site is None:
            raise ValueError("Select a host site first")
        return self.draft.targets(site, symmetric=self.symmetric.isChecked())

    def _update_symmetry_preview(self, *_):
        # All equivalent sites are visible globally, independently of whether
        # the edit-operation checkbox will apply to them.
        self._highlighted.clear()
        if self._selected_site is not None:
            try:
                self._highlighted.update(
                    self.draft.equivalents(self._selected_site)
                )
                targets = (
                    self._highlighted if self.symmetric.isChecked()
                    else {self._selected_site}
                )
            except (ValueError, IndexError) as exc:
                self.symmetry_info.setText(f"Symmetry unavailable: {exc}")
            else:
                self.symmetry_info.setText(
                    f"{len(self._highlighted)} equivalent sites; "
                    f"edits affect {len(targets)} site(s)"
                )
        self._refresh_cells()
        self._schedule_global_render()
        self._schedule_local_render()

    def _confirm_overwrite(self, targets, occupation=None):
        # Editing the selected site is intentional. Only overwriting independent
        # modifications of *other* symmetry-equivalent sites needs approval.
        conflicts = []
        for site in targets:
            if site == self._selected_site:
                continue
            entry = self.draft.entries.get(site)
            if entry is None:
                continue
            if occupation is not None and entry.occupation != occupation and self._is_modified(site):
                conflicts.append(site)
        if not conflicts:
            return True
        key = (self._selected_site, frozenset(targets))
        if key == self._occupation_overwrite_approval:
            return True
        response = QMessageBox.question(
            self, "Replace previous edits?",
            f"{len(conflicts)} other site(s) already have a modification. "
            "Apply this occupation to them too?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response == QMessageBox.StandardButton.Yes:
            self._occupation_overwrite_approval = key
            return True
        return False

    def _include(self):
        self._apply_pending_displacement()
        def action():
            targets = self._targets()
            self.draft.include(targets)
            self._changed()
        self._guard(action)

    def _exclude(self):
        self._apply_pending_displacement()
        def action():
            targets = self._targets()
            self.draft.exclude(targets)
            self._changed()
        self._guard(action)

    def _set_centre(self):
        self._apply_pending_displacement()
        def action():
            if self._selected_site is None:
                raise ValueError("Select a site first")
            self.draft.set_center(self._selected_site)
            self._changed()
        self._guard(action)

    def _occupation_edited(self):
        # Keystrokes during a mixed-occupation edit can have an incomplete
        # total. Keep them local, but never commit a partial physical state.
        if self._selected_site is not None and self.occupation.is_valid():
            self._assign_occupation()

    def _assign_occupation(self):
        self._apply_pending_displacement()
        def action():
            occupation = self.occupation.occupation()
            targets = self._targets()
            if all(
                site in self.draft.entries
                and self.draft.entries[site].occupation == occupation
                for site in targets
            ):
                return
            if not self._confirm_overwrite(targets, occupation):
                # A rejected multi-site overwrite must not leave controls
                # displaying an occupation different from the model.
                self._display_site()
                return
            self.draft.set_occupation(targets, occupation)
            # Do not replace the live row controls while the user is typing.
            self._changed(preserve_occupation=True)
        self._guard(action)

    def _restore_occupation(self):
        """Compatibility entry point; the button now lives in OccupationEditor."""
        self.occupation.restore_initial()

    def _schedule_displacement(self, *_):
        if self._selected_site is not None:
            self._displacement_timer.start()

    def _apply_pending_displacement(self):
        if self._displacement_timer.isActive():
            self._displacement_timer.stop()
            self._assign_displacement()

    def _assign_displacement(self):
        def action():
            if self._selected_site is None:
                return
            values = np.asarray([spin.value() for spin in self.deltas])
            targets = self._targets()
            if not self.symmetric.isChecked() and all(
                np.array_equal(
                    self.draft.entries[site].displacement
                    if site in self.draft.entries else np.zeros(3),
                    values,
                )
                for site in targets
            ):
                return
            self.draft.set_displacement(
                self._selected_site, values, symmetric=self.symmetric.isChecked()
            )
            self._changed(preserve_occupation=True)
        self._guard(action)

    def _add_radius(self):
        self._apply_pending_displacement()
        self._guard(lambda: (self.draft.add_radius(self.radius.value()), self._changed()))

    def _add_shells(self):
        self._apply_pending_displacement()
        self._guard(lambda: (self.draft.add_shells(self.shells.value()), self._changed()))

    def _changed(self, *, preserve_occupation=False):
        self._refresh_sites(preserve_occupation=preserve_occupation)
        self._refresh_summary()
        self._schedule_global_render()
        self._schedule_local_render()

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

    def _schedule_global_render(self, *_):
        self._global_timer.start()

    def _schedule_local_render(self, *_):
        self._local_timer.start()

    def _render(self):
        """Render both views synchronously (also useful for GUI regression tests)."""
        self._render_local()
        self._render_global()

    def _site_colour(self, site, *, local=False):
        if site == self._selected_site:
            return "#d5a13b", 100
        if site in self._highlighted:
            return "#a081cf", 75
        if self._is_modified(site):
            return "#d88c49", 65
        if site in self.draft.entries:
            return "#4b92cf", 55
        return "#a6aeb6", 23 if local else 12

    def _draw_sites(self, ax, canvas, sites, *, pick=False):
        # Preserve the user's camera orientation across content updates.
        elev, azim = ax.elev, ax.azim
        ax.clear()
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        points, colors, sizes = [], [], []
        for site in sites:
            entry = self.draft.entries.get(site)
            displacement = entry.displacement if entry is not None else 0
            points.append(site.position(self.draft.host) + displacement)
            color, size = self._site_colour(site, local=pick)
            colors.append(color)
            sizes.append(size)
        scatter = None
        if points:
            coordinates = np.asarray(points)
            scatter = ax.scatter(
                coordinates[:, 0], coordinates[:, 1], coordinates[:, 2],
                c=colors, s=sizes, depthshade=False,
                **({"picker": 7} if pick else {}),
            )
            # Axes3D's implicit autoscaling can crop edge markers in a small
            # Qt canvas, especially when the axis decorations are hidden.
            # Fit all points in an equal-scale padded Cartesian cube.
            lower = np.min(coordinates, axis=0)
            upper = np.max(coordinates, axis=0)
            centre = (lower + upper) / 2
            halfwidth = max(1.0, float(np.max(upper - lower)) * 0.62)
            ax.set_xlim(centre[0] - halfwidth, centre[0] + halfwidth)
            ax.set_ylim(centre[1] - halfwidth, centre[1] + halfwidth)
            ax.set_zlim(centre[2] - halfwidth, centre[2] + halfwidth)
            ax.set_box_aspect((1, 1, 1), zoom=0.8)
        canvas.draw_idle()
        return scatter

    def _render_local(self):
        """The pickable view contains one *regular* host cell only.

        The site table also lists exceptional native 5-vector translations
        to permit lossless editing; they do not belong to this physical cell.
        """
        self._local_sites = [
            HostSite(i, self._selected_cell).canonical(self.draft.host)
            for i in sorted(self.draft.allowed)
        ]
        self._local_scatter = self._draw_sites(
            self.local_axes, self.local_canvas, self._local_sites, pick=True
        )
        self.local_info.setText(
            f"Cell {self._selected_cell} · {len(self._local_sites)} sites"
        )

    def _render_global(self):
        """The resulting cluster, plus all symmetry equivalents of selection."""
        sites = set(self.draft.entries) | self._highlighted
        if self._selected_site is not None:
            sites.add(self._selected_site)
        if self.global_scope.currentData() == "layer":
            if self.draft.dimension == 3:
                sites = {
                    site for site in sites
                    if site.cell_index[2] == self.layer.value()
                }
            else:
                sites = {
                    site for site in sites
                    if site.cell_index[:2] == self._selected_cell[:2]
                }
        self._rendered_sites = sorted(sites)
        self._scatter = self._draw_sites(
            self.axes, self.canvas, self._rendered_sites
        )
        included_shown = sum(
            site in self.draft.entries for site in self._rendered_sites
        )
        self.preview_info.setText(
            f"Included: {included_shown}/{len(self.draft.entries)} · "
            f"Equivalents: {len(self._highlighted)} · "
            f"Displayed: {len(self._rendered_sites)}"
        )

    def _local_pick(self, event):
        """Select a site through the local view; global view is not pickable."""
        if event.artist is not self._local_scatter or not len(event.ind):
            return
        index = int(event.ind[0])
        if not 0 <= index < len(self._local_sites):
            return
        site = self._local_sites[index]
        if site not in self._site_rows:
            return
        self.sites.blockSignals(True)
        self.sites.selectRow(self._site_rows.index(site))
        self.sites.blockSignals(False)
        self._site_changed()

    def _accept(self):
        self._apply_pending_displacement()
        def action():
            if self._selected_site is not None and not self.occupation.is_valid():
                raise ValueError(
                    "Finish or restore the incomplete occupation before accepting."
                )
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
