"""Painter geometry regression tests for the compact workflow toolbar."""

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QFont, QFontMetrics, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.widgets.centered_icon_buttons import (
    CenteredIconToolButton,
    _draw_content,
)


class _PainterRecorder:
    """Capture icon and text positions without depending on a desktop theme."""

    def __init__(self):
        self.font = QFont()
        self.icons = []
        self.labels = []

    def save(self):
        pass

    def restore(self):
        pass

    def setClipRect(self, _rect):
        pass

    def setPen(self, _color):
        pass

    def setFont(self, font):
        self.font = QFont(font)

    def drawPixmap(self, x, y, pixmap):
        self.icons.append((x, y, pixmap))

    def drawText(self, rect, _alignment, text):
        self.labels.append((rect, text, QFont(self.font)))


def _button(label, mode=Qt.ToolButtonStyle.ToolButtonTextBesideIcon):
    app = QApplication.instance() or QApplication([])
    button = CenteredIconToolButton()
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.red)
    button.setIcon(QIcon(pixmap))
    button.setIconSize(QSize(24, 24))
    button.setText(label)
    button.setToolButtonStyle(mode)
    return button


def test_labeled_icon_and_wrapped_title_center_as_a_group():
    button = _button("Load Input File")
    painter = _PainterRecorder()
    area = QRect(0, 0, 160, 68)
    _draw_content(painter, button, area)

    assert len(painter.icons) == 1
    assert painter.labels
    icon_x, _, icon = painter.icons[0]
    icon_width = round(icon.width() / max(1.0, icon.devicePixelRatioF()))
    text_end = max(
        rect.left() + QFontMetrics(font).horizontalAdvance(text)
        for rect, text, font in painter.labels
    )
    # Check the actual painted icon/text group rather than the full text box.
    group_center = (icon_x + text_end) / 2
    assert abs(group_center - area.center().x()) <= 2


def test_recent_filename_is_confined_to_split_button_body():
    button = _button("Recent Results\n…/Fe2_SCF.out")
    painter = _PainterRecorder()
    # The separate dropdown control occupies the area beyond this main rect.
    main_rect = QRect(0, 0, 130, 68)
    _draw_content(painter, button, main_rect)

    assert len(painter.icons) == 1
    assert len(painter.labels) >= 2
    assert all(rect.left() >= main_rect.left() for rect, _, _ in painter.labels)
    assert all(
        rect.right() <= main_rect.right() - 7
        for rect, _, _ in painter.labels
    )


def test_compact_and_footer_icons_are_centered_in_their_own_body():
    for mode, under in (
        (Qt.ToolButtonStyle.ToolButtonIconOnly, False),
        (Qt.ToolButtonStyle.ToolButtonTextUnderIcon, True),
    ):
        button = _button("Project Browser", mode)
        painter = _PainterRecorder()
        main_rect = QRect(0, 0, 90, 68)
        _draw_content(painter, button, main_rect, under=under)

        assert len(painter.icons) == 1
        x, _, pixmap = painter.icons[0]
        icon_width = round(pixmap.width() / max(1.0, pixmap.devicePixelRatioF()))
        assert abs(x + icon_width / 2 - main_rect.center().x()) <= 2
