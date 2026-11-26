"""Persist stable references to the result windows open at normal GUI exit."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from collections.abc import Iterable

from ase2sprkkr_gui.gui.application.persistence import save_gui_json

logger = logging.getLogger(__name__)


def _references(entries) -> tuple[tuple[str, str], ...]:
    if not isinstance(entries, (tuple, list)):
        return ()
    references = []
    for entry in entries:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            continue
        kind, value = entry
        if kind not in ("job", "output") or not isinstance(value, str) or not value:
            continue
        if kind == "output":
            value = str(Path(value).expanduser().absolute())
        references.append((kind, value))
    return tuple(dict.fromkeys(references))


def load_window_session(path: Path) -> tuple[tuple[str, str], ...]:
    try:
        return _references(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return ()
    except (OSError, UnicodeError, ValueError):
        logger.warning("Cannot read result-window session %s", path, exc_info=True)
        return ()


def save_window_session(path: Path, references: Iterable[tuple[str, str]]) -> None:
    entries = _references(tuple(references))
    if entries or path.exists():
        save_gui_json(path, entries)
