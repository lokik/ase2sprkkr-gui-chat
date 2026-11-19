"""Fullscreen Matplotlib previews must not steal the original figure."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ase2sprkkr_gui-test-matplotlib")

from matplotlib.figure import Figure
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.widgets import matplotlib_preview as previews


def test_double_click_opens_fullscreen_and_other_buttons_do_not(monkeypatch):
    canvas = previews.PreviewFigureCanvas(Figure())
    calls = []
    monkeypatch.setattr(previews.PreviewFigureCanvas, "open_fullscreen",
                        lambda self: calls.append(self))
    canvas.show()
    QApplication.processEvents()
    QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton)
    assert calls == [canvas]

    QTest.mouseDClick(canvas, Qt.MouseButton.RightButton)
    assert calls == [canvas]
    canvas.close()


def test_fullscreen_is_modal_and_uses_an_independent_3d_figure(monkeypatch):
    figure = Figure()
    axes = figure.add_subplot(111, projection="3d")
    axes.scatter([0, 1], [0, 1], [0, 1])
    original_xlim = axes.get_xlim()
    canvas = previews.PreviewFigureCanvas(figure)
    seen = []

    def fake_show_fullscreen(dialog):
        seen.append("fullscreen")

    def fake_exec(dialog):
        assert dialog.isModal()
        assert dialog.canvas.figure is not figure
        assert dialog.canvas.figure.axes[0].name == "3d"
        dialog.canvas.figure.axes[0].set_xlim(10, 20)
        seen.append("modal")
        return 0

    monkeypatch.setattr(previews._FullscreenFigureDialog, "showFullScreen",
                        fake_show_fullscreen)
    monkeypatch.setattr(previews._FullscreenFigureDialog, "exec", fake_exec)
    canvas.open_fullscreen()

    assert seen == ["fullscreen", "modal"]
    assert canvas.figure is figure
    assert axes.get_xlim() == original_xlim
    canvas.close()


def test_non_pickleable_plot_still_expands_as_a_snapshot(monkeypatch):
    figure = Figure()
    figure.add_subplot(111).plot([0, 1], [1, 0])

    def cannot_pickle(*args, **kwargs):
        raise TypeError("custom artist cannot be pickled")

    monkeypatch.setattr(previews.pickle, "dumps", cannot_pickle)
    snapshot = previews._fullscreen_figure(figure)
    assert snapshot is not figure
    assert len(snapshot.axes) == 1
    assert len(snapshot.axes[0].images) == 1
