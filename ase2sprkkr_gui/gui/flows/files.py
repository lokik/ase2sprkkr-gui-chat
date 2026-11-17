"""Qt file orchestration around the workspace document controller."""
from __future__ import annotations

import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from ase.io import read as ase_read
from ase.io import write as ase_write
from ase.io.formats import ioformats
from ase2sprkkr import InputParameters, Potential
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget

from ase2sprkkr_gui.gui.application.recent_files import RecentFiles, RecentKind
from ase2sprkkr_gui.gui.application.result_loading import (
    LoadedResult,
    PotentialVersionChanged,
    load_potential_atoms,
    load_result_file,
)
from ase2sprkkr_gui.gui.application.workspace_controller import ResultAdoption, WorkspaceController
from ase2sprkkr_gui.gui.flows.structure_candidates import (
    load_structure_candidate,
    structure_open_filter,
)
from ase2sprkkr_gui.gui.flows.structures import warn_if_structure_kind_is_unknown
from ase2sprkkr_gui.gui.misc.modal_task import run_modal_task
from ase2sprkkr_gui.gui.misc.structure_wait import document_change_applied


_POTENTIAL_SUFFIXES = {".pot", ".pot_new"}


def _companion_host_path(cluster_path: str | Path) -> Path:
    """GUI convention; native SPR-KKR binds files through input CONTROL fields."""
    path = Path(cluster_path)
    return path.with_name(f"{path.stem}.host.pot")


def _read_structure_file(path: Path):
    """Read a structure, keeping a POT file's potential authoritative in atoms."""
    if path.suffix.lower() in _POTENTIAL_SUFFIXES and path.exists():
        return load_potential_atoms(path)
    return ase_read(path)


def _show_potential_warning(adoption: ResultAdoption, parent: QWidget) -> None:
    if adoption.potential_error is None:
        return
    QMessageBox.warning(
        parent,
        "Potential Load Warning",
        "The result was loaded, but its potential could not be loaded:\n"
        f"{adoption.potential_error}",
    )


def _show_input_parameters_warning(adoption: ResultAdoption, parent: QWidget) -> None:
    if adoption.input_parameters_error is None:
        return
    QMessageBox.warning(
        parent,
        "Input Parameters Load Warning",
        "The result was loaded, but its input parameters could not be loaded:\n"
        f"{adoption.input_parameters_error}",
    )


def choose_and_load_structure(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    file_path, _ = QFileDialog.getOpenFileName(
        parent,
        "Load Structure File",
        "",
        "Embedded cluster (*.clu *.pot);;" + structure_open_filter(),
    )
    return bool(file_path and load_structure(controller, recent_files, file_path, parent))


def load_structure(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    file_path: str | Path,
    parent: QWidget,
) -> bool:
    structure_generation = controller.structure_generation
    directory_generation = controller.directory_generation
    path = Path(file_path).resolve()
    try:
        if path.suffix.lower() in {*_POTENTIAL_SUFFIXES, ".clu"} and Potential.is_cluster_file(path):
            return load_embedded_cluster(controller, recent_files, path, parent)
    except OSError as exc:
        QMessageBox.critical(parent, "Load Error", str(exc))
        return False
    candidate = load_structure_candidate(path, parent, reader=_read_structure_file)
    if candidate is None:
        return False
    atoms = candidate.atoms

    potential_path = str(path) if path.suffix.lower() in _POTENTIAL_SUFFIXES else None
    change = controller.replace_structure(
        atoms,
        potential_path=potential_path,
        directory=str(path.parent),
        expected_structure_generation=structure_generation,
        expected_directory_generation=directory_generation,
    )
    if not document_change_applied(parent, change):
        return False
    controller.mark_structure_saved(controller.structure_generation)
    warn_if_structure_kind_is_unknown(atoms, parent)
    recent_files.remember("structure", file_path)
    return True


def _current_host_potential(controller: WorkspaceController):
    """Use only a genuine periodic host Potential, not a ClusterPotential."""
    from ase2sprkkr import Potential

    atoms = controller.atoms
    if atoms is None:
        return None
    embedded_host = getattr(atoms, "embedding_host", None)
    if isinstance(embedded_host, Potential):
        return embedded_host
    checker = getattr(atoms, "has_potential", None)
    if callable(checker) and checker():
        potential = atoms.potential
        return potential if isinstance(potential, Potential) else None
    return None


def load_embedded_cluster(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    file_path: str | Path,
    parent: QWidget,
    *,
    host_path: str | Path | None = None,
) -> bool:
    """Load CLUPOT with the existing or an explicitly selected host POTFIL.

    The cluster file is not self-contained. Keep the host Potential as a
    shared reference in the resulting embedded Atoms and do not overwrite the
    current workspace if either file cannot be parsed.
    """
    structure_generation = controller.structure_generation
    directory_generation = controller.directory_generation
    path = Path(file_path).expanduser().resolve()
    if host_path is None and _companion_host_path(path).is_file():
        host_path = _companion_host_path(path)
    host = None if host_path is not None else _current_host_potential(controller)
    if host is None:
        if host_path is None:
            selected, _ = QFileDialog.getOpenFileName(
                parent, "Select Host SPR-KKR Potential",
                str(path.parent), "SPR-KKR host POTFIL (*.pot *.pot_new);;All Files (*)",
            )
            if not selected:
                return False
            host_path = selected
        try:
            from ase2sprkkr import Potential
            host = run_modal_task(
                parent,
                lambda: Potential.from_file(str(Path(host_path).expanduser().resolve())),
                title="Loading Host Potential",
                label="Reading host POTFIL…",
            )
        except Exception as exc:  # noqa: BLE001 - parser errors presented to the user
            QMessageBox.critical(parent, "Host Potential Load Error", str(exc))
            return False
    try:
        from ase2sprkkr.potentials.cluster_potentials import ClusterPotential
        def read_cluster():
            potential = ClusterPotential.from_file(path, host=host)
            return potential.atoms, potential.host_geometry_warning()

        cluster, geometry_warning = run_modal_task(
            parent,
            read_cluster,
            title="Loading Embedded Cluster",
            label="Reading CLUPOT…",
        )
        if cluster is None:
            raise ValueError("Cluster potential did not contain embedded atoms")
    except Exception as exc:  # noqa: BLE001 - native file validation boundary
        QMessageBox.critical(parent, "Embedded Cluster Load Error", str(exc))
        return False

    if geometry_warning:
        # CLUPOT contains no host-cell identity; an approximate geometrical
        # mismatch is advisory, not grounds for rejecting an explicit choice.
        QMessageBox.warning(
            parent, "Host Potential May Not Match",
            geometry_warning + "\n\nCheck the host POTFIL before calculating.",
        )

    change = controller.replace_structure(
        cluster,
        directory=str(path.parent),
        expected_structure_generation=structure_generation,
        expected_directory_generation=directory_generation,
    )
    if not document_change_applied(parent, change):
        return False
    controller.mark_structure_saved(controller.structure_generation)
    recent_files.remember("structure", path)
    return True


def choose_and_load_input_parameters(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    file_path, _ = QFileDialog.getOpenFileName(
        parent,
        "Load Input File",
        "",
        "SPRKKR Input Files (*.inp *.in *.txt);;All Files (*)",
    )
    return bool(
        file_path
        and load_input_parameters(controller, recent_files, file_path, parent)
    )


def load_input_parameters(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    file_path: str | Path,
    parent: QWidget,
) -> bool:
    input_generation = controller.input_parameters_generation
    directory_generation = controller.directory_generation
    path = Path(file_path).resolve()
    try:
        parameters = run_modal_task(
            parent,
            lambda: InputParameters.from_file(path),
            title="Loading Input",
            label="Reading input parameters…",
        )
    except Exception as exc:  # noqa: BLE001 - parser errors are presented
        QMessageBox.critical(
            parent,
            "Load Error",
            f"Failed to load input parameters:\n{exc}",
        )
        return False

    change = controller.replace_input_parameters(
        parameters,
        directory=str(path.parent),
        expected_input_parameters_generation=input_generation,
        expected_directory_generation=directory_generation,
    )
    if not document_change_applied(parent, change):
        return False
    controller.mark_input_parameters_saved(controller.input_parameters_generation)
    recent_files.remember("input", file_path)
    return True


def choose_and_load_output(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    file_path, _ = QFileDialog.getOpenFileName(
        parent,
        "Load SPRKKR Output File",
        "",
        "SPRKKR Output Files (*.out *.log *.txt);;All Files (*)",
    )
    return bool(file_path and load_output(controller, recent_files, file_path, parent))


def load_output_directory(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    directory: str | Path,
    parent: QWidget,
) -> tuple[Path, ...]:
    """Import recognized outputs from a directory into result history."""
    folder = Path(directory).expanduser().resolve()
    if not folder.is_dir():
        raise NotADirectoryError(folder)
    candidates = sorted(
        (p for p in folder.iterdir()
         if p.is_file() and p.suffix.lower() in {".out", ".log", ".txt"}),
        key=lambda p: p.name.casefold(),
    )
    if not candidates:
        QMessageBox.information(
            parent, "No Output Files", f"No output files found in:\n{folder}"
        )
        return ()

    def read_all():
        loaded, failures = [], []
        for path in candidates:
            try:
                loaded.append((path, load_result_file(path)))
            except Exception as exc:  # noqa: BLE001 - unrelated logs may exist
                failures.append((path.name, str(exc)))
        return loaded, failures

    try:
        loaded, failures = run_modal_task(
            parent, read_all,
            title="Importing Results",
            label="Reading SPR-KKR outputs…",
        )
    except Exception as exc:  # noqa: BLE001 - backend errors
        QMessageBox.critical(parent, "Directory Import Failed", str(exc))
        return ()

    imported = []
    for index, (path, result) in enumerate(loaded):
        controller.adopt_external_result(
            result, select_result=index == len(loaded) - 1
        )
        recent_files.remember("output", path)
        imported.append(path)

    if failures:
        summary = "\n".join(
            f"{name}: {reason}" for name, reason in failures[:8]
        )
        QMessageBox.warning(
            parent, "Output Directory",
            f"Imported {len(imported)} result(s). "
            f"Skipped {len(failures)} file(s):\n{summary}",
        )
    elif imported:
        QMessageBox.information(
            parent, "Output Directory",
            f"Imported {len(imported)} result(s) from:\n{folder}",
        )
    return tuple(imported)


def choose_and_load_output_directory(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> tuple[Path, ...]:
    """Choose and import an existing directory of calculation outputs."""
    folder = QFileDialog.getExistingDirectory(
        parent, "Open Output Directory", controller.directory or ""
    )
    return (
        load_output_directory(controller, recent_files, folder, parent)
        if folder else ()
    )


def _read_output(file_path: str | Path, parent: QWidget) -> LoadedResult | None:
    try:
        return run_modal_task(
            parent,
            lambda: load_result_file(file_path),
            title="Loading Result",
            label="Parsing SPR-KKR output…",
        )
    except Exception as exc:  # noqa: BLE001 - backend readers vary
        QMessageBox.critical(
            parent,
            "Load Error",
            f"Failed to load SPR-KKR output:\n{exc}",
        )
        return None


def _finish_output_load(
    adoption: ResultAdoption,
    recent_files: RecentFiles,
    file_path: str | Path,
    parent: QWidget,
) -> None:
    recent_files.remember("output", file_path)
    _show_potential_warning(adoption, parent)
    _show_input_parameters_warning(adoption, parent)


def load_output(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    file_path: str | Path,
    parent: QWidget,
) -> bool:
    """Import an output into result history without changing the workspace."""
    loaded = _read_output(file_path, parent)
    if loaded is None:
        return False
    adoption = controller.adopt_external_result(loaded, select_result=True)
    _finish_output_load(adoption, recent_files, file_path, parent)
    return True


def continue_output(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    file_path: str | Path,
    parent: QWidget,
) -> bool:
    """Import or reuse an output and make its reusable calculation state active."""
    structure_generation = controller.structure_generation
    directory_generation = controller.directory_generation
    loaded = _read_output(file_path, parent)
    if loaded is None:
        return False

    adoption = controller.adopt_external_result(loaded, select_result=True)
    record = adoption.record
    if record is None:
        return False

    state = controller.result_store.state_for_result(record)
    if state is None:
        QMessageBox.warning(
            parent,
            "Cannot Continue Calculation",
            "The selected result has no reusable calculation state.",
        )
        _finish_output_load(adoption, recent_files, file_path, parent)
        return False

    if controller.state_id != state.id:
        try:
            change = controller.use_result_state(
                record.id,
                expected_structure_generation=structure_generation,
            )
        except (ValueError, PotentialVersionChanged) as exc:
            QMessageBox.warning(
                parent,
                "Cannot Continue Calculation",
                f"The selected result cannot be restored:\n{exc}",
            )
            _finish_output_load(adoption, recent_files, file_path, parent)
            return False
        if not document_change_applied(parent, change):
            _finish_output_load(adoption, recent_files, file_path, parent)
            return False

    if loaded.directory is not None and not document_change_applied(
        parent,
        controller.change_directory(
            loaded.directory,
            expected_directory_generation=directory_generation,
        ),
    ):
        _finish_output_load(adoption, recent_files, file_path, parent)
        return False
    # Continuing from an existing output restores a file-backed structure,
    # not a new unsaved edit. Only commit its saved baseline once the entire
    # continuation (including the working directory) succeeded.
    controller.mark_structure_saved(controller.structure_generation)
    _finish_output_load(adoption, recent_files, file_path, parent)
    return True


def save_structure(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    atoms = controller.atoms
    if atoms is None:
        if controller.pending_embedded_cluster is not None:
            return save_orphan_cluster(controller, recent_files, parent)
        return False
    # Never fall back to a generic ASE writer for an embedded structure:
    # it cannot preserve the original-site/cell mapping and host reference.
    # save_embedded_cluster diagnoses a missing host rather than losing data.
    if "sprkkr_embedding" in getattr(atoms, "arrays", {}):
        return save_embedded_cluster(controller, recent_files, parent)
    generation = controller.structure_generation
    formats = {
        " ".join(f"*.{ext}" for ext in file_format.extensions): file_format.name
        for file_format in ioformats.values()
        if file_format.extensions
    }
    formats["*"] = "All Files"
    file_path, selected_filter = QFileDialog.getSaveFileName(
        parent,
        "Save Structure File",
        "",
        ";;".join(f"{name} ({patterns})" for patterns, name in formats.items()),
    )
    if not file_path:
        return False
    match = re.search(r"\*\.(\w+)", selected_filter)
    if match and not file_path.lower().endswith(f".{match.group(1)}"):
        file_path += f".{match.group(1)}"

    path = Path(file_path).resolve()
    try:
        ase_write(path, atoms)
    except Exception as exc:  # noqa: BLE001 - backend writers vary
        QMessageBox.critical(
            parent, "Save Error", f"Failed to save structure:\n{exc}"
        )
        return False
    recent_files.remember("structure", file_path)
    controller.mark_structure_saved(generation)
    return True


def _save_cluster_only(potential, controller, recent_files, parent) -> bool:
    """Save an already parsed native CLUPOT when its host cannot be supplied."""
    answer = QMessageBox.question(
        parent,
        "Host Potential Missing",
        "Host potential of embedded structure is missing. "
        "Should I save only the embedded potential?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    file_path, _ = QFileDialog.getSaveFileName(
        parent, "Save Embedded Potential (without Host)", "",
        "SPR-KKR embedded potential (*.clu *.pot);;All Files (*)",
    )
    if not file_path:
        return False
    path = Path(file_path).expanduser().resolve()
    if not path.suffix:
        path = path.with_suffix(".clu")
    try:
        potential.save_to_file(path)
    except Exception as exc:  # noqa: BLE001 - native writer is external
        QMessageBox.critical(parent, "Save Embedded Potential Failed", str(exc))
        return False
    recent_files.remember("structure", path)
    if controller.atoms is not None:
        controller.mark_structure_saved(controller.structure_generation)
    return True


def save_orphan_cluster(controller, recent_files, parent) -> bool:
    """Save a loaded CLUPOT that has not yet been associated with its host."""
    potential = controller.pending_embedded_cluster
    if potential is None:
        return False
    if not _save_cluster_only(potential, controller, recent_files, parent):
        return False
    # No atoms were fabricated, and the pending CLUPOT remains available
    # for later attachment.
    return True


def save_embedded_cluster(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    """Save the native cluster and a companion host potential.

    Even a cluster constructed before host SCF can be saved: a START host
    POTFIL is generated from its geometry. Both files are staged before either
    is moved to its requested destination. The cluster remains attached to
    its original host object in the workspace.
    """
    atoms = controller.atoms
    source_host = getattr(atoms, "embedding_host", None)
    if source_host is None:
        checker = getattr(atoms, "has_potential", None)
        native = atoms.potential if callable(checker) and checker() else None
        from ase2sprkkr.potentials.cluster_potentials import ClusterPotential
        if isinstance(native, ClusterPotential):
            return _save_cluster_only(native, controller, recent_files, parent)
        QMessageBox.warning(
            parent, "Cannot Save Embedded Cluster",
            "Host potential of embedded structure is missing. The cluster "
            "also has no native CLUPOT that can be saved independently. "
            "Load or create its matching host potential first.",
        )
        return False
    generation = controller.structure_generation
    file_path, _ = QFileDialog.getSaveFileName(
        parent,
        "Save Embedded Cluster and Host Potential",
        "",
        "SPR-KKR cluster potential (*.clu *.pot);;All Files (*)",
    )
    if not file_path:
        return False
    path = Path(file_path).expanduser().resolve()
    if not path.suffix:
        path = path.with_suffix(".clu")
    host_path = _companion_host_path(path)
    if host_path == path:
        raise ValueError("Host and cluster potential paths must be distinct")
    if host_path.exists():
        response = QMessageBox.question(
            parent, "Overwrite Host Potential?",
            f"Saving the cluster also writes its host potential:\n{host_path}\n\n"
            "Overwrite the existing host potential?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response != QMessageBox.StandardButton.Yes:
            return False
    try:
        from ase2sprkkr.potentials.cluster_potentials import ClusterPotential

        host_potential = (
            source_host if isinstance(source_host, Potential)
            else Potential.from_atoms(source_host)
        )
        if host_potential is source_host:
            cluster_atoms = atoms
        else:
            # Do not replace the host of the authoritative workspace atoms.
            cluster_atoms = atoms.copy()
            cluster_atoms.embedding_host = host_potential
        if (host_potential is source_host and atoms.has_potential()
                and isinstance(atoms.potential, ClusterPotential)):
            # Retain converged radial data and original native sections.
            potential = atoms.potential
        else:
            potential = ClusterPotential.from_atoms(cluster_atoms)

        with TemporaryDirectory(dir=path.parent, prefix=".cluster-save-") as staged:
            staging_dir = Path(staged)
            staged_host = staging_dir / host_path.name
            staged_cluster = staging_dir / path.name
            host_potential.save_to_file(staged_host)
            potential.save_to_file(staged_cluster)
            # Stage both before replacing either. A crash between these two
            # renames can still leave one newer file; save is not a filesystem
            # transaction across distinct paths.
            os.replace(staged_host, host_path)
            os.replace(staged_cluster, path)
    except Exception as exc:  # noqa: BLE001 - native writers may raise varied errors
        QMessageBox.critical(
            parent, "Save Embedded Cluster Failed",
            f"Could not save the cluster and its host potential:\n{exc}",
        )
        return False
    controller.mark_structure_saved(generation)
    recent_files.remember("structure", path)
    return True


def ensure_embedded_host_potential(
    controller: WorkspaceController,
    parent: QWidget,
) -> bool:
    """Attach a matching host POTFIL before a Guided embedded calculation.

    The atom-site identities are relative to the original host, so accepting
    an unrelated host would silently calculate the wrong impurity geometry.
    """
    if controller.can_run_embedding():
        return True
    cluster = controller.atoms
    old_host = getattr(cluster, "embedding_host", None)
    if old_host is None:
        QMessageBox.warning(parent, "Host Potential Required",
                            "The embedded structure has no host reference.")
        return False

    directory = controller.directory or ""
    path, _ = QFileDialog.getOpenFileName(
        parent, "Select Host SPR-KKR Potential", directory,
        "SPR-KKR host POTFIL (*.pot *.pot_new);;All Files (*)",
    )
    if not path:
        return False
    try:
        host_potential = run_modal_task(
            parent, lambda: Potential.from_file(path),
            title="Loading Host Potential", label="Reading host POTFIL…",
        )
        original_atoms = getattr(old_host, "atoms", old_host)
        replacement_atoms = host_potential.atoms
        if (len(original_atoms) != len(replacement_atoms)
                or not np.allclose(original_atoms.cell, replacement_atoms.cell,
                                   atol=1e-3, rtol=0)
                or not np.allclose(original_atoms.positions,
                                   replacement_atoms.positions, atol=1e-3, rtol=0)):
            raise ValueError(
                "The selected host POTFIL has different site ordering, "
                "positions or lattice vectors. Select the matching host potential."
            )
        generation = controller.structure_generation

        def attach(candidate):
            candidate.embedding_host = host_potential
            return candidate

        change = controller.apply_structure_edit(
            attach, expected_structure_generation=generation,
        )
        return bool(change is not None and document_change_applied(parent, change))
    except Exception as exc:  # noqa: BLE001 - invalid host file is a user error
        QMessageBox.warning(parent, "Cannot Attach Host Potential", str(exc))
        return False


def save_input_parameters(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    parameters = controller.input_parameters
    if parameters is None:
        return False
    generation = controller.input_parameters_generation
    file_path, _ = QFileDialog.getSaveFileName(
        parent,
        "Save Input File",
        "",
        "SPRKKR Input Files (*.inp *.in *.txt);;All Files (*)",
    )
    if not file_path:
        return False
    try:
        parameters.to_file(Path(file_path).resolve())
    except Exception as exc:  # noqa: BLE001 - backend writers vary
        QMessageBox.critical(
            parent,
            "Save Error",
            f"Failed to save input parameters:\n{exc}",
        )
        return False
    recent_files.remember("input", file_path)
    controller.mark_input_parameters_saved(generation)
    return True


def open_recent(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    kind: RecentKind,
    file_path: str | Path,
    parent: QWidget,
) -> bool:
    if not Path(file_path).exists():
        QMessageBox.warning(parent, "Missing File", f"File not found:\n{file_path}")
        recent_files.forget(kind, file_path)
        return False
    loaders = {
        "structure": load_structure,
        "input": load_input_parameters,
        "output": load_output,
    }
    return loaders[kind](controller, recent_files, file_path, parent)


def continue_recent(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    kind: RecentKind,
    file_path: str | Path,
    parent: QWidget,
) -> bool:
    """Continue from a recent item instead of merely reopening an output."""
    if not Path(file_path).exists():
        QMessageBox.warning(parent, "Missing File", f"File not found:\n{file_path}")
        recent_files.forget(kind, file_path)
        return False
    if kind == "output":
        return continue_output(controller, recent_files, file_path, parent)
    return open_recent(controller, recent_files, kind, file_path, parent)
