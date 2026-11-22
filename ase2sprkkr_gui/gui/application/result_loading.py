"""Interpret calculation results and result files without changing a workspace."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ase2sprkkr import Potential, TaskResult
from ase2sprkkr.outputs import looks_like_scf_output
try:
    from ase2sprkkr.outputs.task_result import InputFileCache
except ImportError:
    # Older installed backend releases still support ordinary output loading.
    InputFileCache = None
from ase2sprkkr.outputs.file_provenance import result_file_manifest, result_file_version
from ase2sprkkr.potentials.potentials import PotentialFileChanged

from ase2sprkkr_gui.ase.copy import copy_for_edit

from ase2sprkkr_gui.gui.application.potential_version import PotentialFileVersion


class PotentialVersionChanged(RuntimeError):
    """The potential file no longer matches the version approved by the user."""


class PotentialProvenanceAmbiguous(ValueError):
    """Several imported SCFs claim one overwritten output potential path."""


@dataclass(frozen=True)
class FileBackedInput:
    """Parsed structure and all potential input versions verified at launch."""

    atoms: Any
    input_versions: tuple[PotentialFileVersion, ...]

    @property
    def potential_version(self) -> PotentialFileVersion:
        # The cluster potential is the primary structural state for embedding.
        return self.input_versions[-1]


@dataclass(frozen=True)
class LoadedResult:
    """A result whose filesystem references have already been interpreted.

    ``output_path`` and ``potential_path`` are provenance file locations.
    The authoritative physical potential, when successfully loaded, is attached
    to ``atoms``. A missing referenced potential is different: reconstructing
    atoms depends on it, so ``potential_error`` contains ``FileNotFoundError``.

    ``persistent_ref`` is the stable ASE2SPRKKR persistent-run identity when
    this result originated from a persistent execution. It deliberately does
    not duplicate the backend-specific run reference.
    """

    result: TaskResult
    output_path: Path | None
    potential_path: Path | None
    atoms: Any | None
    potential_error: Exception | None
    directory: str | None
    input_parameters: Any | None = None
    input_parameters_error: Exception | None = None
    persistent_ref: str | None = None
    file_backed_input: FileBackedInput | None = None
    output_potential_version: PotentialFileVersion | None = None
    # Captured before parsing and verified afterwards, for one-shot reuse.
    parsed_potential_version: PotentialFileVersion | None = None
    source_versions: tuple[PotentialFileVersion, ...] = ()


class ResultImportCache:
    """Batch-local, version-checked parsing; distinct results keep their metadata.

    CLUPOT and its host are parsed without constructing Atoms. Their joint
    structure is reconstructed on first use, then reused until either file
    changes. The native SCF convergence correction is never written into the
    shared object.
    """

    def __init__(self):
        self.inputs = InputFileCache() if InputFileCache is not None else None
        self.potentials: dict[Path, LoadedResult] = {}
        self._hosts: dict[Path, tuple[PotentialFileVersion, Any]] = {}
        self._clusters: dict[
            tuple[Path, Path],
            tuple[PotentialFileVersion, PotentialFileVersion, Any],
        ] = {}

    def remember(self, loaded: LoadedResult) -> None:
        if (
            loaded.potential_path is not None
            and loaded.atoms is not None
            and loaded.parsed_potential_version is not None
            and not getattr(loaded.result, "is_embedded", False)
        ):
            self.potentials[loaded.potential_path] = loaded

    def embedded_atoms(
        self,
        cluster_path: Path,
        host_path: Path,
        cluster_version: PotentialFileVersion,
        host_version: PotentialFileVersion,
    ) -> Any:
        """Parse each native file once, with the host/CLUPOT dependency tracked."""
        require_potential_version_current(cluster_version)
        require_potential_version_current(host_version)
        key = (cluster_path, host_path)
        cached = self._clusters.get(key)
        if cached is not None and cached[:2] == (cluster_version, host_version):
            return cached[2]

        host_entry = self._hosts.get(host_path)
        if host_entry is not None and host_entry[0] == host_version:
            host = host_entry[1]
        else:
            host = Potential.from_file(
                host_path, atoms=False, check_file_unchanged=True,
            )
            require_potential_version_current(host_version)
            self._hosts[host_path] = (host_version, host)

        # Explicitly avoid any SCF-specific mutation of the cached CLUPOT.
        cluster = Potential.from_file(
            cluster_path, atoms=False, host=host, check_file_unchanged=True,
        )
        require_potential_version_current(cluster_version)
        require_potential_version_current(host_version)
        atoms = cluster.atoms
        if atoms is None:
            raise ValueError(f"Cluster potential contains no structure: {cluster_path}")
        require_potential_version_current(cluster_version)
        require_potential_version_current(host_version)
        self._clusters[key] = (cluster_version, host_version, atoms)
        return atoms


def _embedded_atoms_for_result(result: Any, atoms: Any) -> Any:
    """Keep output-derived convergence separate from cached CLUPOT file data."""
    if str(getattr(result, "task_name", "")).casefold() != "scf":
        return atoms
    if getattr(result, "return_code", 0) != 0:
        return atoms
    try:
        if not getattr(result, "iterations", None) or not result.converged:
            return atoms
        status = atoms.potential.SCF_INFO.SCFSTATUS()
    except (AttributeError, KeyError, TypeError, ValueError):
        return atoms
    if str(status).strip().upper() != "ITERATING":
        return atoms
    # A shallow structural copy clones the attached potential's option
    # containers while sharing unchanged, potentially large radial arrays.
    selected = copy_for_edit(atoms)
    selected.potential.SCF_INFO.SCFSTATUS = "CONVERGED"
    selected.info["sprkkr_embedding_scf_status"] = "CONVERGED"
    return selected


def disambiguate_scf_potentials(
    loaded: list[tuple[Path, LoadedResult]],
) -> tuple[list[tuple[Path, LoadedResult]], tuple[str, ...]]:
    """Do not associate one current pot_new with multiple historical SCF runs.

    A filename cannot tell which SCF produced the bytes now on disk. Preserve
    each OUT/result, but leave these SCF results without a reusable state.
    Non-SCF tasks legitimately consuming the same input POTFIL are unaffected.
    """
    by_potential: dict[Path, list[int]] = {}
    for index, (_, item) in enumerate(loaded):
        if (
            item.potential_path is not None
            and str(getattr(item.result, "task_name", "")).casefold() == "scf"
            and getattr(item.result, "produces_potential", True)
        ):
            by_potential.setdefault(item.potential_path, []).append(index)

    ambiguous = {
        index
        for indices in by_potential.values() if len(indices) > 1
        for index in indices
        if len({loaded[i][0].resolve() for i in indices}) > 1
    }
    if not ambiguous:
        return loaded, ()
    updated = list(loaded)
    for index in sorted(ambiguous):
        path, item = updated[index]
        error = PotentialProvenanceAmbiguous(
            f"Several SCF outputs reference {item.potential_path.name}; "
            "the current file cannot be attributed to this SCF output."
        )
        updated[index] = (path, replace(item, atoms=None, potential_error=error))
    return updated, tuple(loaded[i][0].name for i in sorted(ambiguous))


def load_potential_atoms(file_path: str | Path, *, embedding_host=None) -> Any:
    """Read a periodic POTFIL or CLUPOT, preserving native radial information."""
    path = Path(file_path).resolve()
    if Potential.is_cluster_file(path):
        if embedding_host is None:
            raise ValueError(
                f"Embedded cluster {path} requires a host SPR-KKR Potential"
            )
        from ase2sprkkr.potentials.cluster_potentials import ClusterPotential
        potential = ClusterPotential.from_file(path, host=embedding_host)
    else:
        potential = Potential.from_file(str(path), check_file_unchanged=True)
    atoms = potential.atoms
    if atoms is None:
        raise ValueError(f"Potential file contains no structure: {path}")
    return atoms


def require_potential_version_current(version: PotentialFileVersion) -> None:
    """Reject a POTFIL path that no longer contains ``version``."""
    current = version.current()
    if not current.available or current != version:
        raise PotentialVersionChanged(
            f"Potential file changed after it was presented: {version.path}"
        )


def load_potential_version(
    version: PotentialFileVersion, *, embedding_host=None
) -> Any:
    """Load a versioned POTFIL or CLUPOT, rejecting pre/read/post races."""
    require_potential_version_current(version)
    try:
        atoms = load_potential_atoms(
            version.path, embedding_host=embedding_host
        )
    except PotentialFileChanged as exc:
        raise PotentialVersionChanged(
            f"Potential file changed while it was being read: {version.path}"
        ) from exc

    after = version.current()
    if not after.available or after != version:
        raise PotentialVersionChanged(
            f"Potential file changed while it was being read: {version.path}"
        )
    return atoms


def load_result_file(
    file_path: str | Path, *, cache: ResultImportCache | None = None,
) -> LoadedResult:
    """Parse an external result, optionally reusing one batch's inputs and POTFILs."""
    path = Path(file_path).resolve()
    result = (
        TaskResult.from_file(path, input_cache=cache.inputs)
        if cache is not None and cache.inputs is not None else TaskResult.from_file(path)
    )
    loaded = load_result(
        result,
        fallback_directory=path.parent,
        output_fallback=path,
        potential_cache=cache.potentials if cache is not None else None,
        import_cache=cache,
    )
    if cache is not None:
        cache.remember(loaded)
    return loaded


def load_result(
    result: TaskResult,
    *,
    fallback_directory: str | Path | None,
    output_fallback: str | Path | None = None,
    persistent_ref: str | None = None,
    embedding_host=None,
    reusable_potential: LoadedResult | None = None,
    potential_cache: dict[Path, LoadedResult] | None = None,
    import_cache: ResultImportCache | None = None,
) -> LoadedResult:
    """Resolve result files for an already constructed calculation result."""
    output, potential = _result_files(result, fallback_directory)
    if reusable_potential is None and potential_cache is not None:
        reusable_potential = potential_cache.get(potential)
    manifest = result_file_manifest(result)
    if output is None and output_fallback is not None:
        output = Path(output_fallback).resolve()

    # Reusing parsed Atoms is safe only if these are still the exact bytes
    # represented by the state recorded after parsing. Capture *before* read.
    pre_read_potential_version = None
    if potential is not None:
        try:
            if potential.is_file():
                pre_read_potential_version = PotentialFileVersion.capture(potential)
        except OSError:
            pass  # A missing/unreadable POTFIL is reported by the normal loader.

    # Native embedded output identifies its host through CONTROL.POTFIL.
    # Capture that file before parsing the CLUPOT, then verify the very same
    # version afterwards. Do not guess a host from neighboring filenames.
    host_version = None
    host_version_error = None
    if potential is not None and getattr(result, "is_embedded", False):
        try:
            host_path = result.host_potential_filename
        except (AttributeError, KeyError, ValueError):
            pass  # Without native input settings, host provenance is unknown.
        else:
            host_path = _resolve_path(host_path, result, fallback_directory)
            if manifest is not None:
                # A historical manifest is authoritative; never attribute
                # today's host bytes to a previous run.
                host_version = manifest.input_version(host_path)
            else:
                try:
                    host_version = PotentialFileVersion.capture(
                        host_path, require_digest=True,
                    )
                except OSError as exc:
                    host_version_error = exc

    try:
        if potential is not None and manifest is not None:
            result.require_current_file(potential)
    except OSError as exc:
        atoms, potential_error = None, exc
    else:
        if getattr(result, "is_embedded", False) and potential is not None:
            try:
                if (
                    import_cache is not None
                    and pre_read_potential_version is not None
                    and host_version is not None
                    and host_version.available
                ):
                    # Bulk import parses the native CLUPOT without Atoms or an
                    # SCF-dependent status. Reuse its host and geometry.
                    host_path = Path(host_version.path).resolve()
                    atoms = import_cache.embedded_atoms(
                        potential, host_path, pre_read_potential_version,
                        host_version,
                    )
                    atoms = _embedded_atoms_for_result(result, atoms)
                else:
                    # Keep regular, standalone loading and historical manifest
                    # semantics intact when a safe cache key is unavailable.
                    atoms = result.potential.atoms
                potential_error = None
            except Exception as exc:  # noqa: BLE001 - corrupted native file
                atoms, potential_error = None, exc
        elif (
            reusable_potential is not None
            and not getattr(result, "is_embedded", False)
            and reusable_potential.potential_path == potential
            and reusable_potential.atoms is not None
            and reusable_potential.potential_error is None
            and reusable_potential.parsed_potential_version is not None
            and reusable_potential.parsed_potential_version == pre_read_potential_version
        ):
            # The selected output has already parsed exactly this POTFIL.
            # Preserve the per-result manifest checks above and below.
            atoms, potential_error = reusable_potential.atoms, None
        else:
            atoms, potential_error = _load_potential(
                potential, embedding_host=embedding_host
            )
        if atoms is not None and manifest is not None:
            try:
                result.require_current_file(potential)
            except OSError as exc:
                atoms, potential_error = None, exc
    if atoms is not None and host_version_error is not None:
        atoms, potential_error = None, host_version_error
    if atoms is not None and host_version is not None and not host_version.is_current():
        atoms, potential_error = None, PotentialVersionChanged(
            f"Host potential changed while the embedded result was loaded: {host_version.path}"
        )
    source_versions = (
        (host_version,) if atoms is not None and host_version is not None else ()
    )
    parsed_potential_version = None
    if atoms is not None and pre_read_potential_version is not None:
        try:
            if (pre_read_potential_version.available
                    and pre_read_potential_version.is_current()):
                parsed_potential_version = pre_read_potential_version
        except OSError:
            pass  # Re-parse when restoring instead of reusing unverified data.
    input_parameters, input_parameters_error = _load_input_parameters(result)
    directory = str(output.parent.resolve()) if output is not None else None
    return LoadedResult(
        result=result,
        output_path=output,
        potential_path=potential,
        atoms=atoms,
        potential_error=potential_error,
        directory=directory,
        input_parameters=input_parameters,
        input_parameters_error=input_parameters_error,
        persistent_ref=persistent_ref,
        output_potential_version=(
            result_file_version(result, potential)
            if potential is not None and manifest is not None else None
        ),
        source_versions=source_versions,
        parsed_potential_version=parsed_potential_version,
    )


def _scf_candidate_sort_key(
    path: Path, selected_stem: str, selected_task: str,
) -> tuple[int, str]:
    """Prioritize the same dataset's SCF output, without assuming a naming rule."""
    base = selected_stem.casefold()
    task = selected_task.casefold()
    # E.g. Fe_DOS.out -> Fe_SCF.out (also handle Fe_DOS_DOS.out).
    for separator in ("_", "-", "."):
        suffix = separator + task
        while base.endswith(suffix):
            base = base[:-len(suffix)]

    stem = path.stem.casefold()
    if any(stem == base + separator + "scf" for separator in ("_", "-", ".")):
        rank = 0
    elif stem == base or any(
        stem.startswith(base + separator) for separator in ("_", "-", ".")
    ):
        rank = 1
    elif "scf" in stem:
        rank = 2
    else:
        rank = 3
    return rank, stem


def load_companion_scf_result(loaded: LoadedResult) -> LoadedResult | None:
    """Try to find the SCF output that produced a non-SCF result's potential.

    First try similarly named .out files, then other .out files whose opening
    banner says KKRSCF. A match also must reference exactly the same potential.
    Missing, unreadable, or unrelated outputs are never errors for the result.
    """
    if loaded.output_path is None or loaded.potential_path is None:
        return None
    task_name = str(getattr(loaded.result, "task_name", "")).casefold()
    if task_name == "scf":
        return None

    directory = loaded.output_path.parent
    target_potential = loaded.potential_path.resolve()
    try:
        candidates = sorted(
            (path for path in directory.iterdir()
             if path != loaded.output_path and path.is_file()
             and path.suffix.casefold() == ".out"),
            key=lambda path: _scf_candidate_sort_key(
                path, loaded.output_path.stem, task_name,
            ),
        )
    except OSError:
        return None

    for path in candidates:
        if not looks_like_scf_output(path, potential_filename=target_potential):
            continue
        try:
            result = TaskResult.from_file(path)
            if str(getattr(result, "task_name", "")).casefold() != "scf":
                continue
            # Compare provenance before loading the potentially large POTFIL.
            _output, potential = _result_files(result, directory)
            if potential != target_potential:
                continue
            return load_result(
                result, fallback_directory=directory, output_fallback=path,
                reusable_potential=loaded,
            )
        except Exception:  # noqa: BLE001 - optional, possibly corrupt output
            continue
    return None


def _load_input_parameters(result: Any) -> tuple[Any | None, Exception | None]:
    try:
        return getattr(result, "input_parameters", None), None
    except Exception as exc:  # noqa: BLE001 - parser failures are data
        return None, exc


def _registered_path(result: Any, key: str) -> Path | None:
    files = getattr(result, "files", None)
    if files is None or key not in files:
        return None
    value = result.path_to(key)
    return Path(value) if value else None


def _resolve_path(
    path: str | Path | None,
    result: Any,
    fallback_directory: str | Path | None,
) -> Path | None:
    if not path:
        return None
    resolved = Path(path)
    if not resolved.is_absolute():
        base = getattr(result, "directory", None) or fallback_directory
        if base:
            resolved = Path(base) / resolved
    return resolved.resolve()


def _result_files(
    result: Any,
    fallback_directory: str | Path | None,
) -> tuple[Path | None, Path | None]:
    output = _registered_path(result, "output")
    if output is None:
        output = getattr(result, "output_file", None)
    output = _resolve_path(output, result, fallback_directory)

    # For embedded tasks (including DOS), the potential that defines the
    # calculation structure is POTFIL_CLU, not the periodic host POTFIL. The
    # latter may be registered as FILES["potential"], but restoring it as a
    # result state would silently turn the cluster into a bulk structure.
    embedded = getattr(result, "is_embedded", False)
    potential = None
    if embedded:
        try:
            potential = getattr(result, "potential_filename", None)
        except (AttributeError, KeyError, ValueError):
            pass
        if potential is None:
            potential = _registered_path(result, "converged")
        # Never fall back to the host's FILES["potential"] for an embedded
        # result: without CLUPOT the result has no reusable cluster state.
    else:
        potential = _registered_path(result, "converged")
        if potential is None:
            # The FILES entry named "potential" is the *input* POTFIL for SCF.
            # ScfResult.potential_filename, in contrast, points to the newly
            # produced POTFIL (_new). Never adopt the input as SCF output.
            try:
                is_scf = str(getattr(result, "task_name", "")).casefold() == "scf"
            except (AttributeError, KeyError, ValueError):
                is_scf = False
            if is_scf:
                try:
                    potential = getattr(result, "potential_filename", None)
                except (AttributeError, KeyError, ValueError):
                    potential = None
        if potential is None:
            potential = _registered_path(result, "potential")
        if potential is None:
            try:
                potential = getattr(result, "potential_filename", None)
            except (AttributeError, KeyError, ValueError):
                potential = None
    potential = _resolve_path(potential, result, fallback_directory)
    return output, potential


def _load_potential(
    potential: Path | None,
    *,
    embedding_host=None,
) -> tuple[Any | None, Exception | None]:
    if potential is None:
        return None, None
    if not potential.exists():
        return None, FileNotFoundError(
            f"Referenced potential file does not exist: {potential}"
        )
    if not potential.is_file():
        return None, IsADirectoryError(
            f"Referenced potential is not a file: {potential}"
        )
    try:
        if embedding_host is not None:
            return load_potential_atoms(potential, embedding_host=embedding_host), None
        return load_potential_atoms(potential), None
    except Exception as exc:  # noqa: BLE001 - parser failures are data
        return None, exc
