"""Saved stdout can be replayed without trusting overwritten historical data."""
from __future__ import annotations

from types import SimpleNamespace

from kkrunner.files import FileVersion

from ase2sprkkr_gui.gui.application.saved_output import read_saved_output


def test_saved_output_reads_existing_native_out(tmp_path):
    path = tmp_path / "Fe2_SPLEED.out"
    path.write_text("first line\nsecond line\n", encoding="utf-8")
    result = SimpleNamespace(output_file=path)
    replay = read_saved_output(result)

    assert replay.warning is None
    assert not replay.truncated
    assert replay.text == "first line\nsecond line\n"


def test_saved_output_rejects_overwritten_record_version(tmp_path):
    path = tmp_path / "Fe_SCF.out"
    path.write_text("historical", encoding="utf-8")
    version = FileVersion.capture(path)
    path.write_text("current overwritten contents", encoding="utf-8")
    record = SimpleNamespace(output_path=path, output_version=version)
    replay = read_saved_output(SimpleNamespace(), record=record)

    assert replay.text == ""
    assert "overwritten" in replay.warning
    assert "current overwritten contents" not in replay.text


def test_saved_output_rejects_overwritten_manifest(tmp_path):
    path = tmp_path / "Fe_SCF.out"
    path.write_text("current contents", encoding="utf-8")

    class HistoricalResult:
        output_file = path

        def require_current_file(self, _path):
            raise OSError("Not the historical output version")

    replay = read_saved_output(HistoricalResult())
    assert replay.text == ""
    assert "historical output version" in replay.warning


def test_saved_output_bounds_tail_and_skips_partial_line(tmp_path):
    path = tmp_path / "big.out"
    path.write_text("discard this line\nkeep this line\nlast line\n")
    replay = read_saved_output(SimpleNamespace(output_file=path), max_bytes=27)

    assert replay.warning is None
    assert replay.truncated
    assert "discard" not in replay.text
    assert replay.text.endswith("last line\n")


def test_saved_output_without_path_reports_unavailable():
    replay = read_saved_output(SimpleNamespace(output_values={}))
    assert replay.text == ""
    assert "No saved" in replay.warning
