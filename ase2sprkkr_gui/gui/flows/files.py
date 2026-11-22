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
from ase2sprkkr import InputParameters, PeriodicPotential, Potential
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget

from ase2sprkkr_gui.ase.copy import copy_for_edit
from ase2sprkkr_gui.gui.application.recent_files import RecentFiles, RecentKind
from ase2sprkkr_gui.gui.application.result_loading import (
    LoadedResult,
    ResultImportCache,
    disambiguate_scf_potentials,
    PotentialVersionChanged,
    load_potential_atoms,
    load_result_file,
    load_companion_scf_result,
)
from ase2sprkkr_gui.gui.application.workspace_controller import DocumentChange, ResultAdoption, WorkspaceController
from ase2sprkkr_gui.gui.flows.input_parameters import save_input_parameters_to_path
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
        # The caller has already ruled out a native CLUPOT. Avoid probing
        # the same POTFIL a second time in load_potential_atoms().
        return PeriodicPotential.from_file(
            str(path), check_file_unchanged=True,
        ).atoms
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
    change = None

    def apply_loaded_structure(candidate):
        nonlocal change
        change = controller.replace_structure(
            candidate.atoms,
            potential_path=str(path) if path.suffix.lower() in _POTENTIAL_SUFFIXES else None,
            directory=str(path.parent),
            expected_structure_generation=structure_generation,
            expected_directory_generation=directory_generation,
        )
        if change is DocumentChange.APPLIED:
            controller.mark_structure_saved(controller.structure_generation)
            recent_files.remember("structure", file_path)

    candidate = load_structure_candidate(
        path, parent, reader=_read_structure_file, on_loaded=apply_loaded_structure,
    )
    if candidate is None:
        return False
    if not document_change_applied(parent, change):
        return False
    warn_if_structure_kind_is_unknown(candidate.atoms, parent)
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
    host_potential=None,
) -> bool:
    """Load native CLUPOT read-only, or attach a supplied host for editing.

    Missing host is valid for an existing *file*, but not for an editable
    structure. Only convert to SPRKKRAtoms once a host Potential is available.
    """
    structure_generation = controller.structure_generation
    directory_generation = controller.directory_generation
    path = Path(file_path).expanduser().resolve()
    if host_path is None and host_potential is None and _companion_host_path(path).is_file():
        host_path = _companion_host_path(path)
    host = (host_potential if host_potential is not None
            else None if host_path is not None else _current_host_potential(controller))
    if host is None and host_path is None:
        # A hostless native CLUPOT can be parsed, displayed and retained,
        # but must never be converted into editable embedded Atoms.
        try:
            from ase2sprkkr import ClusterPotential

            potential = run_modal_task(
                parent, lambda: ClusterPotential.from_file(path),
                title="Loading Embedded Potential", label="Reading CLUPOT…",
            )
        except Exception as exc:  # noqa: BLE001 - native file validation
            QMessageBox.critical(parent, "Embedded Cluster Load Error", str(exc))
            return False
        change = controller.load_unresolved_cluster(
            potential, path,
            expected_structure_generation=structure_generation,
            expected_directory_generation=directory_generation,
        )
        if not document_change_applied(parent, change):
            return False
        recent_files.remember("structure", path)
        QMessageBox.information(
            parent, "Embedded Potential Loaded (Read-Only)",
            "CLUPOT was loaded without its host POTFIL. The file is read-only "
            "and does not require saving on exit. Use Load Host Potential or "
            "Create Host Potential to complete the structure.",
        )
        return True
    if host is None:
        try:
            host = run_modal_task(
                parent,
                lambda: Potential.from_file(str(Path(host_path).expanduser().resolve())),
                title="Loading Host Potential",
                label="Reading host POTFIL…",
            )
        except Exception as exc:  # noqa: BLE001 - native file validation
            QMessageBox.critical(parent, "Host Potential Load Error", str(exc))
            return False
    try:
        from ase2sprkkr import ClusterPotential
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
        # The native file does not carry complete host-cell identity, so a
        # geometry discrepancy is an advisory check. Do not accept a potentially
        # incorrect host *silently*, especially when the host was just created.
        decision = QMessageBox.question(
            parent, "Host Potential May Not Match",
            geometry_warning + "\n\nUse this host potential anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if decision != QMessageBox.StandardButton.Yes:
            return False

    # A freshly parsed CLUPOT has no GUI centre. Assign its geometrical
    # centre on load; metadata stays on these cluster atoms, not on the host.
    if "sprkkr_embedding" in getattr(cluster, "arrays", {}):
        from ase2sprkkr_gui.ase.embedding import reset_cluster_center
        reset_cluster_center(cluster)

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
    def apply_loaded_parameters(parameters):
        change = controller.replace_input_parameters(
            parameters,
            directory=str(path.parent),
            expected_input_parameters_generation=input_generation,
            expected_directory_generation=directory_generation,
        )
        if change is DocumentChange.APPLIED:
            controller.mark_input_parameters_saved(controller.input_parameters_generation)
            # RecentFiles.changed rebuilds workflow actions synchronously.
            recent_files.remember("input", path)
        return change

    try:
        change = run_modal_task(
            parent,
            lambda: InputParameters.from_file(path),
            title="Loading Input",
            label="Loading input parameters…",
            on_success=apply_loaded_parameters,
        )
    except Exception as exc:  # noqa: BLE001 - parser errors are presented
        QMessageBox.critical(
            parent,
            "Load Error",
            f"Failed to load input parameters:\n{exc}",
        )
        return False

    return document_change_applied(parent, change)


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


def _read_output_batch(
    paths: tuple[Path, ...] | list[Path], *, cache: ResultImportCache | None = None,
):
    """One parse/cache/SCF-association pipeline for recent and directory imports."""
    if cache is None:
        cache = ResultImportCache()
    loaded, failures = [], []
    for path in paths:
        try:
            loaded.append((path, load_result_file(path, cache=cache)))
        except Exception as exc:  # independent outputs need not block the batch
            failures.append((path.name, str(exc)))
    loaded, ambiguous = disambiguate_scf_potentials(loaded, cache=cache)
    return loaded, failures, ambiguous


def load_all_recent_outputs(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> tuple[Path, ...]:
    """Import current recent results in one batch, preserving the workspace.

    Parse off the GUI thread and adopt each result on the GUI thread.
    Process oldest first, so the newest successful result is selected.
    """
    paths = tuple(Path(path) for path in reversed(recent_files.paths("output")))
    if not paths:
        return ()

    def read_all():
        return _read_output_batch(paths)

    def publish(read_results):
        loaded, failures, ambiguous = read_results
        imported: list[Path] = []
        potential_entries: list[tuple[RecentKind, Path]] = []
        selected_id = None
        with controller.batch_external_results():
            for path, result in loaded:
                try:
                    adoption = controller.adopt_external_result(result)
                    if adoption.record is not None:
                        selected_id = adoption.record.id
                    if adoption.potential_path is not None and adoption.potential_error is None:
                        potential_entries.append(("structure", adoption.potential_path))
                    imported.append(path)
                except Exception as exc:  # noqa: BLE001 - independent results
                    failures.append((path.name, str(exc)))

        # One result-tree rebuild, followed by one recent-files update.
        recent_files.remember_many(potential_entries, preserve_last_kind=True)
        if selected_id is not None:
            controller.resultSelectionRequested.emit(selected_id)
        return imported, failures, ambiguous

    try:
        imported, failures, ambiguous = run_modal_task(
            parent, read_all,
            title="Loading Recent Results",
            label="Reading recent SPR-KKR results…",
            on_success=publish,
        )
    except Exception as exc:  # noqa: BLE001 - reader or presentation error
        QMessageBox.critical(parent, "Loading Recent Results Failed", str(exc))
        return ()

    if failures or ambiguous:
        messages = [f"Imported {len(imported)} result(s)."]
        if failures:
            summary = "\n".join(f"{name}: {reason}" for name, reason in failures[:8])
            messages.append(f"Skipped {len(failures)} file(s):\n{summary}")
        if ambiguous:
            messages.append(
                "Multiple SCF outputs reference the same potential. "
                "Only the uniquely newest .out (by modification time) "
                "keeps the potential; older or tied outputs have no reusable "
                "structure:\n"
                + "\n".join(ambiguous[:8])
            )
        QMessageBox.warning(parent, "Loading Recent Results", "\n\n".join(messages))
    return tuple(imported)


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
    # Use the backend's directory index for both discovery and parsing.
    # The cache is handed to the worker only after this GUI-side selection.
    cache = ResultImportCache()
    candidates = cache.loader.find_outputs(folder)
    if not candidates:
        QMessageBox.information(
            parent, "No Output Files", f"No output files found in:\n{folder}"
        )
        return ()

    def read_all():
        return _read_output_batch(candidates, cache=cache)

    def publish(read_results):
        loaded, failures, ambiguous = read_results
        imported = []
        entries: list[tuple[RecentKind, Path]] = []
        with controller.batch_external_results():
            for index, (path, result) in enumerate(loaded):
                try:
                    adoption = controller.adopt_external_result(
                        result, select_result=index == len(loaded) - 1
                    )
                except Exception as exc:  # noqa: BLE001 - independent outputs
                    failures.append((path.name, str(exc)))
                    continue
                if adoption.potential_path is not None and adoption.potential_error is None:
                    entries.append(("structure", adoption.potential_path))
                entries.append(("output", path))
                imported.append(path)
        recent_files.remember_many(entries)
        return imported, failures, ambiguous

    try:
        imported, failures, ambiguous = run_modal_task(
            parent, read_all,
            title="Importing Results",
            label="Reading SPR-KKR outputs…",
            on_success=publish,
        )
    except Exception as exc:  # noqa: BLE001 - backend or presentation errors
        QMessageBox.critical(parent, "Directory Import Failed", str(exc))
        return ()

    if failures or ambiguous:
        messages = [f"Imported {len(imported)} result(s)."]
        if failures:
            summary = "\n".join(
                f"{name}: {reason}" for name, reason in failures[:8]
            )
            messages.append(f"Skipped {len(failures)} file(s):\n{summary}")
        if ambiguous:
            messages.append(
                "Multiple SCF outputs reference the same potential. "
                "Only the uniquely newest .out (by modification time) "
                "keeps it. Older or tied outputs were imported without "
                "a reusable structure:\n" + "\n".join(ambiguous[:8])
            )
        QMessageBox.warning(parent, "Output Directory", "\n\n".join(messages))
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
    # The native directory picker hides files. Use the Qt dialog so users can
    # inspect output files before choosing which directory to import.
    dialog = QFileDialog(parent, "Open Output Directory", controller.directory or "")
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly, False)
    dialog.setNameFilters([
        "SPR-KKR Output Files (*.out)",
        "All Files (*)",
    ])
    dialog.setViewMode(QFileDialog.ViewMode.Detail)
    if not dialog.exec():
        return ()
    folders = dialog.selectedFiles()
    return (
        load_output_directory(controller, recent_files, folders[0], parent)
        if folders else ()
    )


def _cached_output(
    controller: WorkspaceController, file_path: str | Path,
) -> LoadedResult | None:
    """Reuse an imported file only if its output and structural source remain current."""
    record = controller.result_store.result_for_external_source(file_path)
    if record is None:
        return None
    # Scientific result files can change without the stdout changing.
    # Do not reuse parsed values when any captured artifact is now stale.
    if any(not version.is_current() for version in record.result_files.values()):
        return None
    if record.potential_path is not None:
        state = controller.result_store.state_for_result(record)
        if (
            state is None or state.potential_version is None
            or not state.potential_version.is_current()
            or any(not version.is_current() for version in state.source_dependencies)
        ):
            return None
    return LoadedResult(
        result=record.result,
        output_path=record.output_path,
        potential_path=record.potential_path,
        atoms=None,  # The already-recorded structure state is authoritative.
        potential_error=None,
        directory=record.directory,
        input_parameters=record.input_parameters,
        persistent_ref=record.persistent_ref,
    )


def _existing_scf_potentials(controller: WorkspaceController) -> frozenset[Path]:
    """Snapshot GUI history before starting a worker; never read the store from it."""
    return frozenset(
        record.potential_path for record in controller.result_store.results
        if record.task_name == "scf" and record.potential_path is not None
    )


def _read_output(
    file_path: str | Path,
    parent: QWidget,
    *,
    known_scf_potentials: frozenset[Path],
    on_success,
    cached_loaded: LoadedResult | None = None,
):
    """Read result and optional SCF off-thread, then publish before Working closes."""
    def read():
        if cached_loaded is not None:
            return cached_loaded, None
        # One short-lived loader for the selected result and its companion.
        # Parsed native potentials are reused without storing a batch cache in
        # either TaskResult or the GUI's persistent result history.
        cache = ResultImportCache()
        loaded = load_result_file(file_path, cache=cache)
        companion = None
        if loaded.potential_path not in known_scf_potentials:
            try:
                companion = load_companion_scf_result(loaded, cache=cache)
            except Exception:  # noqa: BLE001 - companion history is best effort
                pass
        return loaded, companion

    try:
        return run_modal_task(
            parent, read,
            title="Loading Result",
            label="Parsing SPR-KKR output…",
            on_success=lambda pair: on_success(*pair),
        )
    except Exception as exc:  # noqa: BLE001 - parser and adoption errors
        QMessageBox.critical(
            parent,
            "Load Error",
            f"Failed to load SPR-KKR output:\n{exc}",
        )
        return None


def _remember_output_load(
    adoption: ResultAdoption, recent_files: RecentFiles, file_path: str | Path,
) -> None:
    recent_files.remember_result(
        file_path,
        potential_path=(
            adoption.potential_path if adoption.potential_error is None else None
        ),
    )


def _show_output_warnings(
    adoption: ResultAdoption,
    parent: QWidget,
    *,
    show_potential_warning: bool = True,
) -> None:
    if show_potential_warning:
        _show_potential_warning(adoption, parent)
    _show_input_parameters_warning(adoption, parent)


def _adopt_companion_scf(
    controller: WorkspaceController, companion: LoadedResult | None,
) -> None:
    """Adopt companion history on the GUI thread, at most once, without failing load."""
    if companion is None:
        return
    try:
        if any(
            record.task_name == "scf" and record.potential_path == companion.potential_path
            for record in controller.result_store.results
        ):
            return
        controller.adopt_external_result(companion)
    except Exception:  # noqa: BLE001 - optional import never blocks primary result
        pass


def load_output(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    file_path: str | Path,
    parent: QWidget,
) -> bool:
    """Import an output into result history without changing the workspace."""
    def publish(loaded: LoadedResult, companion: LoadedResult | None):
        _adopt_companion_scf(controller, companion)
        adoption = controller.adopt_external_result(loaded, select_result=True)
        _remember_output_load(adoption, recent_files, file_path)
        return adoption

    adoption = _read_output(
        file_path, parent,
        known_scf_potentials=_existing_scf_potentials(controller),
        on_success=publish,
        cached_loaded=_cached_output(controller, file_path),
    )
    if adoption is None:
        return False
    _show_output_warnings(adoption, parent)
    return True


def continue_output(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    file_path: str | Path,
    parent: QWidget,
) -> bool:
    """Restore an existing output; finish workspace changes under Working."""
    structure_generation = controller.structure_generation
    directory_generation = controller.directory_generation

    def publish(loaded: LoadedResult, companion: LoadedResult | None):
        _adopt_companion_scf(controller, companion)
        adoption = controller.adopt_external_result(loaded, select_result=True)
        record = adoption.record
        if record is None:
            return adoption, False, ("missing_record", None)

        state = controller.result_store.state_for_result(record)
        if state is None:
            _remember_output_load(adoption, recent_files, file_path)
            return adoption, False, ("missing_state", None)

        if controller.state_id != state.id:
            try:
                change = controller.use_result_state(
                    record.id,
                    expected_structure_generation=structure_generation,
                    reusable_loaded=loaded,
                )
            except (ValueError, PotentialVersionChanged) as exc:
                _remember_output_load(adoption, recent_files, file_path)
                return adoption, False, ("restore_error", str(exc))
            if change is not DocumentChange.APPLIED:
                _remember_output_load(adoption, recent_files, file_path)
                return adoption, False, ("document_change", change)

        if loaded.directory is not None:
            change = controller.change_directory(
                loaded.directory,
                expected_directory_generation=directory_generation,
            )
            if change is not DocumentChange.APPLIED:
                _remember_output_load(adoption, recent_files, file_path)
                return adoption, False, ("document_change", change)

        # A restored file-backed structure is a saved baseline, not an edit.
        controller.mark_structure_saved(controller.structure_generation)
        _remember_output_load(adoption, recent_files, file_path)
        return adoption, True, None

    outcome = _read_output(
        file_path, parent,
        known_scf_potentials=_existing_scf_potentials(controller),
        on_success=publish,
        cached_loaded=_cached_output(controller, file_path),
    )
    if outcome is None:
        return False
    adoption, succeeded, problem = outcome
    if problem is not None:
        kind, detail = problem
        if kind == "missing_state":
            if adoption.potential_error is not None:
                message = (
                    "Cannot continue calculation because the potential could not be loaded:\n"
                    f"{adoption.potential_error}\n\n"
                    "The result was imported into history, but no reusable "
                    "calculation state could be reconstructed."
                )
            else:
                message = (
                    "The result was imported into history, but no reusable "
                    "calculation state could be reconstructed from it."
                )
            QMessageBox.warning(parent, "Cannot Continue Calculation", message)
            _show_output_warnings(adoption, parent, show_potential_warning=False)
            return False
        if kind == "restore_error":
            QMessageBox.warning(
                parent, "Cannot Continue Calculation",
                f"The selected result cannot be restored:\n{detail}",
            )
        elif kind == "document_change":
            document_change_applied(parent, detail)
        _show_output_warnings(adoption, parent)
        return False

    _show_output_warnings(adoption, parent)
    return succeeded


def save_structure(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    atoms = controller.atoms
    if atoms is None:
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
        QMessageBox.warning(
            parent, "Cannot Save Embedded Cluster",
            "Host of embedded structure is missing. This is an inconsistent "
            "embedding state, not a valid editable cluster. The structure "
            "cannot be saved without its host; reload the original data "
            "or rebuild the cluster.",
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
        from ase2sprkkr import ClusterPotential

        host_potential = (
            source_host if isinstance(source_host, Potential)
            else Potential.from_atoms(source_host)
        )
        if host_potential is source_host:
            cluster_atoms = atoms
        else:
            # Do not replace the host of the authoritative workspace atoms.
            cluster_atoms = copy_for_edit(atoms)
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
    *,
    host_potential=None,
) -> bool:
    """Attach a geometrically matching native host to an existing 0D cluster."""
    if controller.can_run_embedding():
        return True
    cluster = controller.atoms
    old_host = getattr(cluster, "embedding_host", None)
    if old_host is None:
        QMessageBox.warning(parent, "Host Potential Required",
                            "The embedded structure has no host geometry.")
        return False

    if host_potential is None:
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
        except Exception as exc:  # noqa: BLE001 - native user file
            QMessageBox.warning(parent, "Cannot Load Host Potential", str(exc))
            return False
    try:
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
    except Exception as exc:  # noqa: BLE001 - reject mismatched host
        QMessageBox.warning(parent, "Cannot Attach Host Potential", str(exc))
        return False


def load_host_potential(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    """Load a periodic host as a new structure, replacing the active cluster."""
    file_path, _ = QFileDialog.getOpenFileName(
        parent, "Load Host Potential as Structure", controller.directory or "",
        "SPR-KKR host POTFIL (*.pot *.pot_new);;All Files (*)",
    )
    if not file_path:
        return False
    path = Path(file_path).expanduser().resolve()
    try:
        if Potential.is_cluster_file(path):
            QMessageBox.warning(
                parent, "Not a Host Potential",
                "This file contains an embedded cluster, not a periodic "
                "host potential. Use Load a Structure to open a CLUPOT.",
            )
            return False
    except Exception as exc:  # noqa: BLE001 - native file identification
        QMessageBox.critical(parent, "Host Potential Load Error", str(exc))
        return False
    return load_structure(controller, recent_files, path, parent)


def attach_host_potential(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    """Attach a matching host to either a read-only CLUPOT or ASE-hosted cluster."""
    source = controller.unresolved_cluster_path
    if source is None:
        return ensure_embedded_host_potential(controller, parent)
    file_path, _ = QFileDialog.getOpenFileName(
        parent, "Load Host POTFIL for CLUPOT", str(source.parent),
        "SPR-KKR host POTFIL (*.pot *.pot_new);;All Files (*)",
    )
    return bool(file_path and load_embedded_cluster(
        controller, recent_files, source, parent, host_path=file_path,
    ))


def create_host_potential(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
) -> bool:
    """Generate a native host Potential from an existing or newly created host.

    With no geometry available in a CLUPOT, the user explicitly constructs
    a host. Its site identities are verified on loading the CLUPOT.
    """
    atoms = controller.atoms
    source = controller.unresolved_cluster_path
    source_host = getattr(atoms, "embedding_host", None)
    if source_host is None and source is not None:
        from ase2sprkkr_gui.gui.flows.structure_candidates import create_structure_candidate
        from ase2sprkkr_gui.gui.dialogs.structures.spacegroup_selector import select_spacegroup
        from ase2sprkkr_gui.gui.dialogs.structures.element_assignment import select_site_elements
        from ase2sprkkr_gui.gui.misc.dialog_flow import chain_dialogs

        candidate = create_structure_candidate(
            parent,
            chain=chain_dialogs,
            spacegroup_selector=select_spacegroup,
            site_selector=select_site_elements,
        )
        if candidate is None:
            return False
        source_host = candidate.atoms
    if source_host is None:
        QMessageBox.warning(
            parent, "Cannot Create Host Potential",
            "No host structure is available. Load an existing host POTFIL "
            "or create the matching periodic host structure.",
        )
        return False
    try:
        potential = run_modal_task(
            parent,
            lambda: Potential.from_atoms(source_host),
            title="Creating Host Potential",
            label="Preparing host POTFIL from structure…",
        )
    except Exception as exc:  # noqa: BLE001 - backend conversion
        QMessageBox.warning(parent, "Cannot Create Host Potential", str(exc))
        return False
    if source is not None:
        return load_embedded_cluster(
            controller, recent_files, source, parent, host_potential=potential,
        )
    return ensure_embedded_host_potential(
        controller, parent, host_potential=potential,
    )


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
        save_input_parameters_to_path(
            parameters.copy(copy_values=True), file_path,
            atoms=controller.atoms, directory=controller.directory,
        )
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
