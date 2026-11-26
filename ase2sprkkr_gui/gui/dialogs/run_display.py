"""Shared INFO page construction and log bounds for live and saved runs."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtWidgets import QWidget

from ase2sprkkr_gui.gui.dialogs.scf_progress import ScfProgressPage, scf_tolerance_from_parameters
from ase2sprkkr_gui.gui.dialogs.jxc_progress import JxcProgressPage
from ase2sprkkr_gui.gui.dialogs.dos_progress import DosProgressPage
from ase2sprkkr_gui.gui.dialogs.bsf_progress import BsfProgressPage
from ase2sprkkr_gui.gui.dialogs.spleed_progress import SpleedProgressPage
from ase2sprkkr_gui.gui.dialogs.arpes_progress import ArpesProgressPage
from ase2sprkkr_gui.gui.dialogs.xas_progress import XasProgressPage
from ase2sprkkr.outputs.readers.dos import dos_total_points_from_parameters
from ase2sprkkr.outputs.readers.xas import xas_total_points_from_parameters


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

MAX_OUTPUT_BLOCKS = 4096
MAX_LOGICAL_LINE_LENGTH = 4096


def has_info_page(task_name: str | None) -> bool:
    """Whether a task has a native parser-driven INFO presentation."""
    return str(task_name or "").strip().lower() in _INFO_PAGE_FACTORIES


def info_page_settings(task_name: str | None, parameters: Any) -> dict[str, Any]:
    """Input-derived progress settings shared by live and saved INFO views.

    Only the input's own task matters: an absent, incomplete or untrusted
    parameter source must not cause us to guess an energy total or tolerance.
    """
    task = str(task_name or "").strip().lower()
    if task == "scf":
        return {"scf_tolerance": scf_tolerance_from_parameters(parameters)}
    if task == "dos":
        return {"dos_total_points": dos_total_points_from_parameters(parameters)}
    if task == "xas":
        return {"xas_total_points": xas_total_points_from_parameters(parameters)}
    return {}


def create_info_page(
    task_name: str | None,
    parent: QWidget,
    *,
    scf_tolerance: float | None = None,
    dos_total_points: int | None = None,
    xas_total_points: int | None = None,
) -> QWidget | None:
    """Construct the same task-specific INFO widget for live and saved runs."""
    task = str(task_name or "").strip().lower()
    factory = _INFO_PAGE_FACTORIES.get(task)
    if factory is None:
        return None
    page_options = {
        "scf": {"target_error": scf_tolerance},
        "dos": {"total_points": dos_total_points},
        "xas": {"total_points": xas_total_points},
    }
    return factory(parent, **page_options.get(task, {}))
