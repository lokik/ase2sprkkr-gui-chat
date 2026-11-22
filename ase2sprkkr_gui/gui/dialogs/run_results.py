"""Post-calculation results and reusable, on-demand plot pages."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from ase2sprkkr_gui.gui.application.result_presentation import present_result
from ase2sprkkr_gui.gui.dialogs.object_view import execute_value_action
from ase2sprkkr_gui.gui.widgets.result_actions import ResultActionsWidget


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


class CompletedRunPages(QObject):
    """Add results and file-specific plots only after calculation completion.

    The Results browser is selected immediately; heavyweight figures are
    deferred until their individual plot tab is first selected.
    """

    def __init__(self, tabs: QTabWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._tabs = tabs
        self._parent = parent
        self._pending_plots: set[ResultPlotPage] = set()
        self.results_widget: ResultActionsWidget | None = None
        tabs.currentChanged.connect(self._tab_changed)

    def show_result(self, result: Any) -> None:
        presentation = present_result(result)
        if presentation is None:
            return

        browser = ResultActionsWidget(
            lambda value, action: execute_value_action(value, action, self._parent),
            show_values_without_actions=False,
            show_history_button=False,
            show_assignment_controls=False,
            single_result=True,
            parent=self._tabs,
        )
        browser.set_result(presentation)
        self.results_widget = browser
        self._tabs.addTab(browser, "Results")

        for entry in presentation.values:
            if "plot" not in entry.actions or entry.value is None:
                continue
            value = entry.value
            try:
                filename = Path(value.path()).name
                full_path = str(value.path())
            except (AttributeError, TypeError, ValueError, OSError):
                filename = entry.name
                full_path = entry.name

            page = ResultPlotPage(value, self._tabs)
            index = self._tabs.addTab(page, filename)
            self._tabs.setTabToolTip(index, full_path)
            self._pending_plots.add(page)

        self._tabs.setCurrentWidget(browser)

    def _tab_changed(self, index: int) -> None:
        page = self._tabs.widget(index)
        if page not in self._pending_plots:
            return
        # Let Qt display the page before plotting potentially large results.
        QTimer.singleShot(0, lambda p=page: self._render_plot(p))

    def _render_plot(self, page: ResultPlotPage) -> None:
        if page not in self._pending_plots or self._tabs.currentWidget() is not page:
            return
        self._pending_plots.remove(page)
        page.render()
