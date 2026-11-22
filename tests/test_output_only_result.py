"""Incomplete native output remains inspectable without inventing parsed data."""
from __future__ import annotations

from threading import RLock
from types import SimpleNamespace

import pytest
from kkrunner.files import FileVersion

from ase2sprkkr.outputs import OutputOnlyResult
from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJob, PersistentJobSource
from ase2sprkkr_gui.gui.application.result_presentation import present_result


def _record(tmp_path):
    output = tmp_path / "crashed.out"
    output.write_text("VERSION SPR-KKR\nSCF iteration 1\n", encoding="utf-8")
    version = FileVersion.capture(output)
    files = SimpleNamespace(
        output_file=str(output),
        captured_at="2026-11-18T00:00:00+00:00",
        all_versions=(version,),
    )
    return SimpleNamespace(
        id="run",
        metadata={"ase2sprkkr": {"task": "SCF"}},
        files=files,
        execution=SimpleNamespace(directory=SimpleNamespace(local=str(tmp_path))),
        return_code=139,
    )


def test_output_only_result_exposes_just_captured_output(tmp_path):
    record = _record(tmp_path)
    result = OutputOnlyResult(record, EOFError("Unexpected end of output"))
    assert result.task_name == "scf"
    assert result.return_code == 139
    assert not result.produces_potential
    assert result.input_parameters is None
    assert tuple(result.output_values) == ("output",)
    output = result.output_values["output"]
    assert output.path() == record.files.output_file
    assert "open" in output.actions()
    assert "plot" not in output.actions()

    presentation = present_result(result)
    assert len(presentation.values) == 1
    assert presentation.values[0].name == "Output file"
    assert "captured output file only" in presentation.file_warning
    assert "Unexpected end of output" in presentation.file_warning


def _source(record, *, current=True):
    manager = SimpleNamespace(
        store=SimpleNamespace(get=lambda _job_id: record),
        retrieve_result_files=lambda _record: None,
        output_file_is_current=lambda _record: current,
    )
    source = object.__new__(PersistentJobSource)
    source._manager = manager
    source._lock = RLock()
    source._jobs = {}
    source._replace_cached_job = lambda _job: False
    return source


def test_persistent_source_requests_backend_fallback(tmp_path, monkeypatch):
    record = _record(tmp_path)
    source = _source(record)
    expected = OutputOnlyResult(record, EOFError("Unexpected end of output"))
    seen = []

    def load_backend(manager, run_id, *, allow_unparsed=False):
        seen.append((manager, run_id, allow_unparsed))
        return expected

    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.application.persistent_jobs.load_project_result",
        load_backend,
    )
    monkeypatch.setattr(
        PersistentJob, "from_run_record",
        classmethod(lambda cls, _record, **_kwargs: None),
    )
    assert source.load_result("run") is expected
    assert seen == [(source._manager, "run", True)]


@pytest.mark.parametrize("error", [ValueError("Bug"), EOFError("Truncated")])
def test_persistent_source_does_not_translate_backend_errors(tmp_path, monkeypatch, error):
    record = _record(tmp_path)

    def load_backend(_manager, _run_id, *, allow_unparsed=False):
        assert allow_unparsed
        raise error

    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.application.persistent_jobs.load_project_result",
        load_backend,
    )
    with pytest.raises(type(error)) as caught:
        _source(record).load_result("run")
    assert caught.value is error
