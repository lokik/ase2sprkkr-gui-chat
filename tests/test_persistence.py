"""Contracts for recoverable GUI-state persistence policy."""
from __future__ import annotations

import json
import os
import stat

import pytest

from kkrunner.support import durability
from ase2sprkkr_gui.gui.application.persistence import save_gui_json


def test_save_gui_json_creates_parents_and_indents_json(tmp_path):
    path = tmp_path / "missing" / "nested" / "recent.json"
    save_gui_json(path, {"new": [1, 2]})

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": [1, 2]}
    assert path.read_text(encoding="utf-8") == (
        '{\n  "new": [\n    1,\n    2\n  ]\n}'
    )
    assert not tuple(path.parent.glob(f".{path.name}.*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode bits required")
def test_save_gui_json_keeps_replaced_files_private(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o644)

    save_gui_json(path, {"updated": True})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_gui_json_tolerates_failed_directory_sync(tmp_path, monkeypatch):
    path = tmp_path / "recent.json"

    def fail_directory_sync(_directory):
        raise OSError("directory fsync unavailable")

    monkeypatch.setattr(durability, "fsync_directory", fail_directory_sync)
    save_gui_json(path, {"recent_files": []})

    assert json.loads(path.read_text(encoding="utf-8")) == {"recent_files": []}
