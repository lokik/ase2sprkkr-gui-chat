"""Incomplete native output remains inspectable without inventing parsed data."""
from __future__ import annotations

import pytest
from kkrunner import RunManager, RunState, RunStore
from kkrunner.files import FileEntry, FileManifest

from ase2sprkkr.outputs import OutputOnlyResult
from ase2sprkkr_gui.gui.application.result_presentation import present_result


@pytest.fixture
def failed_run(tmp_path):
    output = tmp_path / "crashed.out"
    output.write_text("VERSION SPR-KKR\nSCF iteration 1\n", encoding="utf-8")
    manager = RunManager(RunStore(tmp_path / "runs"))
    record = manager.new_record(
        directory=tmp_path, run_id="run",
        metadata={"ase2sprkkr": {"task": "SCF"}},
        files=FileManifest(stdout=FileEntry(local_path=str(output))),
    )
    manager.store.save(record)
    record = manager.complete(
        record, state=RunState.FAILED, return_code=139, artifacts_available=True,
    )
    return manager, record


def test_output_only_result_exposes_just_captured_output(failed_run):
    _, record = failed_run
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


def test_persistent_source_requests_backend_fallback(failed_run, make_job_source, monkeypatch):
    manager, record = failed_run
    source = make_job_source(manager)
    expected = OutputOnlyResult(record, EOFError("Unexpected end of output"))
    seen = []

    def load_backend(manager, run_id, *, allow_unparsed=False):
        seen.append((manager, run_id, allow_unparsed))
        return expected

    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.application.persistent_jobs.load_project_result",
        load_backend,
    )
    assert source.load_result("run") is expected
    assert seen == [(manager, "run", True)]


@pytest.mark.parametrize("error", [ValueError("Bug"), EOFError("Truncated")])
def test_persistent_source_does_not_translate_backend_errors(failed_run, make_job_source, monkeypatch, error):
    manager, record = failed_run

    def load_backend(_manager, _run_id, *, allow_unparsed=False):
        assert allow_unparsed
        raise error

    monkeypatch.setattr(
        "ase2sprkkr_gui.gui.application.persistent_jobs.load_project_result",
        load_backend,
    )
    with pytest.raises(type(error)) as caught:
        make_job_source(manager).load_result(record.id)
    assert caught.value is error
