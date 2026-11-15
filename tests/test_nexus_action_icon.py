"""Use original official NeXus icons for the spectrum export action."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

from ase2sprkkr_gui.gui.misc.resources import icon_path
from ase2sprkkr_gui.gui.widgets import result_actions


def test_nexus_icons_are_bundled_and_render_at_action_size():
    app = QApplication.instance() or QApplication([])
    assert not icon_path("document-export.svg").exists()
    for name in ("NeXus_Logo_square.svg", "NeXus_Logo_dark_square.svg"):
        path = icon_path(name)
        assert path.is_file()
        assert "<svg" in path.read_text(encoding="utf-8")
    widget = QWidget()
    try:
        assert not result_actions.result_action_icon(widget.style(), "export_nexus").pixmap(22, 22).isNull()
    finally:
        widget.close()


def test_nexus_uses_background_appropriate_official_variant(monkeypatch):
    app = QApplication.instance() or QApplication([])
    previous = QPalette(app.palette())
    widget = QWidget()
    called = []
    original_icon_path = result_actions.icon_path

    def tracked_icon_path(name):
        called.append(name)
        return original_icon_path(name)

    monkeypatch.setattr(result_actions, "icon_path", tracked_icon_path)
    try:
        for background, expected in (
            ("#ffffff", "NeXus_Logo_square.svg"),
            ("#1c1c1c", "NeXus_Logo_dark_square.svg"),
        ):
            palette = QPalette(previous)
            palette.setColor(QPalette.ColorRole.Window, QColor(background))
            app.setPalette(palette)
            result_actions.result_action_icon(widget.style(), "export_nexus")
            assert called[-1] == expected
    finally:
        app.setPalette(previous)
        widget.close()
