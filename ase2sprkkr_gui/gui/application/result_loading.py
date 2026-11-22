"""Interpret calculation results and result files without changing a workspace."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ase2sprkkr import Potential, TaskResult
from ase2sprkkr.outputs import FileAssociation, ResultLoader
from ase2sprkkr.outputs.file_provenance import result_file_manifest, result_file_version
from ase2sprkkr.potentials.potentials import PotentialFileChanged

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
    # Cheap, pre-parser stdout fingerprint used to reject concurrent rewrites.
    parsed_output_version: PotentialFileVersion | None = None


class ResultImportCache:
    """Batch-local, version-checked parsing; distinct results keep their metadata.

    Embedded structure reconstruction and SCF status interpretation are
    delegated to the backend ResultLoader. This cache retains only GUI-side
    version snapshots and reusable imported result metadata.
    """

    def __init__(self):
        self.loader = ResultLoader()
        self.inputs = self.loader.inputs
        self._versions: dict[Path, PotentialFileVersion] = {}

    def version(
        self, path: Path, *, require_digest: bool = False,
    ) -> PotentialFileVersion:
        """Avoid rehashing identical files for every result in the same batch."""
        path = path.resolve()
        previous = self._versions.get(path)
        if (
            previous is not None
            and (not require_digest or previous.available)
            and previous.is_current()
        ):
            return previous
        version = PotentialFileVersion.capture(path, require_digest=require_digest)
        self._versions[path] = version
        return version

    def scf_claims(self, directory: Path, potential_path: Path) -> tuple[Path, ...]:
        """Use the backend's cached directory/SCF index."""
        return self.loader.scf_claims(directory, potential_path)

def disambiguate_scf_potentials(
    loaded: list[tuple[Path, LoadedResult]],
    *,
    cache: ResultImportCache | None = None,
) -> tuple[list[tuple[Path, LoadedResult]], tuple[str, ...]]:
    """Adapt backend per-file association decisions to GUI structure state."""
    updated, ambiguous = [], []
    for path, item in loaded:
        files = getattr(item.result, "files", None)
        option = files["converged"] if files is not None and "converged" in files else None
        if option is not None and hasattr(option, "association") and (
            option.association is FileAssociation.NOT_THIS_RESULT
            or (option.association is FileAssociation.UNKNOWN
                and option.association_evidence == "scf_mtime_tie_or_unreadable")
        ):
            ambiguous.append(path.name)
            item = replace(
                item, atoms=None,
                potential_error=PotentialProvenanceAmbiguous(
                    f"Current potential {option.path()} cannot be attributed "
                    f"to the SCF output {path.name}: {option.association_evidence}"
                ),
            )
        updated.append((path, item))
    return updated, tuple(ambiguous)


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
    output_version = (
        PotentialFileVersion.capture(path, digest=False) if path.is_file() else None
    )
    loader = cache.loader if cache is not None else ResultLoader()
    result = loader.load_file(path)
    loaded = load_result(
        result,
        fallback_directory=path.parent,
        output_fallback=path,
        import_cache=cache,
    )
    if output_version is not None and not output_version.is_current():
        raise PotentialVersionChanged(
            f"Output file changed while it was read: {path}"
        )
    loaded = replace(loaded, parsed_output_version=output_version)
    # A stand-alone load must honor tied SCF ownership as well as batch loads.
    if getattr(result, "produces_potential", False):
        updated, _ambiguous = disambiguate_scf_potentials([(path, loaded)])
        loaded = updated[0][1]
    return loaded


def load_result(
    result: TaskResult,
    *,
    fallback_directory: str | Path | None,
    output_fallback: str | Path | None = None,
    persistent_ref: str | None = None,
    embedding_host=None,
    reusable_potential: LoadedResult | None = None,
    import_cache: ResultImportCache | None = None,
) -> LoadedResult:
    """Resolve result files for an already constructed calculation result."""
    output, potential = _result_files(result, fallback_directory)
    manifest = result_file_manifest(result)
    if output is None and output_fallback is not None:
        output = Path(output_fallback).resolve()

    # Reusing parsed Atoms is safe only if these are still the exact bytes
    # represented by the state recorded after parsing. Capture *before* read.
    pre_read_potential_version = None
    if potential is not None:
        try:
            if potential.is_file():
                pre_read_potential_version = (
                    import_cache.version(potential) if import_cache is not None
                    else PotentialFileVersion.capture(potential)
                )
        except OSError:
            pass  # A missing/unreadable POTFIL is reported by the normal loader.

    # TaskResult declares any additional source potentials needed to
    # reconstruct its structure. Only the backend knows whether these are
    # periodic hosts, embedded cluster sources, or another native dependency.
    dependency_versions = []
    dependency_error = None
    if potential is not None:
        for source in getattr(result, "potential_dependencies", ()):
            source_path = _resolve_path(source, result, fallback_directory)
            if manifest is not None:
                # Historical evidence must never be replaced by a fresh
                # filesystem snapshot.
                version = manifest.input_version(source_path)
            else:
                try:
                    version = (
                        import_cache.version(source_path, require_digest=True)
                        if import_cache is not None
                        else PotentialFileVersion.capture(
                            source_path, require_digest=True,
                        )
                    )
                except OSError as exc:
                    dependency_error = exc
                    break
            if version is not None:
                dependency_versions.append(version)

    try:
        if potential is not None and callable(getattr(result, "require_current_file", None)):
            result.require_current_file(potential)
    except OSError as exc:
        atoms, potential_error = None, exc
    else:
        if potential is None:
            atoms, potential_error = None, None
        elif isinstance(result, TaskResult):
            # A native result owns its scientific structure and SCF status.
            # ResultLoader is optional and changes only parsing efficiency.
            try:
                atoms = result.get_atoms(
                    loader=import_cache.loader if import_cache is not None else None
                )
                if atoms is None:
                    raise ValueError(f"Potential contains no structure: {potential}")
                potential_error = None
            except Exception as exc:  # noqa: BLE001 - corrupted native file
                atoms, potential_error = None, exc
        elif getattr(result, "potential", None) is not None:
            # Lightweight non-native results used by GUI operations/tests.
            try:
                atoms = result.potential.atoms
                potential_error = None
            except Exception as exc:  # noqa: BLE001 - invalid external data
                atoms, potential_error = None, exc
        elif (
            reusable_potential is not None
            and reusable_potential.potential_path == potential
            and reusable_potential.atoms is not None
            and reusable_potential.potential_error is None
            and reusable_potential.parsed_potential_version is not None
            and reusable_potential.parsed_potential_version == pre_read_potential_version
        ):
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
    if atoms is not None and dependency_error is not None:
        atoms, potential_error = None, dependency_error
    if atoms is not None:
        for version in dependency_versions:
            if not version.is_current():
                atoms, potential_error = None, PotentialVersionChanged(
                    f"Source potential changed while the result was loaded: {version.path}"
                )
                break
    source_versions = tuple(dependency_versions) if atoms is not None else ()
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


def load_companion_scf_result(
    loaded: LoadedResult, *, cache: ResultImportCache | None = None,
) -> LoadedResult | None:
    """Find the companion SCF through the canonical backend ResultLoader."""
    if loaded.output_path is None or loaded.potential_path is None:
        return None
    loader = cache.loader if cache is not None else ResultLoader()
    result = loader.companion_scf(loaded.result)
    if result is None:
        return None
    path = Path(result.path_to("output")).resolve()
    return load_result(
        result, fallback_directory=path.parent, output_fallback=path,
        reusable_potential=loaded, import_cache=cache,
    )


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
    """Resolve paths supplied by the result without reinterpreting its task."""
    output = _registered_path(result, "output")
    if output is None:
        output = getattr(result, "output_file", None)
    output = _resolve_path(output, result, fallback_directory)

    try:
        potential = result.potential_filename
    except (AttributeError, KeyError, ValueError):
        potential = None
    return output, _resolve_path(potential, result, fallback_directory)

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
