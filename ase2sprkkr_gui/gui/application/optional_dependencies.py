"""Optional ASE2SPRKKR features installed only after explicit user approval.

The installed ase2sprkkr distribution is the source of truth for extra
requirements. Do not reinstall or upgrade the core distribution at runtime.
"""
from __future__ import annotations

import importlib
import importlib.util
from importlib import metadata
import os
from pathlib import Path
import shutil
import site
import sys
import sysconfig

from packaging.markers import default_environment
from packaging.requirements import Requirement
from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


OPTIONAL_MODULES = {
    "plot": ("matplotlib", "scipy", "skimage"),
    "nexus": ("h5py", "nexusformat"),
    "mendeleev": ("mendeleev",),
}
EXTRA_LABELS = {
    "plot": "SPR-KKR result plotting",
    "nexus": "NeXus export",
    "mendeleev": "Detailed chemical-element information",
}


def missing_modules(extra: str) -> tuple[str, ...]:
    """Check for modules without importing large scientific libraries."""
    return tuple(
        name for name in OPTIONAL_MODULES[extra]
        if importlib.util.find_spec(name) is None
    )


def requirements_for_extra(extra: str) -> tuple[str, ...]:
    """Resolve the direct requirements of a declared ase2sprkkr extra."""
    if extra not in OPTIONAL_MODULES:
        raise ValueError(f"Unknown optional feature: {extra}")
    dist = metadata.distribution("ase2sprkkr")
    environment = default_environment()
    environment["extra"] = extra
    requirements = []
    for item in dist.requires or ():
        requirement = Requirement(item)
        if requirement.marker is None or "extra" not in str(requirement.marker):
            continue
        if requirement.marker.evaluate(environment):
            # The marker applies to the parent distribution. Its meaning is
            # lost when invoking pip with bare requirement strings.
            requirements.append(str(requirement).split(";", 1)[0].strip())
    if not requirements:
        raise ValueError(
            f"The installed ase2sprkkr distribution does not declare [{extra}]. "
            "Update ase2sprkkr before installing this optional feature."
        )
    return tuple(requirements)


def _in_environment() -> bool:
    return (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or os.environ.get("CONDA_PREFIX") == sys.prefix
    )


def pip_command(extra: str) -> tuple[str, ...]:
    """Choose pip tied to the interpreter running the GUI.

    User installations are not supported inside venvs, Conda, or externally
    managed Python installations. Never pass --break-system-packages.
    """
    requirements = requirements_for_extra(extra)
    command = [sys.executable, "-m", "pip", "install"]
    if not _in_environment():
        managed = Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED"
        if managed.is_file():
            raise RuntimeError(
                "This Python installation is externally managed (PEP 668). "
                "Install the GUI in a virtual environment to manage optional features."
            )
        if site.ENABLE_USER_SITE is not True:
            raise RuntimeError(
                "User-site installations are disabled in this interpreter. "
                "Use a virtual environment instead."
            )
        command.append("--user")
    if importlib.util.find_spec("pip") is None:
        uv = shutil.which("uv")
        if _in_environment() and uv:
            return (uv, "pip", "install", "--python", sys.executable, *requirements)
        raise RuntimeError(
            "pip is unavailable in this Python interpreter. "
            "Install pip or run the GUI in a uv-managed virtual environment."
        )
    return tuple(command + list(requirements))


class _InstallDialog(QDialog):
    """Run a confirmed installation without blocking the GUI event loop."""

    def __init__(self, extra: str, command: tuple[str, ...], parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle(f"Installing {EXTRA_LABELS[extra]}")
        self.setMinimumSize(610, 340)
        self.process = QProcess(self)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Installing into the Python environment running this GUI:"))
        self.console = QPlainTextEdit(self)
        self.console.setReadOnly(True)
        self.console.setPlainText(" ".join(command) + "\n\n")
        layout.addWidget(self.console)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setEnabled(False)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.process.setProgram(command[0])
        self.process.setArguments(list(command[1:]))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._append_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        QTimer.singleShot(0, self.process.start)

    def _append_output(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        self.console.insertPlainText(data)
        self.console.ensureCursorVisible()

    def _finished(self, code, status):
        self._append_output()
        if code == 0 and status == QProcess.ExitStatus.NormalExit:
            if site.ENABLE_USER_SITE:
                # The directory may not have existed when Python started.
                user_site = site.getusersitepackages()
                if os.path.isdir(user_site):
                    site.addsitedir(user_site)
            importlib.invalidate_caches()
            self.accept()
        else:
            self.console.appendPlainText(f"\nInstallation failed (exit code {code}).")
            self.close_button.setEnabled(True)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.console.appendPlainText("\nUnable to start the package installer.")
            self.close_button.setEnabled(True)

    def reject(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(2000):
                self.process.kill()
                self.process.waitForFinished(2000)
        super().reject()


def install_extra(extra: str, parent: QWidget) -> bool:
    """Obtain approval, install, and return whether the feature is usable."""
    try:
        command = pip_command(extra)
    except (RuntimeError, ValueError, metadata.PackageNotFoundError) as exc:
        QMessageBox.warning(parent, "Cannot Install Optional Feature", str(exc))
        return False
    requirements = ", ".join(requirements_for_extra(extra))
    answer = QMessageBox.question(
        parent,
        "Install Optional Dependencies?",
        f"Install {EXTRA_LABELS[extra]} ({requirements})?\n\n"
        "The following command will modify this Python environment only "
        "after your confirmation:\n\n" + " ".join(command),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    if _InstallDialog(extra, command, parent).exec() != QDialog.DialogCode.Accepted:
        return False
    unavailable = missing_modules(extra)
    if unavailable:
        QMessageBox.information(
            parent, "Restart May Be Required",
            "Installation completed, but these modules are not yet available: "
            + ", ".join(unavailable) + ". Restart the GUI to activate them.",
        )
        return False
    return True


def action_extra(action: str, exc: ModuleNotFoundError) -> str | None:
    """Prompt only for a missing module belonging to this action's extra."""
    extra = {"plot": "plot", "export_nexus": "nexus"}.get(action)
    if extra is None or not exc.name:
        return None
    return extra if exc.name.split(".", 1)[0] in OPTIONAL_MODULES[extra] else None
