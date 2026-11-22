"""Post-calculation results and reusable, on-demand plot pages."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QToolButton, QVBoxLayout, QWidget

from ase2sprkkr_gui.gui.application.result_presentation import present_result
from ase2sprkkr_gui.gui.dialogs.object_view import execute_value_action
from ase2sprkkr_gui.gui.widgets.result_actions import result_action_icon, result_action_tooltip


logger = logging.getLogger(__name__)


class ResultPlotPage(QWidget):
    """A single output file's plot, shared by every task and rendered on demand.

    One parsed result may create multiple Matplotlib figures. In that case
    the figures become nested tabs in this same file-specific plot page.
    """

    def __init__(self, value: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self._value = value
        self._rendered = False
        self._layout = QVBoxLayout(self)
        label = QLabel("Plot will be generated when this page is opened.", self)
        label.setWordWrap(True)
        self._layout.addWidget(label)
        self._layout.addStretch(1)

    def render(self) -> None:
        if self._rendered:
            return
        self._rendered = True
        layout = self._layout
        plt = None
        before: set[int] = set()
        figures = []
        error = None
        try:
            from matplotlib import pyplot as plt

            before = set(plt.get_fignums())
            # Native plotting supports this argument for all plottable files,
            # and does not call pyplot.show().
            self._value.parsed_file().plot(show=False)
            figures = [plt.figure(n) for n in plt.get_fignums() if n not in before]
            if not figures:
                raise RuntimeError("Plotting produced no Matplotlib figure.")
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
        except Exception as exc:  # noqa: BLE001 - scientific plotting boundary
            error = exc
            logger.exception("Could not render result plot")
        finally:
            # Hand ownership to the embedded Qt canvas rather than pyplot's
            # figure manager, even if plotting raises partway through.
            if plt is not None:
                for number in set(plt.get_fignums()) - before:
                    plt.close(number)

        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        if error is not None:
            message = QLabel(f"Could not display this plot:\n{error}", self)
            message.setWordWrap(True)
            layout.addWidget(message)
            layout.addStretch(1)
            return

        if len(figures) > 1:
            figure_tabs = QTabWidget(self)
            layout.addWidget(figure_tabs, 1)
        for number, figure in enumerate(figures, 1):
            holder = QWidget(self)
            holder_layout = QVBoxLayout(holder)
            canvas = FigureCanvasQTAgg(figure)
            holder_layout.addWidget(NavigationToolbar2QT(canvas, holder))
            holder_layout.addWidget(canvas, 1)
            if len(figures) > 1:
                figure_tabs.addTab(holder, f"Figure {number}")
            else:
                layout.addWidget(holder, 1)
            canvas.draw_idle()


class ResultFilePage(QWidget):
    """One output value/file, its available actions, and an optional inline plot."""

    def __init__(
        self, entry: Any, parent: QWidget, action_parent: QWidget,
        *, file_warning: str | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.plot_page: ResultPlotPage | None = None

        detail = f"{entry.name}: {entry.summary}" if entry.summary else entry.name
        heading = QLabel(detail, self)
        heading.setWordWrap(True)
        heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(heading)
        if entry.info:
            description = QLabel(entry.info, self)
            description.setWordWrap(True)
            description.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(description)
        for detail_warning in (file_warning, entry.warning):
            if detail_warning:
                warning = QLabel(detail_warning, self)
                warning.setWordWrap(True)
                layout.addWidget(warning)

        if entry.actions and entry.value is not None:
            buttons = QHBoxLayout()
            for action in entry.actions:
                button = QToolButton(self)
                button.setAutoRaise(True)
                button.setIcon(result_action_icon(self.style(), action))
                button.setToolTip(result_action_tooltip(entry.value, action))
                button.clicked.connect(
                    lambda _checked=False, v=entry.value, a=action:
                        execute_value_action(v, a, action_parent)
                )
                buttons.addWidget(button)
            buttons.addStretch(1)
            layout.addLayout(buttons)

        if "plot" in entry.actions and entry.value is not None:
            self.plot_page = ResultPlotPage(entry.value, self)
            layout.addWidget(self.plot_page, 1)
        else:
            layout.addStretch(1)

    def render(self) -> None:
        if self.plot_page is not None:
            self.plot_page.render()


def _filename_for_entry(entry: Any) -> tuple[str, str]:
    """Use the actual file path when provided, otherwise a presentation label."""
    value = entry.value
    if value is not None:
        try:
            raw_path = value.path()
            if raw_path:
                path = str(raw_path)
                return Path(path).name, path
        except (AttributeError, TypeError, ValueError, OSError):
            pass
    # Some non-plottable output values expose the filename as their summary.
    summary = entry.summary.strip()
    if summary and not summary.startswith("<") and (
        "." in Path(summary).name and "\n" not in summary
    ):
        return Path(summary).name, summary
    return entry.name, entry.name


class CompletedRunPages(QObject):
    """After completion, add one file-named tab per output, without a Results tab.

    Each tab uses the same ResultFilePage, which contains an optional reusable,
    lazily rendered plot. Select the first output as soon as it becomes available.
    """

    def __init__(self, tabs: QTabWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._tabs = tabs
        self._parent = parent
        self._pending_plots: set[ResultFilePage] = set()
        self.file_pages: list[ResultFilePage] = []
        tabs.currentChanged.connect(self._tab_changed)

    def show_result(self, result: Any) -> None:
        presentation = present_result(result, result_only=True)
        if presentation is None:
            return
        first_page = None
        for entry in presentation.values:
            filename, path = _filename_for_entry(entry)
            page = ResultFilePage(
                entry, self._tabs, self._parent,
                file_warning=presentation.file_warning,
            )
            index = self._tabs.addTab(page, filename)
            self._tabs.setTabToolTip(index, path)
            self.file_pages.append(page)
            if page.plot_page is not None:
                self._pending_plots.add(page)
            if first_page is None:
                first_page = page

        if first_page is not None:
            self._tabs.setCurrentWidget(first_page)

    def _tab_changed(self, index: int) -> None:
        page = self._tabs.widget(index)
        if page not in self._pending_plots:
            return
        # Wait for the tab to become visible before rendering a heavy plot.
        QTimer.singleShot(0, lambda p=page: self._render_plot(p))

    def _render_plot(self, page: ResultFilePage) -> None:
        if page not in self._pending_plots or self._tabs.currentWidget() is not page:
            return
        self._pending_plots.remove(page)
        page.render()
