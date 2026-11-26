"""Best-effort normal-exit snapshot of GUI windows and independent native runs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path

from kkrunner.files.versions import FileVersion
from kkrunner.runs import RunRecord
from kkrunner.support.file_locks import FileLock

from ase2sprkkr_gui.gui.application.persistence import save_gui_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowReference:
    kind: str
    value: str
    version: FileVersion | None = None

    @classmethod
    def output(cls, version):
        return cls("output", str(version.path), version)

    def to_dict(self):
        version = None if self.version is None else asdict(self.version)
        if version is not None:
            version["path"] = str(self.version.path)
        return {"kind": self.kind, "value": self.value, "version": version}


def recoverable_session_run(record):
    """Only accepted, independently running native jobs belong in a snapshot."""
    return (
        record.project_id is None and record.detached and not record.state.is_terminal
        and record.execution.backend_id is not None
    )


def save_session(path: Path, records, windows):
    """Last clean exit wins. No live session registry or inter-GUI coordination."""
    data = {
        "version": 1,
        "runs": [record.to_dict() for record in records if recoverable_session_run(record)],
        "windows": [window.to_dict() for window in dict.fromkeys(windows)],
    }
    if data["runs"] or data["windows"] or path.exists():
        with FileLock(path.with_suffix(".lock")):
            save_gui_json(path, data)


def take_session(path: Path, store):
    """Consume one clean-exit snapshot atomically, then bind records to store."""
    if not path.exists():
        return ()
    try:
        with FileLock(path.with_suffix(".lock")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return ()
            path.unlink()
    except (OSError, UnicodeError, ValueError):
        logger.warning("Cannot restore GUI run session %s", path, exc_info=True)
        return ()
    if not isinstance(data, dict) or data.get("version") != 1:
        return ()
    records = data.get("runs")
    if isinstance(records, list):
        for raw in records:
            try:
                record = RunRecord(**raw)
                if recoverable_session_run(record):
                    store.save(record)
            except (TypeError, ValueError, RuntimeError):
                logger.warning("Skipping invalid session run", exc_info=True)
    windows = []
    entries = data.get("windows")
    if isinstance(entries, list):
        for raw in entries:
            try:
                kind, value = raw["kind"], raw["value"]
                if not isinstance(value, str) or not value:
                    continue
                if kind == "output":
                    version = FileVersion(**raw["version"])
                    if str(version.path) != value:
                        continue
                    windows.append(WindowReference.output(version))
                elif kind in ("persistent", "memory"):
                    if kind == "memory":
                        store.get(value)
                    windows.append(WindowReference(kind, value))
            except (TypeError, ValueError, KeyError, OSError):
                logger.warning("Skipping invalid session window", exc_info=True)
    return tuple(dict.fromkeys(windows))


def output_reference(record):
    """Use the recorded output version, never a fresh snapshot of changed data."""
    if record.files is None or record.files.output_file is None:
        return None
    path = Path(record.files.output_file).absolute()
    for version in record.files.output_versions:
        if version.path == path and version.is_current():
            return WindowReference.output(version)
    return None
