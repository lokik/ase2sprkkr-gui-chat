"""Interpret calculation results and result files without changing a workspace."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ase2sprkkr import Potential, TaskResult
from ase2sprkkr.outputs import FileAssociation, ResultLoader
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
    # Cheap, pre-parser stdout fingerprint used to reject concurrent rewrites.
    parsed_output_version: PotentialFileVersion | None = None


class ResultImportCache:
    """Batch-local, version-checked parsing; distinct results keep their metadata.

    CLUPOT and its host are parsed without constructing Atoms. Their joint
    structure is reconstructed on first use, then reused until either file
    changes. The native SCF convergence correction is never written into the
    shared object.
    """

    def __init__(self):
        self.loader = ResultLoader()
        self.inputs = self.loader.inputs
        self.potentials: dict[Path, LoadedResult] = {}
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
        """Reuse backend-parsed CLUPOT/host, without changing cached SCF state."""
        require_potential_version_current(cluster_version)
        require_potential_version_current(host_version)
        atoms = self.loader.potential(cluster_path, host=host_path).atoms
        if atoms is None:
            raise ValueError(f"Cluster potential contains no structure: {cluster_path}")
        require_potential_version_current(cluster_version)
        require_potential_version_current(host_version)
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
        potential_cache=cache.potentials if cache is not None else None,
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
                pre_read_potential_version = (
                    import_cache.version(potential) if import_cache is not None
                    else PotentialFileVersion.capture(potential)
                )
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
                    host_version = (
                        import_cache.version(host_path, require_digest=True)
                        if import_cache is not None
                        else PotentialFileVersion.capture(
                            host_path, require_digest=True,
                        )
                    )
                except OSError as exc:
                    host_version_error = exc

    try:
        if potential is not None and callable(getattr(result, "require_current_file", None)):
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
