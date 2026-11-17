"""Geometry-only preview for the embedding editor.

The dialog chooses which host identities are visible. This widget draws their
Cartesian positions and reports a clicked point's index, without editing atoms.
"""
from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr_gui.gui.plots.lattice import edge_indices


class EmbeddingPreview(QWidget):
    pointSelected = pyqtSignal(int)

    def __init__(self, parent=None, *, selectable=False, controls_on_right=False,
                 controls_header: QWidget | None = None):
        super().__init__(parent)
        self._press = None
        self._positions = np.empty((0, 3))
        self._selectable = selectable
        self._corners = np.empty((0, 3))
        self._colors = []
        self._sizes = []
        self._outlines = []
        self._widths = []
        self._labels = ()
        self._angles = (25., -60.)
        self._zoom_factor = 1.
        self._pan = np.zeros(2)
        self._extent = np.empty((0, 2))
        self._scatter = None
        self._label_artists = []
        self._label_index = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QVBoxLayout()
        if controls_header is not None:
            controls.addWidget(controls_header)
        self.projection = QComboBox()
        for label, angles in (
            ("3D", (25, -60)), ("XY", (90, -90)),
            ("XZ", (0, -90)), ("YZ", (0, 0)),
        ):
            self.projection.addItem(label, angles)
        self.projection.setToolTip(
            "Cartesian projections; left drag to rotate, middle drag to pan, wheel to zoom"
        )
        self.projection.currentIndexChanged.connect(self._change_projection)
        controls.addWidget(self.projection)
        self.fit_button = QPushButton("Fit")
        self.fit_button.clicked.connect(self._fit)
        controls.addWidget(self.fit_button)
        self.site_numbers = None
        if selectable:
            self.site_numbers = QCheckBox("All site\nnumbers")
            self.site_numbers.setToolTip("The selected site's number is always visible")
            self.site_numbers.toggled.connect(self._update_labels)
            controls.addWidget(self.site_numbers)
        controls.addStretch(1)
        self.figure = Figure(figsize=(4.5, 3))
        self.figure.subplots_adjust(left=0, right=1, bottom=0, top=1)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(120)
        self.axes = self.figure.add_axes((0, 0, 1, 1))
        self.axes.set_axis_off()
        self._change_projection()
        if not controls_on_right:
            layout.addLayout(controls)
        layout.addWidget(self.canvas, 1)
        if controls_on_right:
            layout.addLayout(controls)
        self.canvas.mpl_connect("scroll_event", self._zoom)
        self.canvas.mpl_connect("resize_event", self._apply_limits)
        self.canvas.mpl_connect("button_press_event", self._mouse_press)
        self.canvas.mpl_connect("motion_notify_event", self._mouse_move)
        self.canvas.mpl_connect("button_release_event", self._mouse_release)

    @property
    def view_angles(self):
        return self._angles

    def set_view(self, elev, azim):
        self._angles = (float(elev), float(azim))
        self._pan[:] = 0
        self._draw_scene()

    def project(self, positions):
        """Orthographic Cartesian projection, with depth increasing towards us."""
        elev, azim = np.radians(self._angles)
        right = np.array([-np.sin(azim), np.cos(azim), 0])
        up = np.array([-np.sin(elev) * np.cos(azim),
                       -np.sin(elev) * np.sin(azim), np.cos(elev)])
        direction = np.cross(right, up)
        positions = np.asarray(positions, dtype=float).reshape(-1, 3)
        return positions @ np.column_stack((right, up)), positions @ direction

    def _change_projection(self, *_):
        self._zoom_factor = 1.
        self.set_view(*self.projection.currentData())

    def set_points(self, positions, colors, sizes, *, cell_corners, labels=(), label_index=None,
                   outlines="#515961", widths=.4):
        """Replace geometry; keep the camera when only editing styles/labels."""
        positions = np.asarray(positions, dtype=float).reshape(-1, 3)
        corners = np.asarray(cell_corners, dtype=float)
        if not (np.array_equal(positions, self._positions)
                and np.array_equal(corners, self._corners)):
            self._zoom_factor = 1.
            self._pan[:] = 0
        self._positions, self._corners = positions, corners
        self._colors, self._sizes = np.asarray(colors), np.asarray(sizes)
        self._outlines = np.broadcast_to(outlines, (len(positions),))
        self._widths = np.broadcast_to(widths, (len(positions),))
        self._labels, self._label_index = labels, label_index
        self._draw_scene()

    def _draw_scene(self):
        ax = self.axes
        ax.clear()
        ax.set_axis_off()
        ax.set_aspect("equal", adjustable="datalim")
        self._scatter = None
        self._label_artists = []
        points, depth = self.project(self._positions)
        corners, _ = self.project(self._corners)
        self._extent = np.concatenate((points, corners))
        if len(corners):
            for edge in corners[edge_indices]:
                ax.plot(*edge.T, color="#9c8a60", linewidth=.8, alpha=.75, zorder=3)
        if len(points):
            self._point_order = np.argsort(depth, kind="stable")
            order = self._point_order
            self._scatter = ax.scatter(
                *points[order].T, c=self._colors[order], s=self._sizes[order],
                edgecolors=self._outlines[order], linewidths=self._widths[order], zorder=2,
            )
            rings = self._widths > 1
            if np.any(rings):
                ax.scatter(*points[rings].T, facecolors="none", edgecolors=self._outlines[rings],
                           s=self._sizes[rings], linewidths=self._widths[rings], zorder=4)
            for point, label in zip(points, self._labels):
                self._label_artists.append(ax.annotate(
                    label, point, xytext=(6, 0), textcoords="offset points", fontsize=8,
                    annotation_clip=True, zorder=5,
                ))
        self._update_labels()
        self._apply_limits()

    def _apply_limits(self, *_):
        if not len(self._extent):
            return
        width, height = max(self.canvas.width(), 1), max(self.canvas.height(), 1)
        span = np.ptp(self._extent, axis=0)
        # Fit the actual projected shape into the *whole* rectangular canvas.
        # Keep physical aspect, reserving pixel space for markers and labels.
        padding = 28
        units = max(span[0] / max(width - 2 * padding, 1),
                    span[1] / max(height - 2 * padding, 1), 1e-6)
        centre = (self._extent.min(axis=0) + self._extent.max(axis=0)) / 2 + self._pan
        half = np.array([width, height]) * units / (2 * self._zoom_factor)
        self.axes.set_xlim(centre[0] - half[0], centre[0] + half[0])
        self.axes.set_ylim(centre[1] - half[1], centre[1] + half[1])
        self.canvas.draw_idle()

    def _fit(self):
        self._zoom_factor = 1.
        self._pan[:] = 0
        self._apply_limits()

    def _zoom(self, event):
        if event.inaxes is self.axes:
            self._zoom_factor = np.clip(self._zoom_factor * 1.2 ** event.step, .05, 1000)
            self._apply_limits()

    def _update_labels(self, *_):
        show_all = self.site_numbers is not None and self.site_numbers.isChecked()
        for index, text in enumerate(self._label_artists):
            text.set_visible(show_all or index == self._label_index)
        self.canvas.draw_idle()

    def _mouse_press(self, event):
        self._press = None
        if event.button in (1, 2) and event.inaxes is self.axes:
            self._press = (event.x, event.y, event.button, self._angles, self._pan.copy(),
                           np.ptp(self.axes.get_xlim()), np.ptp(self.axes.get_ylim()))

    def _mouse_move(self, event):
        if self._press is None:
            return
        x, y, button, angles, pan, xspan, yspan = self._press
        dx, dy = event.x - x, event.y - y
        if np.hypot(dx, dy) <= 5:
            return
        if button == 1:
            self.set_view(angles[0] + dy * .5, angles[1] - dx * .5)
            self.projection.blockSignals(True)
            self.projection.setCurrentIndex(0)
            self.projection.blockSignals(False)
        else:
            self._pan = pan - np.array([dx * xspan / self.canvas.width(),
                                        dy * yspan / self.canvas.height()])
            self._apply_limits()

    def _mouse_release(self, event):
        press, self._press = self._press, None
        if (press is None or not self._selectable or event.button != 1
                or event.inaxes is not self.axes or self._scatter is None
                or np.hypot(event.x - press[0], event.y - press[1]) > 5):
            return
        hit, details = self._scatter.contains(event)
        if not hit:
            return
        indices = self._point_order[np.asarray(details["ind"], dtype=int)]
        points, depth = self.project(self._positions[indices])
        screen = self.axes.transData.transform(points)
        distance = np.linalg.norm(screen - [event.x, event.y], axis=1)
        nearest = distance <= distance.min() + 1.
        candidates = np.flatnonzero(nearest)
        index = candidates[np.argmax(depth[nearest])]
        self.pointSelected.emit(int(indices[index]))
