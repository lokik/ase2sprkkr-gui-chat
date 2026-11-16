"""Qt-independent editing draft for SPR-KKR embedded clusters.

The finite cluster is separate from the (possibly huge) periodic host.  Only
sites explicitly included in the cluster are materialized as SPRKKRAtoms on OK.
All temporary editor changes stay in this draft.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
from ase.data import atomic_numbers
from ase2sprkkr.sprkkr.embedding import embedding_data, host_atoms, host_dimension
from ase2sprkkr.sprkkr.embedding_geometry import (
    EmbeddingSymmetry,
    HostSite,
    embedding_host_indices,
    embedding_sites_within_radius,
    embedding_sites_within_shells,
)
from ase2sprkkr.sprkkr.sprkkr_atoms import SPRKKRAtoms


@dataclass
class EmbeddedSiteDraft:
    occupation: dict[str, float]
    displacement: np.ndarray = field(default_factory=lambda: np.zeros(3))
    magnetisation: np.ndarray = field(default_factory=lambda: np.zeros(2))


def parse_occupation(value: str) -> dict[str, float]:
    """Parse 'Fe' or 'Fe:0.7, Ni:0.3' without silent normalization."""
    parts = [part.strip() for part in value.replace(";", ",").split(",")]
    if not all(parts):
        raise ValueError("Occupation must contain one or more elements")
    if len(parts) == 1 and ":" not in parts[0]:
        parts[0] += ":1"
    occupation = {}
    for part in parts:
        symbol, sep, fraction = part.partition(":")
        symbol = symbol.strip()
        if symbol == "Vc":
            symbol = "X"
        if not sep or symbol not in atomic_numbers and symbol != "X":
            raise ValueError(f"Invalid occupation component {part!r}")
        if symbol in occupation:
            raise ValueError(f"Duplicate element {symbol}")
        try:
            amount = float(fraction)
        except ValueError as exc:
            raise ValueError(f"Invalid concentration {fraction!r}") from exc
        if not np.isfinite(amount) or amount <= 0 or amount > 1:
            raise ValueError("Each concentration must be between 0 and 1")
        occupation[symbol] = amount
    if not np.isclose(sum(occupation.values()), 1.0, atol=1e-6, rtol=0):
        raise ValueError("Occupation concentrations must sum to 1")
    return occupation


def occupation_label(occupation: Mapping[str, float]) -> str:
    if len(occupation) == 1 and abs(next(iter(occupation.values())) - 1.0) < 1e-8:
        return next(iter(occupation))
    return ", ".join(f"{symbol}:{amount:.6g}" for symbol, amount in occupation.items())


class EmbeddingDraft:
    """Editor-only state: host, centre and explicit included cluster sites."""

    def __init__(self, host, cluster: SPRKKRAtoms | None = None):
        self.host = host
        self.dimension = host_dimension(host)
        allowed = embedding_host_indices(host)
        if not allowed:
            raise ValueError("The host has no sites available for embedding")
        self.allowed = frozenset(allowed)
        self.entries: dict[HostSite, EmbeddedSiteDraft] = {}
        self.center = HostSite(allowed[0], (0,) * self.dimension)
        if cluster is not None:
            if cluster.embedding_host is not host:
                raise ValueError("The embedded structure must reference the selected host")
            for row, site in zip(embedding_data(cluster), cluster.sites):
                host_site = HostSite(int(row[0]), tuple(int(v) for v in row[1:])).canonical(host)
                if host_site.original_site not in self.allowed or host_site in self.entries:
                    raise ValueError("Invalid or duplicate embedded host site")
                self.entries[host_site] = EmbeddedSiteDraft(
                    occupation=dict(site.occupation.to_dict(True)),
                    displacement=np.asarray(site.embedding.displacement, dtype=float).copy(),
                    magnetisation=np.asarray(site.magnetisation, dtype=float).copy(),
                )
            if self.entries:
                self.center = next(iter(self.entries))

    def canonical(self, site: HostSite) -> HostSite:
        result = site.canonical(self.host)
        if result.original_site not in self.allowed:
            raise ValueError("This site is not in the embeddable part of the host")
        result.position(self.host)
        return result

    def cell_sites(self, cell_index) -> tuple[HostSite, ...]:
        index = tuple(cell_index)
        return tuple(HostSite(i, index).canonical(self.host) for i in sorted(self.allowed))

    def set_center(self, site: HostSite) -> None:
        self.center = self.canonical(site)

    def equivalents(self, site: HostSite) -> tuple[HostSite, ...]:
        original = self.canonical(site)
        symmetry = EmbeddingSymmetry(self.host, self.center)
        # Only sites for which CLUPOT can represent an embedding are eligible.
        return tuple(s for s in symmetry.equivalent_sites(original)
                     if s.original_site in self.allowed)

    def targets(self, site: HostSite, symmetric: bool = False) -> tuple[HostSite, ...]:
        original = self.canonical(site)
        return self.equivalents(original) if symmetric else (original,)

    def _entry(self, site: HostSite) -> EmbeddedSiteDraft:
        site = self.canonical(site)
        if site not in self.entries:
            self.entries[site] = EmbeddedSiteDraft(occupation=site.occupation(self.host))
        return self.entries[site]

    def include(self, sites) -> int:
        before = len(self.entries)
        for site in sites:
            self._entry(site)
        return len(self.entries) - before

    def exclude(self, sites) -> int:
        before = len(self.entries)
        for site in sites:
            self.entries.pop(self.canonical(site), None)
        return before - len(self.entries)

    def set_occupation(self, sites, occupation: Mapping[str, float]) -> None:
        validated = parse_occupation(occupation_label(occupation))
        for site in sites:
            self._entry(site).occupation = dict(validated)

    def reset_occupation(self, sites) -> None:
        for site in sites:
            site = self.canonical(site)
            self._entry(site).occupation = site.occupation(self.host)

    def set_displacement(self, site: HostSite, value, *, symmetric=False) -> None:
        """Apply a displacement, rotating it to equivalent host sites if requested.

        If several operations map to the same site but imply different displacements,
        the proposed operation would break the requested symmetry and is rejected.
        No draft entry is modified until all targets have been validated.
        """
        site = self.canonical(site)
        vector = np.asarray(value, dtype=float)
        if vector.shape != (3,) or not np.all(np.isfinite(vector)):
            raise ValueError("Displacement must be a finite Cartesian three-vector")
        if symmetric:
            symmetry = EmbeddingSymmetry(self.host, self.center)
            changes = {}
            for operation in symmetry.operations:
                mapped = symmetry.map_site(site, operation)
                if mapped.original_site not in self.allowed:
                    continue
                displacement = symmetry.transform_displacement(vector, operation)
                if mapped in changes and not np.allclose(
                    changes[mapped], displacement, atol=1e-7, rtol=0
                ):
                    raise ValueError("Displacement conflicts with the selected site symmetry")
                changes[mapped] = displacement
        else:
            changes = {site: vector}
        for mapped, displacement in changes.items():
            self._entry(mapped).displacement = np.array(displacement, copy=True)

    def add_radius(self, radius: float) -> int:
        return self.include(embedding_sites_within_radius(self.host, self.center, radius))

    def add_shells(self, shells: int) -> int:
        return self.include(embedding_sites_within_shells(self.host, self.center, shells))

    def to_atoms(self) -> SPRKKRAtoms:
        """Build an independent, valid explicit cluster without mutating the host."""
        if not self.entries:
            raise ValueError("The cluster must contain at least one site")
        atoms = SPRKKRAtoms.from_embedding(
            self.host,
            [(site.original_site, site.cell_index, entry.occupation)
             for site, entry in sorted(self.entries.items())],
        )
        for site, entry in zip(atoms.sites, (self.entries[k] for k in sorted(self.entries))):
            site.embedding.displacement = entry.displacement
            site.magnetisation = entry.magnetisation.copy()
        return atoms


def embedding_source(atoms):
    """Return (host, existing cluster) without accidentally creating a Potential."""
    if atoms is None:
        raise ValueError("Load a host structure first")
    if isinstance(atoms, SPRKKRAtoms) and atoms.embedding_host is not None:
        return atoms.embedding_host, atoms
    has_potential = getattr(atoms, "has_potential", None)
    if callable(has_potential) and has_potential():
        return atoms.potential, None
    return atoms, None
