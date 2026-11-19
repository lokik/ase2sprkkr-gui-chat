"""Read-only structure-editor cluster/host previews.

Cluster atom identity comes from the native embedding array: its first column
is the host site index. SiteType grouping is intentionally separate for the
cluster and host (substitutions can change the local site symmetry).
"""
from __future__ import annotations

from itertools import product

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import to_hex
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from ase2sprkkr.sprkkr.embedding import embedding_data, host_atoms
from ase2sprkkr_gui.gui.plots.atom_sizes import marker_areas
from ase2sprkkr_gui.gui.widgets.structures.embedding_preview import EmbeddingPreview


def site_type_group_keys(atoms):
    """Use actual shared SiteType objects, falling back to ASE symmetry kinds."""
    if atoms is None:
        return ()
    sites = getattr(atoms, "arrays", {}).get("sprkkr_sites_data")
    if sites is not None and len(sites) == len(atoms):
        return tuple(("site_type", id(site.site_type)) for site in sites)
    kinds = atoms.arrays.get("spacegroup_kinds")
    if kinds is not None and len(kinds) == len(atoms):
        return tuple(("spacegroup_kind", str(kind)) for kind in kinds)
    return tuple(("symbol", symbol) for symbol in atoms.get_chemical_symbols())


def site_type_colors(atoms):
    """Colors distinguish site types, not merely chemical elements."""
    keys = site_type_group_keys(atoms)
    if not keys:
        return np.empty(0, dtype=object)
    ordered = tuple(dict.fromkeys(keys))
    palette = colormaps.get_cmap("tab20").resampled(max(len(ordered), 1))
    colors = {
        key: to_hex(palette(index))
        for index, key in enumerate(ordered)
    }
    return np.asarray([colors[key] for key in keys])


def cluster_preview_style(atoms, selected=()):
    """Highlighted atoms remain visible with a larger marker and bright ring."""
    count = len(atoms)
    colors = site_type_colors(atoms)
    sizes = marker_areas(atoms, 55.0)
    outlines = np.full(count, "#333a42", dtype=object)
    widths = np.full(count, 0.65)
    for index in selected:
        if 0 <= int(index) < count:
            sizes[int(index)] *= 145.0 / 55.0
            outlines[int(index)] = "#ffcf4f"
            widths[int(index)] = 3.0
    return colors, sizes, outlines, widths


def host_site_cluster_indices(cluster, host_site):
    """Return *all* periodic-image cluster atoms referring to a host site."""
    if cluster is None or host_site < 0:
        return ()
    rows = embedding_data(cluster)
    return tuple(np.flatnonzero(rows[:, 0] == int(host_site)).tolist())


def _cell_corners(atoms):
    if atoms is None:
        return np.empty((0, 3))
    cell = np.asarray(atoms.cell, dtype=float)
    return np.asarray(list(product((0, 1), repeat=3)), dtype=float) @ cell


class EditorClusterHostPreviews(QWidget):
    """Vertically stacked read-only previews used by the Overview tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cluster = None
        self._host = None
        self._scope = "cluster"
        self._row = -1
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter, 1)

        cluster_page = QWidget(self.splitter)
        cluster_layout = QVBoxLayout(cluster_page)
        self.cluster_label = QLabel("Embedded cluster", cluster_page)
        label_font = self.cluster_label.font()
        label_font.setBold(True)
        self.cluster_label.setFont(label_font)
        cluster_layout.addWidget(self.cluster_label)
        self.cluster_preview = EmbeddingPreview(
            cluster_page, compact=True, controls_on_right=True,
        )
        cluster_layout.addWidget(self.cluster_preview, 1)
        self.splitter.addWidget(cluster_page)

        host_page = QWidget(self.splitter)
        host_layout = QVBoxLayout(host_page)
        self.host_label = QLabel("Host structure", host_page)
        self.host_label.setFont(label_font)
        host_layout.addWidget(self.host_label)
        self.host_preview = EmbeddingPreview(
            host_page, compact=True, controls_on_right=True,
        )
        host_layout.addWidget(self.host_preview, 1)
        self.splitter.addWidget(host_page)
        self.splitter.setSizes([300, 300])

    def set_structure(self, cluster):
        self._cluster = cluster
        self._host = host_atoms(cluster.embedding_host) if cluster is not None else None
        if self._host is not None:
            colors = site_type_colors(self._host)
            count = len(self._host)
            self.host_preview.set_points(
                self._host.get_positions(), colors,
                marker_areas(self._host, 55.0, potential=self._cluster.embedding_host),
                cell_corners=_cell_corners(self._host),
                outlines="#333a42", widths=0.65,
            )
        self.update_selection(self._scope, self._row)

    def update_selection(self, scope, row):
        self._scope, self._row = scope, int(row)
        if self._cluster is None:
            return
        if scope == "host":
            selected = host_site_cluster_indices(self._cluster, self._row)
        elif 0 <= self._row < len(self._cluster):
            selected = (self._row,)
        else:
            selected = ()
        colors, sizes, outlines, widths = cluster_preview_style(
            self._cluster, selected,
        )
        self.cluster_preview.set_points(
            self._cluster.get_positions(), colors, sizes,
            cell_corners=np.empty((0, 3)),
            outlines=outlines, widths=widths,
        )
