"""Live SCF iteration tracking and visualization for the run monitor.

The tracker only consumes output already delivered by the run's output listener.
It does not inspect result files or block the GUI thread on a remote filesystem.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


# Native SPR-KKR iteration summary, e.g.:
# 6 ERR 0.211E-02 0.208E-01 EF 0.76199 0.00307 D 14.537 M 2.1740 0.0536
_SCF_LINE = re.compile(
    r"^\s*(?P<iteration>\d+)\s+ERR\s+"
    r"(?P<potential>\S+)\s+(?P<field>\S+)\s+EF\s+"
    r"(?P<fermi>\S+)(?=\s|$)",
    flags=re.IGNORECASE,
)
_INFO_LINE = re.compile(r"^\s*(?:INFO:?\s+)?Iteration\s+\d+\s+error\b", re.I)
_PARTIAL_LINE_LIMIT = 8192
_MAX_ITERATIONS = 2000


@dataclass(frozen=True)
class ScfIteration:
    number: int
    fermi_energy: float
    error: float


def _fortran_float(value: str) -> float:
    return float(value.replace("D", "E").replace("d", "e"))


def parse_scf_iteration(line: str) -> ScfIteration | None:
    """Read the native ERR/EF record; ignore incomplete or invalid records.

    The plotted error is the larger RMS component (potential V or field B),
    since both must satisfy the SCF tolerance for convergence.
    """
    match = _SCF_LINE.match(line)
    if match is None:
        return None
    try:
        potential = _fortran_float(match["potential"])
        field = _fortran_float(match["field"])
        fermi = _fortran_float(match["fermi"])
    except ValueError:
        return None
    if not all(map(isfinite, (potential, field, fermi))):
        return None
    return ScfIteration(
        number=int(match["iteration"]),
        fermi_energy=fermi,
        error=max(abs(potential), abs(field)),
    )


class ScfProgressTracker:
    """Accumulate complete SCF lines across arbitrary stdout chunk boundaries."""

    def __init__(self) -> None:
        self.iterations: list[ScfIteration] = []
        self.last_info_line = "Waiting for SCF iteration output…"
        self._partial = ""

    def feed(self, text: str, kind: str = "out") -> bool:
        """Return True if the last line or the iteration series changed."""
        if kind.lower() in {"err", "stderr"} or not text:
            return False
        # Keep only a bounded unfinished line; completed lines are processed below.
        lines = (self._partial + text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self._partial = lines.pop()[-_PARTIAL_LINE_LIMIT:]
        changed = False
        for line in lines:
            clean = line.strip()
            iteration = parse_scf_iteration(clean)
            if iteration is not None:
                if self.iterations and iteration.number < self.iterations[-1].number:
                    # Another SCF cycle in the same output: do not connect
                    # its curve with the previous cycle.
                    self.iterations.clear()
                if self.iterations and iteration.number == self.iterations[-1].number:
                    self.iterations[-1] = iteration
                else:
                    self.iterations.append(iteration)
                    if len(self.iterations) > _MAX_ITERATIONS:
                        del self.iterations[: len(self.iterations) - _MAX_ITERATIONS]
                self.last_info_line = clean
                changed = True
            elif clean and (kind.lower() == "info" or _INFO_LINE.match(clean) or clean.startswith("INFO:")):
                # Also support backends that forward the formatted INFO summary.
                self.last_info_line = clean
                changed = True
        return changed


class ScfProgressPage(QWidget):
    """Qt INFO page: latest iteration and two live, automatically scaled plots."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Import only for an SCF monitor; non-SCF runs need no plotting canvas.
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        self.tracker = ScfProgressTracker()
        layout = QVBoxLayout(self)
        title = QLabel("Latest INFO line", self)
        layout.addWidget(title)

        self.last_info = QLabel(self.tracker.last_info_line, self)
        self.last_info.setWordWrap(True)
        self.last_info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.last_info)

        self.figure = Figure(figsize=(8, 5), constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, 1)

        self.fermi_axis, self.error_axis = self.figure.subplots(2, 1, sharex=True)
        self.fermi_line, = self.fermi_axis.plot([], [], marker="o", markersize=3)
        self.error_line, = self.error_axis.plot([], [], marker="o", markersize=3)
        self.fermi_axis.set_ylabel("Fermi energy (Ry)")
        self.error_axis.set_ylabel("Convergence error\nmax RMS(V, B)")
        self.error_axis.set_xlabel("SCF iteration")
        self.error_axis.set_yscale("log")
        for axis in (self.fermi_axis, self.error_axis):
            axis.grid(True, alpha=0.3)

        # Coalesce potentially frequent output callbacks into a single redraw.
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(120)
        self._redraw_timer.timeout.connect(self._redraw)

    def consume(self, text: str, kind: str) -> None:
        if self.tracker.feed(text, kind):
            self.last_info.setText(self.tracker.last_info_line)
            if not self._redraw_timer.isActive():
                self._redraw_timer.start()

    def _redraw(self) -> None:
        iterations = self.tracker.iterations
        if not iterations:
            return
        x = [item.number for item in iterations]
        self.fermi_line.set_data(x, [item.fermi_energy for item in iterations])
        # A zero reported error means convergence, not a valid log coordinate.
        self.error_line.set_data(
            x, [max(item.error, 1e-16) for item in iterations]
        )
        for axis in (self.fermi_axis, self.error_axis):
            axis.relim()
            axis.autoscale_view()
        self.canvas.draw_idle()
