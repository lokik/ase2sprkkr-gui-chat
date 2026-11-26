"""Use original official NeXus icons for the spectrum export action."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

from ase2sprkkr_gui.gui.misc.resources import icon_path
from ase2sprkkr_gui.gui.widgets import result_actions


def test_nexus_icons_are_bundled_and_render_at_action_size():
    app = QApplication.instance() or QApplication([])
    for name in ("NeXus_Logo_square.svg", "NeXus_Logo_dark_square.svg"):
        path = icon_path(name)
        assert path.is_file()
        assert not QIcon(str(path)).pixmap(22, 22).isNull()
    widget = QWidget()
    try:
        assert not result_actions.result_action_icon(widget.style(), "export_nexus").pixmap(22, 22).isNull()
    finally:
        widget.close()


def test_nexus_icon_renders_distinct_variants_for_light_and_dark_backgrounds():
    app = QApplication.instance() or QApplication([])
    previous = QPalette(app.palette())
    widget = QWidget()
    images = []
    try:
        for background in ("#ffffff", "#1c1c1c"):
            palette = QPalette(previous)
            palette.setColor(QPalette.ColorRole.Window, QColor(background))
            app.setPalette(palette)
            pixmap = result_actions.result_action_icon(widget.style(), "export_nexus").pixmap(22, 22)
            assert not pixmap.isNull()
            images.append(pixmap.toImage())
        assert images[0] != images[1]
    finally:
        app.setPalette(previous)
        widget.close()
