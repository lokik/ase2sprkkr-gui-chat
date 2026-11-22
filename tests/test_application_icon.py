"""Rendering and inheritance of the packaged SPR-KKR application icon."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.misc.icons import application_icon
from ase2sprkkr_gui.gui.misc.resources import icon_path
from ase2sprkkr_gui.main import GuiApplication


def test_orbital_application_icon_renders_at_window_sizes():
    assert icon_path("sprkkr-orbital.svg").is_file()
    icon = application_icon()
    assert not icon.isNull()
    for size in (16, 32, 64, 128):
        image = icon.pixmap(size, size)
        assert not image.isNull()
        assert image.width() == size and image.height() == size


def test_gui_application_icon_is_inherited_by_workflow_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    previous_icon = app.windowIcon()
    previous_name = app.applicationName()
    app.setWindowIcon(QIcon())
    try:
        gui = GuiApplication(recent_files_path=tmp_path / "recent.json")
        assert app.applicationName() == "Ase2sprkkr"
        assert not app.windowIcon().isNull()
        window = gui.create_workflow_window()
        icon = window.windowIcon()
        assert not icon.isNull()
        assert not icon.pixmap(32, 32).isNull()
    finally:
        app.setWindowIcon(previous_icon)
        app.setApplicationName(previous_name)


def test_icon_is_genuine_vector():
    """The app icon contains shapes, not an embedded raster or image reference."""
    source = icon_path("sprkkr-orbital.svg").read_text(encoding="utf-8")
    assert "<image" not in source
    assert "data:image/" not in source
    assert "<path " in source
    assert "<circle " in source

