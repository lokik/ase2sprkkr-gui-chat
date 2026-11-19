"""Unified, transactional editor for atomic structures.

Sites, occupancies, cell vectors and a live preview share the same draft.
Operations such as scale/repeat/rotate and the specialized embedding builder
can be applied to that draft without publishing intermediate workspace states.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr_gui.gui.dialogs.structures.element_assignment import (
    ElementAssignmentDialog,
)
from ase2sprkkr_gui.gui.dialogs.structures.transforms import (
    repeat_atoms,
    rotate_atoms,
    scale_atoms,
)


class StructureEditorDialog(ElementAssignmentDialog):
    """Edit an independent structure draft, returning one result only on Apply.

    The existing element-assignment controls are reused as *part of* this
    window, not opened as another dialog.  The live 3D preview stays visible
    when switching between editing pages.
    """

    def __init__(
        self,
        atoms: Any,
        parent: QWidget | None = None,
        *,
        recent_files: Any = None,
        allow_build_2d: bool = False,
        allow_build_embedding: bool = False,
    ) -> None:
        self._original_atoms = atoms
        self._working_atoms = atoms
        self._recent_files = recent_files
        self._allow_build_2d = allow_build_2d
        self._allow_build_embedding = allow_build_embedding
        super().__init__(parent, editor_mode=True)
        self.setWindowTitle("Structure Editor")
        self.resize(1450, 850)
        self.setup(atoms)
        self.ok_btn.setText("Apply Structure")
        self.cancel_btn.setText("Cancel")

    def _build_structure_editor_layout(
        self, root, sites_panel: QWidget, preview_panel: QWidget, lattice_group: QWidget
    ) -> None:
        """Compose site, lattice and preview controls in their final positions."""

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("structureEditorTabs")
        self.tabs.addTab(sites_panel, "Sites & occupations")
        self.site_notice = QLabel(
            "Use the embedded-cluster editor for cluster site and geometry "
            "changes. It preserves host indexing and radial-data semantics.",
            sites_panel,
        )
        self.site_notice.setWordWrap(True)
        self.site_notice.setVisible(False)
        sites_panel.layout().insertWidget(0, self.site_notice)

        lattice_page = QWidget(self.tabs)
        lattice_layout = QVBoxLayout(lattice_page)
        lattice_layout.addWidget(lattice_group)
        self.cell_summary = QLabel(lattice_page)
        self.cell_summary.setWordWrap(True)
        lattice_layout.addWidget(self.cell_summary)
        note = QLabel(
            "Atomic fractional positions can be edited in Sites & occupations.",
            lattice_page,
        )
        note.setWordWrap(True)
        lattice_layout.addWidget(note)
        lattice_layout.addStretch(1)
        self.tabs.addTab(lattice_page, "Lattice")

        operations_page = QWidget(self.tabs)
        operations_layout = QVBoxLayout(operations_page)
        for label, callback in (
            ("Scale lattice...", scale_atoms),
            ("Repeat structure...", repeat_atoms),
            ("Rotate structure...", rotate_atoms),
        ):
            button = QPushButton(label, operations_page)
            button.clicked.connect(
                lambda _checked=False, operation=callback: self._transform(operation)
            )
            operations_layout.addWidget(button)
            if callback is scale_atoms:
                self.scale_button = button
            elif callback is repeat_atoms:
                self.repeat_button = button
            else:
                self.rotate_button = button
        operations_note = QLabel(
            "Operations use the current edited occupations, positions and "
            "lattice. Their result is only saved when Apply Structure is pressed.",
            operations_page,
        )
        operations_note.setWordWrap(True)
        operations_layout.addWidget(operations_note)
        operations_layout.addStretch(1)
        self.tabs.addTab(operations_page, "Transform")

        advanced_page = QWidget(self.tabs)
        advanced_layout = QVBoxLayout(advanced_page)
        self.build_2d_button = QPushButton("Build 2D structure...", advanced_page)
        self.build_2d_button.clicked.connect(self._build_two_d)
        self.build_2d_button.setEnabled(
            self._allow_build_2d and self._recent_files is not None
        )
        advanced_layout.addWidget(self.build_2d_button)
        self.embedding_button = QPushButton(
            "Build / edit embedded cluster...", advanced_page
        )
        self.embedding_button.clicked.connect(self._edit_embedding)
        self.embedding_button.setEnabled(self._allow_build_embedding)
        advanced_layout.addWidget(self.embedding_button)
        advanced_layout.addStretch(1)
        self.tabs.addTab(advanced_page, "Advanced")

        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.editor_splitter.setChildrenCollapsible(False)
        self.editor_splitter.addWidget(self.tabs)
        self.editor_splitter.addWidget(preview_panel)
        self.editor_splitter.setStretchFactor(0, 3)
        self.editor_splitter.setStretchFactor(1, 2)
        self.editor_splitter.setSizes([850, 550])
        root.insertWidget(0, self.editor_splitter, 1)
        self.tabs.setCurrentIndex(0)

    def setup(self, atoms: Any, back: bool = False) -> None:
        super().setup(atoms, back=back)
        if hasattr(self, "cell_summary"):
            self._refresh_cell_summary()
            self._refresh_operation_availability(atoms)

    def _refresh_operation_availability(self, atoms: Any) -> None:
        # The Advanced page may produce a cluster from a periodic draft.
        # Periodic transformations and cell edits must then be disabled.
        is_cluster = "sprkkr_embedding" in getattr(atoms, "arrays", {})
        for button in (self.scale_button, self.repeat_button, self.rotate_button):
            button.setEnabled(not is_cluster)
            button.setToolTip(
                "Use the embedded-cluster editor for cluster geometry."
                if is_cluster else ""
            )
        for row in self.lattice_spinboxes:
            for spin in row:
                spin.setEnabled(not is_cluster)
        self.scroll.setEnabled(not is_cluster)
        self.site_notice.setVisible(is_cluster)
        if is_cluster:
            # Generic site validation is irrelevant for read-only cluster sites.
            # The dedicated embedding editor owns their validation.
            self.ok_btn.setEnabled(True)
        if is_cluster and self.tabs.currentIndex() == 0:
            self.tabs.setCurrentIndex(3)
        self.build_2d_button.setEnabled(
            self._allow_build_2d and self._recent_files is not None
            and bool(np.asarray(getattr(atoms, "pbc", ())).all())
            and not is_cluster
        )

    def _on_lattice_vector_changed(self, value: float) -> None:
        super()._on_lattice_vector_changed(value)
        if hasattr(self, "cell_summary"):
            self._refresh_cell_summary()

    def _refresh_cell_summary(self) -> None:
        cell = self._require_draft().cell
        lengths = np.linalg.norm(cell, axis=1)
        self.cell_summary.setText(
            "Cell lengths (Å):  "
            + "   ".join(
                f"{label} = {value:.5g}"
                for label, value in zip("abc", lengths)
            )
        )

    def _candidate(self) -> Any:
        # Never materialize an embedded cluster through the generic site editor:
        # that could rebuild its sites, losing host indexing and radial state.
        if "sprkkr_embedding" in getattr(self._working_atoms, "arrays", {}):
            return self._working_atoms
        draft = self._require_draft()
        if not draft.is_valid():
            raise ValueError("Complete or correct the invalid site occupations first.")
        if draft.is_unchanged():
            return self._working_atoms
        return draft.apply()

    def _accept_operation_result(self, result: Any, base: Any) -> None:
        if result is None:
            return  # Cancelled by the specialized editor.
        if result is base and base is self._working_atoms:
            return  # No operation, no unsaved edits.
        self._working_atoms = result
        self.setup(result)

    def _run_operation(self, operation, label: str) -> None:
        try:
            base = self._candidate()
            result = operation(base)
            self._accept_operation_result(result, base)
        except (ValueError, TypeError, NotImplementedError) as exc:
            QMessageBox.warning(self, label, str(exc))

    def _transform(self, operation) -> None:
        self._run_operation(
            lambda atoms: operation(atoms, parent=self),
            "Cannot Transform Structure",
        )

    def _build_two_d(self) -> None:
        from ase2sprkkr_gui.gui.dialogs.structures.build_2d import (
            select_build_2d_structure,
        )
        self._run_operation(
            lambda atoms: select_build_2d_structure(
                atoms, parent=self, recent_files=self._recent_files
            ),
            "Cannot Build 2D Structure",
        )

    def _edit_embedding(self) -> None:
        from ase2sprkkr_gui.ase.embedding import prepare_embedding_source
        from ase2sprkkr_gui.gui.dialogs.structures.embedding import (
            select_build_embedding_structure,
        )

        def edit(atoms):
            prepared = prepare_embedding_source(atoms)
            result = select_build_embedding_structure(prepared, parent=self)
            return None if result is prepared else result

        self._run_operation(edit, "Cannot Edit Embedded Cluster")

    def _on_ok(self) -> None:
        draft = self._require_draft()
        if ("sprkkr_embedding" not in getattr(self._working_atoms, "arrays", {})
                and not draft.is_valid()):
            return
        try:
            self._result = (
                None
                if self._working_atoms is self._original_atoms
                and draft.is_unchanged()
                else self._candidate()
            )
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Cannot Apply Structure", str(exc))
            return
        self.accept()


def select_structure_editor(
    atoms: Any,
    parent: QWidget | None = None,
    *,
    recent_files: Any = None,
    allow_build_2d: bool = False,
    allow_build_embedding: bool = False,
) -> Any | None:
    """Return a detached accepted structure, or None on cancel/no-op."""
    dialog = StructureEditorDialog(
        atoms,
        parent,
        recent_files=recent_files,
        allow_build_2d=allow_build_2d,
        allow_build_embedding=allow_build_embedding,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog._result
