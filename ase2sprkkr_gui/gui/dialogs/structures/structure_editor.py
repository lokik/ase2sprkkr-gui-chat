"""Tabbed transactional structure editor for periodic and embedded systems.

For a cluster, Sites, Lattice and Transform address the periodic host.  The
Embedded page owns cluster-specific substitutions and displacements.  All
changes stay outside WorkspaceController until the outer dialog is accepted.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSplitter, QStackedWidget,
    QStyle, QTabWidget, QVBoxLayout, QWidget,
)

from ase2sprkkr.sprkkr.embedding import host_atoms
from ase2sprkkr.sprkkr.embedding_transforms import (
    embedded_host_transform_capability,
    rebase_embedded_host,
    repeat_embedded_host,
)
from ase2sprkkr_gui.gui.dialogs.structures.element_assignment import (
    ElementAssignmentDialog,
)
from ase2sprkkr_gui.gui.dialogs.structures.transforms import (
    repeat_atoms, rotate_atoms, scale_atoms, select_repeat_factors,
)
from ase2sprkkr_gui.gui.widgets.action_card import configure_action_card
from ase2sprkkr_gui.gui.widgets.structures.editor_overview import EditorOverviewWidget
from ase2sprkkr_gui.gui.widgets.structures.editor_preview import EditorClusterHostPreviews
from ase2sprkkr_gui.gui.widgets.structures.editor_section import EditorSection
from ase2sprkkr_gui.physics.cell_parameters import (
    cell_from_lengths_angles, lengths_and_angles,
)
from ase2sprkkr_gui.physics.lattice import detect_structure_kind
from ase2sprkkr_gui.gui.misc.resources import icon_path


def _is_cluster(atoms) -> bool:
    return "sprkkr_embedding" in getattr(atoms, "arrays", {})


class StructureEditorDialog(ElementAssignmentDialog):
    """An independent editable draft with one outer Apply/Cancel boundary."""

    def __init__(
        self, atoms: Any, parent: QWidget | None = None, *,
        recent_files: Any = None, allow_build_2d: bool = False,
        allow_build_embedding: bool = False,
    ) -> None:
        self._original_atoms = atoms
        self._working_atoms = atoms
        self._recent_files = recent_files
        self._allow_build_2d = allow_build_2d
        self._allow_build_embedding = allow_build_embedding
        self._embedded_editor = None
        self._embedded_page = None
        self._discarded_host_atoms = None
        self._discarded_host_potential = None
        self._switching_tabs = False
        self._last_tab = 0
        self._editor_ready = False
        self._invalid_cell_parameters = None
        self._cell_parameters_valid = True
        super().__init__(parent, editor_mode=True)
        self.setWindowTitle("Structure Editor")
        self.resize(1450, 900)
        self.setup(atoms)
        self.ok_btn.setText("Apply Structure")
        self.cancel_btn.setText("Cancel")
        self._editor_ready = True

    def _build_structure_editor_layout(
        self, root, sites_panel: QWidget, preview_panel: QWidget, lattice_group: QWidget
    ) -> None:
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("structureEditorTabs")
        self.tabs.setIconSize(QSize(22, 22))
        self.tabs.setStyleSheet(
            "QTabBar::tab { min-height: 36px; padding: 7px 13px; }"
        )
        self.overview = EditorOverviewWidget(self.tabs)
        self.tabs.addTab(self.overview, "Overview")
        self.overview.siteSelectionChanged.connect(self._overview_site_selected)

        # Preserve the per-site dividers; only the surrounding scroll frame
        # made the Sites page look like a framed inset.
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.tabs.addTab(sites_panel, "Sites")

        self.site_notice = QLabel(
            "For embedded structures this page edits the host sites and "
            "occupations. Cluster substitutions are edited on Embedded.",
            sites_panel,
        )
        self.site_notice.setWordWrap(True)
        sites_panel.layout().insertWidget(0, self.site_notice)

        lattice_page = QWidget(self.tabs)
        lattice_layout = QVBoxLayout(lattice_page)
        self.lattice_note = QLabel(lattice_page)
        self.lattice_note.setWordWrap(True)
        lattice_layout.addWidget(self.lattice_note)
        self.lattice_group = EditorSection("Lattice vectors (Å)", lattice_page)
        # Reparent the existing vector grid into a frameless section. The
        # previous QGroupBox is an implementation detail of the base editor.
        self.lattice_group.content_layout.addWidget(self.lattice_vectors_table)
        lattice_layout.addWidget(self.lattice_group)
        lattice_group.deleteLater()

        parameter_box = EditorSection(
            "Cell lengths and angles", lattice_page, layout_type=QGridLayout,
        )
        parameter_grid = parameter_box.content_layout
        self.cell_parameter_spins = {}
        for index, (key, symbol) in enumerate((
            ("a", "a"), ("b", "b"), ("c", "c"),
            ("alpha", "α"), ("beta", "β"), ("gamma", "γ"),
        )):
            spin = QDoubleSpinBox(parameter_box)
            spin.setDecimals(6)
            spin.setSingleStep(0.1)
            spin.setRange(
                0.000001 if index < 3 else 0.1,
                100000.0 if index < 3 else 179.9,
            )
            spin.setSuffix(" Å" if index < 3 else "°")
            spin.setObjectName(f"cellParameter{key.title()}")
            spin.valueChanged.connect(self._cell_parameters_edited)
            self.cell_parameter_spins[key] = spin
            row, column = index % 3, 0 if index < 3 else 2
            parameter_grid.addWidget(QLabel(symbol, parameter_box), row, column)
            parameter_grid.addWidget(spin, row, column + 1)
        lattice_layout.addWidget(parameter_box)
        self.cell_parameter_group = parameter_box
        self.cell_parameter_error = QLabel(lattice_page)
        self.cell_parameter_error.setWordWrap(True)
        self.cell_parameter_error.setStyleSheet("color: #bb4545;")
        lattice_layout.addWidget(self.cell_parameter_error)
        lattice_layout.addStretch(1)
        self.tabs.addTab(lattice_page, "Lattice")

        transformations = QWidget(self.tabs)
        transform_layout = QVBoxLayout(transformations)
        geometry = EditorSection("Geometric transformations", transformations)
        self.geometry_group = geometry
        geometry_layout = geometry.content_layout
        for label, description, icon, action in (
            ("Scale lattice", "Change lattice lengths in the current structure",
             "transform-scale", "scale"),
            ("Repeat structure", "Create a repeated supercell",
             "view-grid", "repeat"),
            ("Rotate structure (Miller indices)",
             "Change the surface orientation", "object-rotate-right", "rotate"),
        ):
            button = QPushButton(geometry)
            configure_action_card(
                button, label, description,
                QIcon.fromTheme(
                    icon, self.style().standardIcon(
                        QStyle.StandardPixmap.SP_ArrowUp,
                    ),
                ),
                category="structure",
            )
            button.clicked.connect(
                lambda _checked=False, operation=action: self._transform(operation)
            )
            geometry_layout.addWidget(button)
            setattr(self, action + "_button", button)
        transform_layout.addWidget(geometry)

        self.derived_group = EditorSection("Build derived structure", transformations)
        derived_layout = self.derived_group.content_layout
        self.build_surface_button = QPushButton(self.derived_group)
        configure_action_card(
            self.build_surface_button, "Create 2D surface",
            "Build a surface from the periodic structure",
            QIcon.fromTheme(
                "applications-science",
                self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon),
            ),
            category="structure",
        )
        self.build_surface_button.clicked.connect(
            lambda _checked=False: self._build_two_d(surface=True)
        )
        derived_layout.addWidget(self.build_surface_button)
        self.build_2d_button = QPushButton(self.derived_group)
        configure_action_card(
            self.build_2d_button, "Create 2D structure",
            "Construct a two-dimensional layered structure",
            QIcon.fromTheme(
                "applications-science",
                self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon),
            ),
            category="structure",
        )
        self.build_2d_button.clicked.connect(
            lambda _checked=False: self._build_two_d(surface=False)
        )
        derived_layout.addWidget(self.build_2d_button)
        self.embedding_button = QPushButton(self.derived_group)
        configure_action_card(
            self.embedding_button, "Build embedded cluster",
            "Create an embedded cluster from the current host",
            QIcon.fromTheme(
                "network-wired",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon),
            ),
            category="structure",
        )
        self.embedding_button.clicked.connect(self._build_embedding)
        derived_layout.addWidget(self.embedding_button)
        transform_layout.addWidget(self.derived_group)
        transform_layout.addStretch(1)
        self.tabs.addTab(transformations, "Transform")

        # Ordinary pages retain the existing 3D host preview. The Overview
        # of a cluster instead shows a vertical cluster/host comparison.
        # Embedded has its own controls and uses the whole editor width.
        self._shared_preview = preview_panel
        self.preview_stack = QStackedWidget(self)
        self.preview_stack.addWidget(preview_panel)
        self._overview_previews = EditorClusterHostPreviews(self.preview_stack)
        self.preview_stack.addWidget(self._overview_previews)
        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.editor_splitter.setChildrenCollapsible(False)
        self.editor_splitter.addWidget(self.tabs)
        self.editor_splitter.addWidget(self.preview_stack)
        self.editor_splitter.setStretchFactor(0, 3)
        self.editor_splitter.setStretchFactor(1, 2)
        self.editor_splitter.setSizes([850, 550])
        root.insertWidget(0, self.editor_splitter, 1)

        # Overview retains its existing system information icon. The remaining
        # tabs use bundled crystal-style artwork for consistent appearance on
        # platforms with different or missing desktop icon themes.
        icons = [
            QIcon.fromTheme(
                "document-properties",
                self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView),
            ),
            QIcon(str(icon_path("structure-sites.svg"))),
            QIcon(str(icon_path("structure-lattice.svg"))),
            QIcon(str(icon_path("structure-transform.svg"))),
        ]
        for index, icon in enumerate(icons):
            self.tabs.setTabIcon(index, icon)
        self.tabs.currentChanged.connect(self._tab_changed)

    def _create_embedded_page(self, cluster) -> None:
        from ase2sprkkr_gui.gui.dialogs.structures.embedding import BuildEmbeddingDialog

        if self._embedded_editor is not None:
            self._remove_embedded_page()
        page = QWidget(self.tabs)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        embedded = BuildEmbeddingDialog(cluster, page, embedded=True)
        layout.addWidget(embedded, 1)
        footer = QHBoxLayout()
        self.discard_cluster_button = QPushButton("Discard cluster…", page)
        self.discard_cluster_button.setToolTip(
            "Return to the periodic host while preserving its potential and SCF state"
        )
        self.discard_cluster_button.setIcon(
            QIcon.fromTheme(
                "edit-delete",
                self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon),
            )
        )
        self.discard_cluster_button.clicked.connect(self._discard_cluster)
        footer.addWidget(self.discard_cluster_button)
        footer.addStretch(1)
        layout.addLayout(footer)
        self._embedded_page = page
        self._embedded_editor = embedded
        index = self.tabs.insertTab(1, page, "Embedded")
        self.tabs.setTabIcon(
            index, QIcon.fromTheme(
                "network-wired",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon),
            )
        )

    def _remove_embedded_page(self) -> None:
        if self._embedded_page is None:
            return
        index = self.tabs.indexOf(self._embedded_page)
        if index >= 0:
            self.tabs.removeTab(index)
        if self._embedded_editor is not None:
            self._embedded_editor.close()
        self._embedded_page.deleteLater()
        self._embedded_page = None
        self._embedded_editor = None

    def setup(self, atoms: Any, back: bool = False) -> None:
        # Called after a transform, on construction, or when publishing the
        # embedded draft to the surrounding editing session.
        self._discarded_host_atoms = None
        self._discarded_host_potential = None
        self._working_atoms = atoms
        cluster = _is_cluster(atoms)
        target = host_atoms(atoms.embedding_host) if cluster else atoms
        previous_guard = self._switching_tabs
        self._switching_tabs = True
        try:
            super().setup(target, back=back)
            self._invalid_cell_parameters = None
            self._sync_cell_parameter_spins()
            self.site_notice.setVisible(cluster)
            self.lattice_group.setTitle(
                "Host potential lattice vectors (Å)" if cluster
                else "Lattice vectors (Å)"
            )
            self.lattice_note.setText(
                "These controls edit the HOST POTENTIAL lattice. The embedded "
                "cluster will be remapped to the updated host (including atom "
                "positions and displacements)."
                if cluster else
                "Editing cell lengths or angles preserves fractional atomic "
                "coordinates; Cartesian positions change with the lattice."
            )
            self._overview_previews.set_structure(atoms if cluster else None)
            self.overview.set_structure(atoms)
            if cluster:
                self._create_embedded_page(atoms)
            else:
                self._remove_embedded_page()
            self._refresh_operation_availability()
        finally:
            self._switching_tabs = previous_guard
        self._last_tab = self.tabs.currentIndex()
        self._update_preview_panel()

    def _embedded_index(self):
        if self._embedded_page is None:
            return -1
        return self.tabs.indexOf(self._embedded_page)

    def _refresh_operation_availability(self) -> None:
        current = self._working_atoms
        cluster = _is_cluster(current)
        kind = detect_structure_kind(current)
        if cluster:
            for action in ("scale", "repeat", "rotate"):
                allowed, reason = embedded_host_transform_capability(current, action)
                button = getattr(self, action + "_button")
                button.setEnabled(allowed)
                button.setToolTip(reason)
            supported, message = embedded_host_transform_capability(current, "sites")
            self.scroll.setEnabled(supported)
            self.site_notice.setText(
                "Editing host sites and occupations; edit cluster substitutions "
                "on Embedded." if supported else message
            )
            for row in self.lattice_spinboxes:
                for spin in row:
                    spin.setEnabled(supported)
            self.cell_parameter_group.setEnabled(
                supported and self._cell_parameters_valid
            )
            for row in self._letter_widgets:
                row.break_btn.setEnabled(False)
                row.break_btn.setToolTip(
                    "Host site splitting changes IQ indices and is not yet supported."
                )
            # The unchanged host draft must not block cluster editing because
            # of a generic occupation check unrelated to cluster semantics.
            if (self._require_draft().is_unchanged()
                    and self._invalid_cell_parameters is None):
                self.ok_btn.setEnabled(True)
        else:
            for action in ("scale", "repeat", "rotate"):
                button = getattr(self, action + "_button")
                button.setEnabled(True)
                button.setToolTip("")
            self.scroll.setEnabled(True)
            for row in self.lattice_spinboxes:
                for spin in row:
                    spin.setEnabled(True)
            self.cell_parameter_group.setEnabled(self._cell_parameters_valid)
        build_2d = kind == "3d" and not cluster
        build_embedded = kind in ("3d", "2d") and not cluster
        self.build_surface_button.setVisible(build_2d)
        self.build_2d_button.setVisible(build_2d)
        self.build_surface_button.setEnabled(
            build_2d and self._allow_build_2d and self._recent_files is not None
        )
        self.build_2d_button.setEnabled(
            build_2d and self._allow_build_2d and self._recent_files is not None
        )
        self.embedding_button.setVisible(build_embedded)
        self.embedding_button.setEnabled(build_embedded and self._allow_build_embedding)
        # isVisible() would be false while the Transform tab is hidden.
        # Derive this from the structure kind, not transient Qt visibility.
        self.derived_group.setVisible(build_2d or build_embedded)

    def _on_lattice_vector_changed(self, value: float) -> None:
        # A zero-length lattice vector has no physical meaning. Revert just
        # the newly invalid row, without blocking repairs of an already
        # malformed structure that was loaded with a zero vector.
        draft = self._require_draft()
        before = draft.cell
        proposed = np.array([
            [spin.value() for spin in row] for row in self.lattice_spinboxes
        ], dtype=float)
        was_nonzero = np.linalg.norm(before, axis=1) > 1e-10
        now_zero = np.linalg.norm(proposed, axis=1) <= 1e-10
        invalid = np.flatnonzero(was_nonzero & now_zero)
        if len(invalid):
            for row in invalid:
                for col, spin in enumerate(self.lattice_spinboxes[int(row)]):
                    spin.blockSignals(True)
                    spin.setValue(float(before[row, col]))
                    spin.blockSignals(False)
            if hasattr(self, "cell_parameter_error"):
                names = ", ".join("abc"[i] for i in invalid)
                self.cell_parameter_error.setText(
                    f"Lattice vector {names} must not have zero length."
                )
            return

        super()._on_lattice_vector_changed(value)
        if hasattr(self, "cell_parameter_spins"):
            self._invalid_cell_parameters = None
            self.cell_parameter_error.clear()
            self._sync_cell_parameter_spins()
            self._update_ok_state()

    def _sync_cell_parameter_spins(self) -> None:
        """Keep the six parameter spinboxes in sync with direct vector edits."""
        try:
            cell = self._require_draft().cell
            values = lengths_and_angles(cell)
            if abs(np.linalg.det(cell)) <= 1e-10 * np.prod(values[:3]):
                raise ValueError("The lattice vectors are coplanar")
            self._cell_parameters_valid = True
        except ValueError:
            self._cell_parameters_valid = False
            self.cell_parameter_group.setEnabled(False)
            self.cell_parameter_error.setText(
                "Cell lengths/angles require a nonsingular lattice. "
                "Edit Cartesian cell vectors to restore a valid cell."
            )
            return
        if self._invalid_cell_parameters is None:
            self.cell_parameter_error.clear()
        for spin, value in zip(self.cell_parameter_spins.values(), values):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.cell_parameter_group.setEnabled(True)

    def _cell_parameters_edited(self, _value: float) -> None:
        """Update the draft as one lattice operation (fractional sites unchanged)."""
        if self._draft is None:
            return
        values = tuple(spin.value() for spin in self.cell_parameter_spins.values())
        try:
            cell = cell_from_lengths_angles(self._require_draft().cell, values)
        except ValueError as error:
            self._invalid_cell_parameters = str(error)
            self.cell_parameter_error.setText(str(error))
            self._update_ok_state()
            return
        self._invalid_cell_parameters = None
        self.cell_parameter_error.clear()
        self._require_draft().set_cell(cell)
        for i, row in enumerate(self.lattice_spinboxes):
            for j, spin in enumerate(row):
                spin.blockSignals(True)
                spin.setValue(float(cell[i, j]))
                spin.blockSignals(False)
        self._draw_preview()
        self._update_ok_state()

    def _update_ok_state(self) -> None:
        super()._update_ok_state()
        if self._invalid_cell_parameters is not None:
            self.ok_btn.setEnabled(False)

    def _overview_site_selected(self, scope: str, row: int) -> None:
        self._overview_previews.update_selection(scope, row)

    def _update_preview_panel(self) -> None:
        cluster = _is_cluster(self._working_atoms)
        embedded = (
            self._embedded_page is not None
            and self.tabs.currentWidget() is self._embedded_page
        )
        self.preview_stack.setVisible(not embedded)
        if not embedded:
            self.preview_stack.setCurrentWidget(
                self._overview_previews
                if cluster and self.tabs.currentWidget() is self.overview
                else self._shared_preview
            )

    def _candidate(self) -> Any:
        draft = self._require_draft()
        if self._invalid_cell_parameters is not None:
            raise ValueError(self._invalid_cell_parameters)
        if draft.is_unchanged():
            return self._working_atoms
        if not draft.is_valid():
            raise ValueError("Correct incomplete site occupations before applying.")
        result = draft.apply()
        if _is_cluster(self._working_atoms):
            return rebase_embedded_host(self._working_atoms, result)
        return result

    def _accept_operation_result(self, result: Any, base: Any) -> bool:
        if result is None or result is base:
            return False
        was_cluster = _is_cluster(self._working_atoms)
        self.setup(result)
        if _is_cluster(result) and not was_cluster:
            self._switching_tabs = True
            try:
                self.tabs.setCurrentIndex(self._embedded_index())
            finally:
                self._switching_tabs = False
            self._last_tab = self.tabs.currentIndex()
            self._update_preview_panel()
        return True

    def _flush_embedded(self) -> bool:
        if self._embedded_editor is None:
            return True
        widget = self._embedded_editor
        if not widget.finalize_embedded():
            return False
        result = widget.result_atoms
        if result is not None and result is not self._working_atoms:
            self.setup(result)
        return True

    def _confirm_pending_changes(self, *, closing: bool) -> str:
        """Use the same decision vocabulary for navigation and closing."""
        question = QMessageBox(self)
        question.setIcon(QMessageBox.Icon.Warning)
        question.setWindowTitle("Unapplied Structure Changes")
        if closing:
            question.setText("The structure editor has unapplied changes.")
            question.setInformativeText(
                "Confirm applies the structure and closes the editor. "
                "Discard closes without saving anything. "
                "Continue editing keeps the editor open."
            )
        else:
            question.setText("The Embedded page has unapplied changes.")
            question.setInformativeText(
                "Confirm keeps these cluster edits and opens the selected page. "
                "Discard restores the cluster to its previous state. "
                "Continue editing stays on Embedded. "
                "Changes reach the workspace only through Apply Structure."
            )
        confirm = question.addButton("Confirm", QMessageBox.ButtonRole.AcceptRole)
        discard = question.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        keep = question.addButton(
            "Continue editing", QMessageBox.ButtonRole.RejectRole
        )
        question.setDefaultButton(keep)
        question.exec()
        selected = question.clickedButton()
        if selected is confirm:
            return "confirm"
        if selected is discard:
            return "discard"
        return "continue"

    def _leave_embedded_page(self) -> bool:
        embedded = self._embedded_editor
        if embedded is None or not embedded.has_unapplied_changes():
            return True
        choice = self._confirm_pending_changes(closing=False)
        if choice == "confirm":
            return self._flush_embedded()
        if choice == "discard":
            # Retain already accepted host edits, but discard the unconfirmed
            # cluster-specific draft (including unfinished edit controls).
            self.setup(self._working_atoms)
            return True
        return False

    def _discard_cluster(self) -> None:
        """Replace the staged cluster with the original host, not its START copy."""
        if self._embedded_editor is None:
            return
        host = self._working_atoms.embedding_host
        if host is None:
            QMessageBox.warning(
                self, "Missing Host", "This embedded cluster has no host structure."
            )
            return
        answer = QMessageBox.question(
            self, "Discard Embedded Cluster?",
            "Discard the entire embedded cluster and return to its host structure?\\n\\n"
            "The host potential and its electronic state will be retained. "
            "The workspace will change only after Apply Structure.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        from ase2sprkkr.potentials.potential_base import Potential
        result = host_atoms(host)
        # Keep the original Potential, even when its Atoms object currently
        # has no attached _potential. Attach it only on final acceptance.
        previous_guard = self._switching_tabs
        self._switching_tabs = True
        try:
            self.setup(result)
            self._discarded_host_atoms = result
            self._discarded_host_potential = (
                host if isinstance(host, Potential) else None
            )
            self.tabs.setCurrentIndex(0)
            self._last_tab = 0
            self._update_preview_panel()
        finally:
            self._switching_tabs = previous_guard

    def _tab_changed(self, index: int) -> None:
        if self._switching_tabs or not getattr(self, "_editor_ready", False):
            return
        old = self._last_tab
        destination = self.tabs.widget(index)
        self._switching_tabs = True
        valid = True
        try:
            if old == self._embedded_index() and index != old:
                valid = self._leave_embedded_page()
            if valid and index == self._embedded_index():
                try:
                    candidate = self._candidate()
                    if self._accept_operation_result(candidate, self._working_atoms):
                        # Replacing the embedded editor removes its current tab.
                        self.tabs.setCurrentIndex(self._embedded_index())
                        index = self.tabs.currentIndex()
                except (ValueError, TypeError, NotImplementedError) as error:
                    QMessageBox.warning(self, "Cannot Update Host", str(error))
                    valid = False
            if not valid:
                self.tabs.setCurrentIndex(old)
                index = old
            else:
                # Reconstructing Embedded may reset the selected tab. Restore
                # the actual destination widget rather than an obsolete index.
                if self.tabs.indexOf(destination) >= 0:
                    self.tabs.setCurrentWidget(destination)
                index = self.tabs.currentIndex()
            if valid and index == 0:
                try:
                    overview_atoms = self._candidate()
                except (ValueError, TypeError):
                    overview_atoms = self._working_atoms
                self._overview_previews.set_structure(
                    overview_atoms if _is_cluster(overview_atoms) else None
                )
                self.overview.set_structure(overview_atoms)
            self._update_preview_panel()
        finally:
            self._switching_tabs = False
        self._last_tab = index

    def _has_unapplied_changes(self) -> bool:
        """Whole-dialog dirty state, including changes confirmed on other tabs."""
        return (
            self._working_atoms is not self._original_atoms
            or not self._require_draft().is_unchanged()
            or self._invalid_cell_parameters is not None
            or (
                self._embedded_editor is not None
                and self._embedded_editor.has_unapplied_changes()
            )
        )

    def _request_close(self) -> bool:
        """Handle Cancel, Escape and window-manager close uniformly."""
        if not self._editor_ready or not self._has_unapplied_changes():
            QDialog.reject(self)
            return True
        choice = self._confirm_pending_changes(closing=True)
        if choice == "confirm":
            # Uses the same validation and potential-state handling as the
            # existing Apply Structure button.
            self._on_ok()
            return self.result() == QDialog.DialogCode.Accepted
        if choice == "discard":
            QDialog.reject(self)
            return True
        return False

    def reject(self) -> None:
        self._request_close()

    def closeEvent(self, event) -> None:
        if self._request_close():
            event.accept()
        else:
            event.ignore()

    def _run_operation(self, operation, label: str) -> None:
        try:
            base = self._candidate()
            if _is_cluster(base) and not self._flush_embedded():
                return
            result = operation(base)
            self._accept_operation_result(result, base)
        except (ValueError, TypeError, NotImplementedError) as exc:
            QMessageBox.warning(self, label, str(exc))

    def _transform(self, operation: str) -> None:
        if not _is_cluster(self._working_atoms):
            action = {"scale": scale_atoms, "repeat": repeat_atoms,
                      "rotate": rotate_atoms}[operation]
            self._run_operation(
                lambda atoms: action(atoms, parent=self),
                "Cannot Transform Structure",
            )
            return

        def apply(cluster):
            allowed, reason = embedded_host_transform_capability(cluster, operation)
            if not allowed:
                raise ValueError(reason)
            source = host_atoms(cluster.embedding_host)
            if operation == "repeat":
                selected = select_repeat_factors(atoms=source, parent=self)
                if selected is None:
                    return None
                if any(not float(value).is_integer() for value in selected):
                    raise ValueError(
                        "Embedded host repeats must be integers; fractional repeats "
                        "cannot preserve periodic host-site indices."
                    )
                return repeat_embedded_host(cluster, tuple(map(int, selected)))
            edited = scale_atoms(source, parent=self)
            if edited is None or edited is source:
                return None
            return rebase_embedded_host(cluster, edited)

        self._run_operation(apply, "Cannot Transform Embedded Host")

    def _build_two_d(self, *, surface: bool) -> None:
        from ase2sprkkr_gui.gui.dialogs.structures.build_2d import (
            select_build_2d_structure,
        )
        if self._recent_files is None:
            return
        self._run_operation(
            lambda atoms: select_build_2d_structure(
                atoms, parent=self, recent_files=self._recent_files,
                surface_mode=surface,
            ),
            "Cannot Build 2D Structure",
        )

    def _build_embedding(self) -> None:
        from ase2sprkkr_gui.ase.embedding import prepare_embedding_source
        from ase2sprkkr_gui.gui.dialogs.structures.embedding import (
            select_build_embedding_structure,
        )

        def build(atoms):
            # Building again immediately after discarding a cluster must
            # reuse the same native host Potential, including its SCF state.
            prepared = (
                self._discarded_host_potential
                if (atoms is self._discarded_host_atoms
                    and self._discarded_host_potential is not None)
                else prepare_embedding_source(atoms)
            )
            result = select_build_embedding_structure(prepared, parent=self)
            return None if result is prepared else result

        self._run_operation(build, "Cannot Build Embedded Cluster")

    def _on_ok(self) -> None:
        if not self._flush_embedded():
            return
        try:
            result = self._candidate()
        except (ValueError, TypeError, NotImplementedError) as exc:
            QMessageBox.warning(self, "Cannot Apply Structure", str(exc))
            return
        if (result is not self._original_atoms
                and _is_cluster(self._original_atoms)
                and _is_cluster(result)
                and result.embedding_host is not self._original_atoms.embedding_host):
            # The backend resets electronic data on a host rebase. Ensure
            # this is a deliberate acceptance when the old SCF is reusable.
            old_host = self._original_atoms.embedding_host
            has_results = False
            if hasattr(old_host, "SCF_INFO"):
                try:
                    has_results = str(old_host.SCF_INFO.SCFSTATUS()).upper() not in (
                        "START", "NONE", ""
                    )
                except (ValueError, TypeError, AttributeError):
                    pass
            if not has_results:
                old_status = self._original_atoms.info.get("sprkkr_embedding_scf_status")
                has_results = str(old_status).upper() not in ("NONE", "START", "")
            if has_results:
                answer = QMessageBox.warning(
                    self,
                    "Host Transformation Invalidates SCF",
                    "The edited host changes the reference geometry of the "
                    "embedded cluster. Existing host and cluster electronic "
                    "results cannot be carried over as converged: the new "
                    "structures will start from START. Apply this change?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        if (result is self._discarded_host_atoms
                and self._discarded_host_potential is not None):
            # Do not change the source's potential while the dialog is open.
            # A discarded cluster must not create a fresh START potential.
            result._potential = self._discarded_host_potential
        self._result = None if result is self._original_atoms else result
        self.accept()


def select_structure_editor(
    atoms: Any, parent: QWidget | None = None, *,
    recent_files: Any = None, allow_build_2d: bool = False,
    allow_build_embedding: bool = False,
) -> Any | None:
    """Return a detached accepted structure, or None on cancel/no-op."""
    dialog = StructureEditorDialog(
        atoms, parent, recent_files=recent_files,
        allow_build_2d=allow_build_2d, allow_build_embedding=allow_build_embedding,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog._result
