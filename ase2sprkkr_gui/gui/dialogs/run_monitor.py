"""Generic UI for monitoring one calculation runner."""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from shutil import copyfileobj
from tempfile import SpooledTemporaryFile
from threading import Thread
from typing import Any

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QIcon, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr_gui.gui.application.execution_cancellation import RunCancellation
from ase2sprkkr_gui.gui.dialogs.scf_progress import ScfProgressPage
from ase2sprkkr_gui.gui.dialogs.jxc_progress import JxcProgressPage
from ase2sprkkr_gui.gui.dialogs.dos_progress import DosProgressPage
from ase2sprkkr_gui.gui.dialogs.bsf_progress import BsfProgressPage
from ase2sprkkr_gui.gui.dialogs.spleed_progress import SpleedProgressPage
from ase2sprkkr_gui.gui.dialogs.arpes_progress import ArpesProgressPage
from ase2sprkkr_gui.gui.dialogs.xas_progress import XasProgressPage
from ase2sprkkr_gui.gui.misc.resources import icon_path


# One dispatch table for native parser-driven INFO pages. Only pages with
# task-specific settings need constructor arguments (below).
_INFO_PAGE_FACTORIES: dict[str, Callable[..., QWidget]] = {
    "scf": ScfProgressPage,
    "jxc": JxcProgressPage,
    "dos": DosProgressPage,
    "bsf": BsfProgressPage,
    "bsfek": BsfProgressPage,
    "bsfkk": BsfProgressPage,
    "spleed": SpleedProgressPage,
    "arpes": ArpesProgressPage,
    "xas": XasProgressPage,
}


logger = logging.getLogger(__name__)

MAX_OUTPUT_BLOCKS = 4096
MAX_LOGICAL_LINE_LENGTH = 4096


def state_label(state: Any) -> str:
    value = getattr(state, "value", state)
    return str(value).replace("_", " ").capitalize()


class RunMonitorWorker(QObject):
    """Formal Qt adapter contract consumed by :class:`RunMonitorWindow`."""

    output = pyqtSignal(str, str)
    status = pyqtSignal(str)
    warning = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(object)
    cancelled = pyqtSignal()

    @pyqtSlot()
    def run(self) -> None:
        raise NotImplementedError


class RunMonitorWindow(QDialog):
    """Display one calculation worker without knowing how its runner was created.

    The caller owns launch/attach/persistence semantics. ``worker_factory``
    returns a :class:`RunMonitorWorker`; the worker registers its concrete
    runner in the supplied :class:`RunCancellation`.
    """

    refreshed = pyqtSignal(str)

    def __init__(
        self,
        worker_factory: Callable[[RunCancellation], RunMonitorWorker],
        parent: QWidget | None = None,
        *,
        title: str = "Calculation Run",
        header: str | None = None,
        header_fields: Sequence[tuple[str, str]] | None = None,
        initial_status: str = "",
        job_id: str | None = None,
        warning_title: str = "Warning",
        on_finished: Callable[[Any], None] | None = None,
        background_thread_factory: Callable[..., Any] = Thread,
        autostart: bool = True,
        observation_only: bool = False,
        task_name: str | None = None,
        scf_tolerance: float | None = None,
        dos_total_points: int | None = None,
        xas_total_points: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.resize(900, 600)

        self._worker_factory = worker_factory
        self._finished_callback = on_finished
        self._warning_title = warning_title
        self._background_thread_factory = background_thread_factory
        self._cancellation = RunCancellation(observation_only=observation_only)
        self._thread: QThread | None = None
        self._worker: RunMonitorWorker | None = None
        self._close_requested = False
        self._output_line_length = 0
        # The on-screen log is bounded; keep the complete, unwrapped stream
        # in a spooled file for copying and saving without unbounded RAM use.
        self._output_archive = SpooledTemporaryFile(
            max_size=1024 * 1024, mode="w+t", encoding="utf-8", newline=""
        )
        self.refreshed.connect(self._set_status)

        layout = QVBoxLayout(self)
        # Keep labels aligned independently from their variable-length values.
        # Two label/value groups per row make room for the result tabs below.
        self._header_grid = QGridLayout()
        self._header_grid.setContentsMargins(0, 0, 0, 0)
        self._header_grid.setHorizontalSpacing(10)
        self._header_grid.setVerticalSpacing(3)
        self._header_grid.setColumnStretch(1, 3)
        self._header_grid.setColumnStretch(3, 2)

        def add_field(label: str, value: str, index: int) -> tuple[QLabel, QLabel]:
            row, pair = divmod(index, 2)
            caption = QLabel(f"{label}:", self)
            caption.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            content = QLabel(str(value), self)
            content.setWordWrap(True)
            content.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._header_grid.addWidget(caption, row, pair * 2)
            self._header_grid.addWidget(content, row, pair * 2 + 1)
            return caption, content

        if header_fields is None:
            # Backwards-compatible free-form headers supplied by other callers.
            fields = []
            for line in (header or "").splitlines():
                if not line.strip():
                    continue
                if ":" in line:
                    label, value = line.split(":", 1)
                    fields.append((label.strip(), value.strip()))
                else:
                    fields.append(("Details", line.strip()))
        else:
            fields = list(header_fields)

        for index, (label, value) in enumerate(fields):
            add_field(label, value, index)

        # Job identity and state can arrive or change while the window is open.
        # Place both on the same final row, avoiding two more full-width lines.
        dynamic_row = (len(fields) + 1) // 2
        self._job_id_title, self._job_id_label = add_field(
            "Job ID", "", dynamic_row * 2
        )
        self._status_title, self._status = add_field(
            "Status", initial_status, dynamic_row * 2 + 1
        )
        self.set_job_id(job_id)
        layout.addLayout(self._header_grid)

        self._output = QPlainTextEdit(self)
        self._output.setReadOnly(True)
        self._output.setMaximumBlockCount(MAX_OUTPUT_BLOCKS)
        task = (task_name or "").strip().lower()
        # Only these specialized pages accept input-derived progress settings.
        page_options = {
            "scf": {"target_error": scf_tolerance},
            "dos": {"total_points": dos_total_points},
            "xas": {"total_points": xas_total_points},
        }
        page_factory = _INFO_PAGE_FACTORIES.get(task)
        self._info_page = (
            page_factory(self, **page_options.get(task, {}))
            if page_factory is not None else None
        )
        self._consume_info = (
            self._info_page.consume if self._info_page is not None else None
        )
        # Every monitor can gain Results and Plot tabs after completion.
        # Preserve INFO when the task supplies a live progress page.
        self._tabs = QTabWidget(self)
        if self._info_page is not None:
            self._tabs.addTab(self._info_page, "INFO")
        self._tabs.addTab(self._output, "Output")
        layout.addWidget(self._tabs, 1)
        self._completed_pages = None

        buttons = QHBoxLayout()
        self._stop_btn = QPushButton("Stop monitoring" if observation_only else "Stop", self)
        self._stop_btn.setToolTip(
            "Stop following output without terminating the calculation."
            if observation_only else "Request termination of the calculation process."
        )
        self._stop_btn.clicked.connect(self._on_stop)
        buttons.addWidget(self._stop_btn)

        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.setToolTip(
            "Refresh the backend run state without blocking the GUI."
        )
        self._refresh_btn.clicked.connect(self._on_refresh)
        buttons.addWidget(self._refresh_btn)

        self._copy_output_btn = QPushButton("Copy output", self)
        self._copy_output_btn.setIcon(
            QIcon.fromTheme("edit-copy", QIcon(str(icon_path("copy-output.svg"))))
        )
        self._copy_output_btn.setToolTip("Copy the complete command output to the clipboard.")
        self._copy_output_btn.setEnabled(False)
        self._copy_output_btn.clicked.connect(self._on_copy_output)
        buttons.addWidget(self._copy_output_btn)

        self._save_output_btn = QPushButton("Save output as…", self)
        self._save_output_btn.setIcon(
            QIcon.fromTheme("document-save-as", QIcon(str(icon_path("save-output-as.svg"))))
        )
        self._save_output_btn.setToolTip("Save the complete command output to a text file.")
        self._save_output_btn.setEnabled(False)
        self._save_output_btn.clicked.connect(self._on_save_output)
        buttons.addWidget(self._save_output_btn)
        buttons.addStretch(1)

        self._close_btn = QPushButton("Close", self)
        self._close_btn.clicked.connect(self.close)
        buttons.addWidget(self._close_btn)
        layout.addLayout(buttons)

        if autostart:
            self.start()

    def set_job_id(self, job_id: str | None) -> None:
        """Show the stable persistent run ID once one is known."""
        self._job_id_label.setText(job_id or "")
        self._job_id_label.setVisible(bool(job_id))
        self._job_id_title.setVisible(bool(job_id))

    @property
    def runner(self) -> Any | None:
        return self._cancellation.runner

    def showEvent(self, event) -> None:  # type: ignore[override]
        # A monitor hidden during execution may be reopened before the worker
        # finishes. Its former close request must not suppress result tabs or
        # destroy the now-visible window when that worker completes.
        self._close_requested = False
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._thread is not None and self._thread.isRunning():
            self._close_requested = True
            self.hide()
            event.ignore()
            return
        self._output_archive.close()
        super().closeEvent(event)

    def start(self) -> None:
        """Start the worker exactly once after the caller has established ownership."""
        if self._thread is not None:
            raise RuntimeError("run monitor worker has already been started")
        self._start_worker()

    def _start_worker(self) -> None:
        thread = QThread(self)
        worker = self._worker_factory(self._cancellation)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.output.connect(self._append_output)
        worker.status.connect(self._set_status)
        worker.warning.connect(self._on_warning)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_finished)
        worker.cancelled.connect(self._on_cancelled)

        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        worker.cancelled.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(self._thread_finished)

        self._thread = thread
        self._worker = worker
        thread.start()

    @pyqtSlot()
    def _thread_finished(self) -> None:
        if self._close_requested:
            self.close()

    @pyqtSlot(str)
    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def _bounded_output(self, text: str) -> str:
        parts: list[str] = []
        lines = text.split("\n")
        for index, line in enumerate(lines):
            while len(line) > MAX_LOGICAL_LINE_LENGTH - self._output_line_length:
                available = MAX_LOGICAL_LINE_LENGTH - self._output_line_length
                if available:
                    parts.append(line[:available])
                    line = line[available:]
                parts.append("\n")
                self._output_line_length = 0

            parts.append(line)
            self._output_line_length += len(line)
            if index < len(lines) - 1:
                parts.append("\n")
                self._output_line_length = 0

        return "".join(parts)

    @pyqtSlot(str, str)
    def _append_output(self, text: str, kind: str) -> None:
        if text:
            self._output_archive.write(text)
            self._copy_output_btn.setEnabled(True)
            self._save_output_btn.setEnabled(True)
        if self._consume_info is not None:
            self._consume_info(text, kind)
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        if kind in {"err", "stderr"}:
            fmt.setForeground(QColor("red"))
            fmt.setFontWeight(QFont.Weight.Bold)
        cursor.setCharFormat(fmt)
        cursor.insertText(self._bounded_output(text))
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    @pyqtSlot()
    def _on_copy_output(self) -> None:
        output = self._output_archive
        output.flush()
        output.seek(0)
        try:
            QApplication.clipboard().setText(output.read())
        finally:
            output.seek(0, 2)

    @pyqtSlot()
    def _on_save_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Command Output As",
            "command-output.txt",
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return

        output = self._output_archive
        output.flush()
        output.seek(0)
        try:
            with open(path, "w", encoding="utf-8", newline="") as destination:
                copyfileobj(output, destination)
        except OSError as exc:
            QMessageBox.warning(self, "Could Not Save Output", f"Could not save command output:\n{exc}")
        finally:
            output.seek(0, 2)

    @pyqtSlot()
    def _on_stop(self) -> None:
        self._stop_btn.setEnabled(False)
        self._cancellation.request()

    @pyqtSlot()
    def _on_refresh(self) -> None:
        runner = self._cancellation.runner
        if runner is None:
            return

        def refresh() -> None:
            try:
                state = runner.state
            except Exception as exc:  # noqa: BLE001 - backend status boundary
                logger.exception("Failed to refresh calculation run state")
                self.refreshed.emit(f"Refresh failed: {exc}")
                return
            self.refreshed.emit(state_label(state))

        self._background_thread_factory(
            target=refresh,
            name="run-refresh",
            daemon=True,
        ).start()

    @pyqtSlot(str)
    def _on_warning(self, message: str) -> None:
        QMessageBox.warning(self, self._warning_title, message)

    @pyqtSlot(str)
    def _on_error(self, message: str) -> None:
        self._append_output(f"\n[ERROR] {message}\n", "stderr")
        self._stop_btn.setEnabled(False)

    @pyqtSlot()
    def _on_cancelled(self) -> None:
        self._stop_btn.setEnabled(False)

    @pyqtSlot(object)
    def _on_finished(self, result: object) -> None:
        try:
            if self._finished_callback is not None:
                self._finished_callback(result)
            # Adoption has completed, so output files and provenance are ready.
            native_result = getattr(result, "result", result)
            if hasattr(native_result, "output_values") and not self._close_requested:
                try:
                    from ase2sprkkr_gui.gui.dialogs.run_results import CompletedRunPages

                    self._completed_pages = CompletedRunPages(self._tabs, self)
                    self._completed_pages.show_result(native_result)
                except Exception:  # noqa: BLE001 - presentation must not invalidate a run
                    logger.exception("Could not display completed calculation results")
        finally:
            self._stop_btn.setEnabled(False)
