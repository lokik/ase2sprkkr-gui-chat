"""Persistent application-wide user preferences."""
from __future__ import annotations

import json
from pathlib import Path

from ase2sprkkr_gui.gui.application.atomic_io import atomic_write_json
from ase2sprkkr_gui.gui.application.config import config_home
from ase2sprkkr_gui.gui.plots.atom_sizes import (
    MAX_ATOM_SIZE_PERCENT, MIN_ATOM_SIZE_PERCENT, set_atom_appearance,
)


DATASET_DEFAULT_MODES = ("empty", "datetime", "uuid")
DEFAULT_DATASET_MODE = "datetime"


def default_settings_path() -> Path:
    """Return the per-user application settings path."""
    return config_home() / "settings.json"


class ApplicationSettings:
    """Load and persist application-wide user preferences."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()
        self.viewer = "ase"
        self.dataset_default_mode = DEFAULT_DATASET_MODE
        self.share_python_shell_history = True
        self.atom_size_percent = 100
        self.atom_relative_sizes = True
        self.atom_potential_radii = True

    def load(self) -> None:
        """Load settings; corrupt or unreadable files keep defaults."""
        self.viewer = "ase"
        self.dataset_default_mode = DEFAULT_DATASET_MODE
        self.atom_size_percent = 100
        self.atom_relative_sizes = True
        self.atom_potential_radii = True
        self._apply_atom_appearance()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (
            FileNotFoundError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return
        if not isinstance(data, dict):
            return
        viewer = data.get("viewer")
        if isinstance(viewer, str) and viewer.strip():
            self.viewer = viewer.strip()
        dataset_mode = data.get("dataset_default_mode")
        if dataset_mode in DATASET_DEFAULT_MODES:
            self.dataset_default_mode = dataset_mode
        enabled = data.get("share_python_shell_history")
        if isinstance(enabled, bool):
            self.share_python_shell_history = enabled
        size = data.get("atom_size_percent")
        if type(size) is int and MIN_ATOM_SIZE_PERCENT <= size <= MAX_ATOM_SIZE_PERCENT:
            self.atom_size_percent = size
        relative = data.get("atom_relative_sizes")
        if isinstance(relative, bool):
            self.atom_relative_sizes = relative
        potential = data.get("atom_potential_radii")
        if isinstance(potential, bool):
            self.atom_potential_radii = potential
        self._apply_atom_appearance()

    def _apply_atom_appearance(self) -> None:
        set_atom_appearance(
            self.atom_size_percent, self.atom_relative_sizes,
            self.atom_potential_radii,
        )

    def save(self) -> bool:
        """Persist settings, returning false when the file is not writable."""
        try:
            atomic_write_json(
                self.path,
                {
                    "viewer": self.viewer,
                    "dataset_default_mode": self.dataset_default_mode,
                    "share_python_shell_history": self.share_python_shell_history,
                    "atom_size_percent": self.atom_size_percent,
                    "atom_relative_sizes": self.atom_relative_sizes,
                    "atom_potential_radii": self.atom_potential_radii,
                },
            )
        except OSError:
            return False
        self._apply_atom_appearance()
        return True
