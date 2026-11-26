"""Read a saved SPR-KKR stdout for historical result-window replay.

No Qt objects are created here; file checks and bounded I/O run in a worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from ase2sprkkr import TaskResult
from .result_store import ResultRecord
from .operation_results import EmptySpheresResult

MAX_SAVED_OUTPUT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class SavedOutput:
    text: str = ""
    warning: str | None = None
    truncated: bool = False


def read_saved_output(
    result: TaskResult | EmptySpheresResult, *, record: ResultRecord | None = None,
    max_bytes: int = MAX_SAVED_OUTPUT_BYTES,
) -> SavedOutput:
    """Replay the recorded OUT, never silently substituting overwritten bytes.

    A tail cap keeps large outputs from exhausting memory or freezing Qt when
    text and INFO parsers consume the returned data. The original file remains
    accessible through its normal result-file actions.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if isinstance(result, EmptySpheresResult):
        return SavedOutput(warning="No saved SPR-KKR output file is recorded.")
    path = record.output_path if record is not None else None
    if path is None:
        path = result.path_to("output") if "output" in result.files else result.output_file
    if path is None:
        return SavedOutput(warning="No saved SPR-KKR output file is recorded.")
    path = Path(path).expanduser().absolute()
    version = record.output_version if record is not None else None

    def verify() -> None:
        if version is not None and not version.is_current():
            raise OSError("The recorded output was overwritten or removed")
        result.require_current_file(path)

    try:
        verify()
        before = path.stat()
        if not path.is_file():
            raise OSError("The saved output path is not a regular file")
        with path.open("rb") as stream:
            truncated = before.st_size > max_bytes
            if truncated:
                stream.seek(before.st_size - max_bytes)
            data = stream.read(max_bytes + 1)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino
        ):
            raise OSError("The output changed while its text was being read")
        verify()
    except (OSError, ValueError) as exc:
        return SavedOutput(warning=f"Saved SPR-KKR output is unavailable ({path}): {exc}")

    if truncated:
        # The first bytes may be a partial native output line.
        newline = data.find(b"\n")
        if newline != -1:
            data = data[newline + 1:]
    return SavedOutput(
        text=data.decode("utf-8", errors="replace"),
        truncated=truncated,
    )
