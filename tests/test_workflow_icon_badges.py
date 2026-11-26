"""Keep workflow badges recognizable and distinct at toolbar sizes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.misc.icons import badge_icon
from ase2sprkkr_gui.gui.misc.resources import icon_path


def test_recent_and_scf_badges_have_distinct_drawings():
    _app = QApplication.instance() or QApplication([])
    base = QIcon(str(icon_path("calculation-gear.svg")))
    assert not base.isNull()
    images = {}
    for symbol in ("recent", "reconverge", "new", "load"):
        icon = badge_icon(base, symbol)
        assert not icon.isNull()
        for pixels in (24, 26, 48):
            image = icon.pixmap(pixels, pixels).toImage()
            assert not image.isNull()
        images[symbol] = icon.pixmap(48, 48).toImage()

    assert images["recent"] != images["reconverge"]
    assert images["recent"] != images["new"]
    assert images["reconverge"] != images["new"]


def test_load_import_subjects_and_badges_are_distinct():
    _app = QApplication.instance() or QApplication([])
    images = []
    for filename in (
        "input-parameters.svg",
        "result-graph.svg",
        "results-folder.svg",
    ):
        subject = QIcon(str(icon_path(filename)))
        assert not subject.isNull()
        icon = badge_icon(subject, "load")
        assert not icon.isNull()
        for pixels in (24, 26, 48):
            assert not icon.pixmap(pixels, pixels).toImage().isNull()
        images.append(icon.pixmap(48, 48).toImage())
    assert images[0] != images[1]
    assert images[0] != images[2]
    assert images[1] != images[2]
