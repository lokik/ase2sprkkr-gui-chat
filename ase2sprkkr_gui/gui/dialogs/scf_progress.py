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
_SCF_TOL_LINE = re.compile(r"^\s*SCF-tol\s*:\s*(\S+)", re.I)
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


def _positive_tolerance(value: object) -> float | None:
    """Return a valid positive tolerance, or None if it is unavailable."""
    try:
        result = _fortran_float(str(value))
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) and result > 0 else None


def scf_tolerance_from_parameters(parameters: object) -> float | None:
    """Read the effective SCF.TOL (including its default) from input parameters."""
    try:
        value = parameters.SCF.TOL()
    except (AttributeError, TypeError, ValueError):
        return None
    return _positive_tolerance(value)


class ScfProgressTracker:
    """Accumulate complete SCF lines across arbitrary stdout chunk boundaries."""

    def __init__(self, target_error: float | None = None) -> None:
        self.iterations: list[ScfIteration] = []
        self.target_error = _positive_tolerance(target_error)
        self.last_info_line = "Preparing first iteration..."
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
            tolerance_line = _SCF_TOL_LINE.match(clean)
            if tolerance_line is not None:
                target = _positive_tolerance(tolerance_line.group(1))
                if target is not None and target != self.target_error:
                    # The executed run's actual value takes precedence over
                    # a previously supplied input-parameters value.
                    self.target_error = target
                    changed = True
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

    def __init__(
        self, parent: QWidget | None = None, *, target_error: float | None = None
    ) -> None:
        super().__init__(parent)
        # Import only for an SCF monitor; non-SCF runs need no plotting canvas.
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        self.tracker = ScfProgressTracker(target_error)
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
        self.target_line = None
        for axis in (self.fermi_axis, self.error_axis):
            axis.grid(True, alpha=0.3)
        # Show the convergence target before the first iteration if known.
        self._sync_target()
        self._set_error_limits()

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

    def _sync_target(self) -> None:
        target = self.tracker.target_error
        if target is None:
            return
        if self.target_line is None:
            self.target_line = self.error_axis.axhline(
                target, linestyle="--", linewidth=1.2, color="tab:red",
                label=f"Target {target:.2g}",
            )
        else:
            self.target_line.set_ydata([target, target])
            self.target_line.set_label(f"Target {target:.2g}")
        self.error_axis.legend(loc="upper right")

    def _set_error_limits(self) -> None:
        """Keep the target and every measured error visible on the log axis."""
        values = [max(item.error, 1e-16) for item in self.tracker.iterations]
        if self.tracker.target_error is not None:
            values.append(self.tracker.target_error)
        if values:
            self.error_axis.set_ylim(min(values) / 2, max(values) * 2)

    def _redraw(self) -> None:
        self._sync_target()
        iterations = self.tracker.iterations
        if iterations:
            x = [item.number for item in iterations]
            self.fermi_line.set_data(x, [item.fermi_energy for item in iterations])
            # A zero reported error means convergence, not a valid log coordinate.
            self.error_line.set_data(
                x, [max(item.error, 1e-16) for item in iterations]
            )
            for axis in (self.fermi_axis, self.error_axis):
                axis.relim()
                axis.autoscale_view()
        self._set_error_limits()
        self.canvas.draw_idle()
