"""Buttons with consistently centered icons and bounded, wrapped labels."""
from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QFont, QFontMetrics, QIcon, QPalette
from PyQt6.QtWidgets import (
    QPushButton, QStyle, QStyleOptionButton, QStyleOptionToolButton,
    QStylePainter, QToolButton,
)


def _wrap_title(text: str, metrics: QFontMetrics, width: int) -> list[str]:
    """Wrap an action title to at most two lines, eliding the last one."""
    words = text.split()
    if not words:
        return []
    lines = []
    while words and len(lines) < 2:
        line = words.pop(0)
        while words and metrics.horizontalAdvance(line + " " + words[0]) <= width:
            line += " " + words.pop(0)
        if words and len(lines) == 1:
            line += " " + " ".join(words)
            words.clear()
        lines.append(metrics.elidedText(line, Qt.TextElideMode.ElideRight, width))
    return lines


def _draw_content(painter, button, rect: QRect, *, under: bool = False) -> None:
    """Paint inside the clickable rect, never into the split-menu arrow."""
    bounds = rect.adjusted(7, 4, -7, -4)
    if bounds.width() <= 0 or bounds.height() <= 0:
        return
    icon = button.icon().pixmap(
        button.iconSize(),
        QIcon.Mode.Normal if button.isEnabled() else QIcon.Mode.Disabled,
    )
    ratio = max(1.0, icon.devicePixelRatioF())
    iw, ih = round(icon.width() / ratio), round(icon.height() / ratio)
    text = (
        "" if isinstance(button, QToolButton)
        and button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        else button.text()
    )
    title, *remainder = text.split("\n", 1)
    filename = remainder[0] if remainder else ""
    enabled_role = (
        QPalette.ColorRole.ButtonText if button.isEnabled()
        else QPalette.ColorRole.PlaceholderText
    )
    painter.save()
    painter.setClipRect(bounds)
    painter.setPen(button.palette().color(enabled_role))
    try:
        if not title:
            painter.drawPixmap(
                bounds.center().x() - iw // 2,
                bounds.center().y() - ih // 2, icon,
            )
            return

        base_font = button.font()
        if under:
            metrics = QFontMetrics(base_font)
            label = metrics.elidedText(
                title, Qt.TextElideMode.ElideRight, bounds.width(),
            )
            block_height = ih + 4 + metrics.height()
            y = bounds.center().y() - block_height // 2
            painter.drawPixmap(bounds.center().x() - iw // 2, y, icon)
            painter.drawText(
                QRect(bounds.left(), y + ih + 4, bounds.width(), metrics.height()),
                Qt.AlignmentFlag.AlignCenter, label,
            )
            return

        title_font = QFont(base_font)
        title_font.setBold(True)
        title_metrics = QFontMetrics(title_font)
        small_font = QFont(base_font)
        small_font.setPointSizeF(max(7.0, base_font.pointSizeF() * .90))
        small_metrics = QFontMetrics(small_font)

        gap = 7
        text_x = bounds.left() + iw + gap
        width = max(1, bounds.right() - text_x + 1)
        title_lines = _wrap_title(title, title_metrics, width)
        title_h = len(title_lines) * title_metrics.height()
        file_h = small_metrics.height() if filename else 0
        block_h = title_h + file_h
        top = bounds.center().y() - block_h // 2

        # The icon aligns with the complete text block, not its first line.
        painter.drawPixmap(
            bounds.left(), bounds.center().y() - ih // 2, icon,
        )
        painter.setFont(title_font)
        for i, line in enumerate(title_lines):
            painter.drawText(
                QRect(text_x, top + i * title_metrics.height(),
                      width, title_metrics.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )
        if filename:
            painter.setFont(small_font)
            painter.drawText(
                QRect(text_x, top + title_h, width, file_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                small_metrics.elidedText(
                    filename, Qt.TextElideMode.ElideLeft, width,
                ),
            )
    finally:
        painter.restore()


class CenteredIconToolButton(QToolButton):
    """Toolbutton with a centered icon in labeled, compact and split modes."""

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
        _draw_content(
            painter, self, main_rect,
            under=self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon,
        )


class CenteredIconPushButton(QPushButton):
    """Pushbutton with the same icon/text positioning as the toolbuttons."""

    def paintEvent(self, event) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.icon = QIcon()
        option.text = ""
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        content_rect = self.style().subElementRect(
            QStyle.SubElement.SE_PushButtonContents, option, self,
        )
        _draw_content(painter, self, content_rect)
