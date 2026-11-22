"""Small GUI persistence wrappers around KKRunner's atomic writes.

GUI preferences and histories may tolerate a failed directory durability
barrier. The file itself must still be synced before replacement and the
temporary file is kept private, as in the old NamedTemporaryFile writer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from kkrunner.support.durability import durable_write_json, durable_write_text


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Atomically replace a GUI state file with best-effort directory fsync."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    durable_write_text(
        destination,
        text,
        encoding=encoding,
        mode=0o600,
        directory_fsync="best_effort",
    )


def atomic_write_json(
    path: str | Path,
    data: Any,
    *,
    indent: int | None = 2,
) -> None:
    """Serialize JSON with the same private, atomic GUI persistence policy."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    durable_write_json(
        destination,
        data,
        indent=indent,
        mode=0o600,
        directory_fsync="best_effort",
    )
