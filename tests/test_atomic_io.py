from __future__ import annotations

import json
import os
import stat

import pytest
from kkrunner.support import durability

from ase2sprkkr_gui.gui.application import atomic_io


def test_atomic_write_json_replaces_complete_document(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"old": true}', encoding="utf-8")

    atomic_io.atomic_write_json(path, {"new": [1, 2, 3]})

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": [1, 2, 3]}
    assert not tuple(tmp_path.glob(".state.json.*.tmp"))


def test_atomic_write_keeps_previous_file_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    original = '{"old": true}'
    path.write_text(original, encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(durability.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_io.atomic_write_json(path, {"new": True})

    assert path.read_text(encoding="utf-8") == original
    assert not tuple(tmp_path.glob(".state.json.*.tmp"))


def test_atomic_write_keeps_previous_file_when_flush_fails(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    original = '{"old": true}'
    path.write_text(original, encoding="utf-8")

    def fail_fsync(_fd):
        raise OSError("flush failed")

    monkeypatch.setattr(durability.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="flush failed"):
        atomic_io.atomic_write_json(path, {"new": True})

    assert path.read_text(encoding="utf-8") == original
    assert not tuple(tmp_path.glob(".state.json.*.tmp"))


def test_atomic_write_keeps_gui_files_private(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("old")
    if os.name == "posix":
        path.chmod(0o644)
    atomic_io.atomic_write_json(path, {"updated": True})
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_atomic_write_creates_missing_parent(tmp_path):
    path = tmp_path / "missing" / "nested" / "settings.json"
    atomic_io.atomic_write_json(path, {"new": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}


def test_atomic_write_ignores_only_directory_sync_failure(tmp_path, monkeypatch):
    path = tmp_path / "recent.json"

    def fail_directory_sync(_directory):
        raise OSError("directory fsync unavailable")

    monkeypatch.setattr(durability, "fsync_directory", fail_directory_sync)
    atomic_io.atomic_write_json(path, {"recent_files": []})
    assert json.loads(path.read_text(encoding="utf-8")) == {"recent_files": []}
