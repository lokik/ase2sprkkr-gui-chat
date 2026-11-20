"""Common user-facing icons, with a bundled fallback for minimal Qt themes."""
from __future__ import annotations

from PyQt6.QtGui import QIcon

from ase2sprkkr_gui.gui.misc.resources import icon_path


def settings_icon() -> QIcon:
    """Use the system settings symbol, never a file-view icon as fallback."""
    return QIcon.fromTheme(
        "preferences-system", QIcon(str(icon_path("settings-cog.svg")))
    )

def application_icon() -> QIcon:
    """Return the bundled, platform-independent SPR-KKR application icon."""
    return QIcon(str(icon_path("sprkkr-orbital.svg")))


def task_icon(task: str) -> QIcon:
    """Use an optional bundled icon named after the canonical native task."""
    from ase2sprkkr_gui.gui.misc.resources import task_icon_path

    path = task_icon_path(task)
    return QIcon(str(path)) if path is not None else QIcon()
