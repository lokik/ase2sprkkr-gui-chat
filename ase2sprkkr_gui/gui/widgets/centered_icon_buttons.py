"""Navigation buttons with icon placement independent of Qt text layout."""
from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QIcon, QPalette
from PyQt6.QtWidgets import (
    QPushButton, QStyle, QStyleOptionButton, QStyleOptionToolButton,
    QStylePainter, QToolButton,
)


def _draw_content(painter, button, rect: QRect, *, under: bool = False) -> None:
    """Center the icon in the click region (excluding a split-menu arrow)."""
    bounds = rect.adjusted(6, 3, -6, -3)
    if bounds.width() <= 0 or bounds.height() <= 0:
        return

    icon = button.icon().pixmap(
        button.iconSize(),
        QIcon.Mode.Normal if button.isEnabled() else QIcon.Mode.Disabled,
    )
    # Pixmap dimensions can be physical pixels on high-DPI screens.
    ratio = max(1.0, icon.devicePixelRatioF())
    icon_width = round(icon.width() / ratio)
    icon_height = round(icon.height() / ratio)
    text = (
        "" if isinstance(button, QToolButton)
        and button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        else button.text()
    )
    lines = text.splitlines() if text else []
    metrics = button.fontMetrics()
    gap = 5
    line_height = metrics.height()
    text_height = len(lines) * line_height

    if under and lines:
        block_height = icon_height + gap + text_height
        icon_y = bounds.center().y() - block_height // 2
        icon_x = bounds.center().x() - icon_width // 2
        painter.drawPixmap(icon_x, icon_y, icon)
        for i, line in enumerate(lines):
            painter.drawText(
                QRect(
                    bounds.left(), icon_y + icon_height + gap + i * line_height,
                    bounds.width(), line_height,
                ),
                Qt.AlignmentFlag.AlignCenter,
                metrics.elidedText(
                    line, Qt.TextElideMode.ElideRight, bounds.width()
                ),
            )
        return

    if not lines:
        # Icon-only: center within the main button, *not* within the
        # main button plus its separate dropdown arrow.
        painter.drawPixmap(
            bounds.center().x() - icon_width // 2,
            bounds.center().y() - icon_height // 2,
            icon,
        )
        return

    available_text = max(1, bounds.width() - icon_width - gap)
    actual_text_width = min(
        available_text, max(metrics.horizontalAdvance(line) for line in lines)
    )
    block_width = icon_width + gap + actual_text_width
    icon_x = bounds.center().x() - block_width // 2
    painter.drawPixmap(
        icon_x, bounds.center().y() - icon_height // 2, icon
    )
    text_x = icon_x + icon_width + gap
    text_y = bounds.center().y() - text_height // 2
    for i, line in enumerate(lines):
        painter.drawText(
            QRect(text_x, text_y + i * line_height, actual_text_width, line_height),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            metrics.elidedText(
                line, Qt.TextElideMode.ElideRight, actual_text_width,
            ),
        )


class CenteredIconToolButton(QToolButton):
    """Toolbutton that centers icons beside, above, or without labels."""

    def paintEvent(self, event) -> None:
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.icon = QIcon()
        option.text = ""
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        main_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ToolButton, option,
            QStyle.SubControl.SC_ToolButton, self,
        )
        painter.setPen(self.palette().color(
            QPalette.ColorRole.ButtonText if self.isEnabled()
            else QPalette.ColorRole.PlaceholderText
        ))
        _draw_content(
            painter, self, main_rect,
            under=self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon,
        )


class CenteredIconPushButton(QPushButton):
    """Pushbutton with the same centered icon arrangement as the toolbuttons."""

    def paintEvent(self, event) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.icon = QIcon()
        option.text = ""
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        rect = self.style().subElementRect(
            QStyle.SubElement.SE_PushButtonContents, option, self,
        )
        painter.setPen(self.palette().color(
            QPalette.ColorRole.ButtonText if self.isEnabled()
            else QPalette.ColorRole.PlaceholderText
        ))
        _draw_content(painter, self, rect)
