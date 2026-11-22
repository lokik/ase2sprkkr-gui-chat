"""Persistence policy for recoverable GUI preferences and histories.

Critical project metadata uses KKRunner's strict durable writes directly.
These GUI files are replaceable and tolerate failed directory fsync, but
their contents must still be synchronized and stored privately.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from kkrunner.support.durability import durable_write_json


def save_gui_json(path: str | Path, data: Any) -> None:
    """Save recoverable GUI state as private, readable JSON."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    durable_write_json(
        destination,
        data,
        mode=0o600,
        indent=2,
        directory_fsync="best_effort",
    )
