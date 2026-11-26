"""Qt-independent execution-profile adaptation and persistence."""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from ase2sprkkr import config
from ase2sprkkr.sprkkr.profile import resolve_sprkkr_profile

_DEFAULT_PROFILE_VALUES = {"executor": "local", "mpi": "auto", "mpi_warning": True}
_DEFAULT_UNCHANGED = object()


@dataclass
class ProfileDraft:
    """Editable snapshot of one execution profile."""

    name: str
    values: dict[str, Any]
    read_only: bool = False


def profile_values(profile: Any) -> dict[str, Any]:
    """Return the explicitly configured, user-editable values of a profile."""
    values = dict(profile.as_dict(only_changed="explicit", generated=False) or {})
    values.pop("command", None)
    return values


def backend_kind(profile: Any) -> str:
    """Return the UI execution kind represented by a resolved runtime profile."""
    executor = profile.executor or "local"
    if executor == "ssh" and profile.backend_config.name == "psij":
        return "ssh-psij"
    return str(executor)


def draft_kind(values: dict[str, Any]) -> str:
    """Return the UI execution kind represented by persisted profile values."""
    executor = values.get("executor") or "local"
    backend = values.get("backend")
    if executor == "ssh" and (
        backend == "psij"
        or (backend is None and (
            values.get("psij_backend") is not None
            or values.get("psij_url") is not None
        ))
    ):
        return "ssh-psij"
    return str(executor)


def kind_label(kind: str) -> str:
    """Return the user-facing label for an execution kind."""
    return {
        "local": "Local computer",
        "ssh": "Remote computer (SSH)",
        "psij": "Workload manager (PSI/J)",
        "ssh-psij": "Remote workload manager (SSH + PSI/J)",
    }.get(kind, kind)


def _profile_presentation(
    name: str | None, *, configuration: Any | None = None
) -> tuple[str, str, str, str]:
    """Resolve execution location, MPI description/value and executable path."""
    profile = resolve_sprkkr_profile(name, configuration=configuration)
    kind = backend_kind(profile)
    if kind == "local":
        where = "Local execution"
    elif kind == "ssh":
        where = f"SSH to {profile.host or '(host not set)'}"
    elif kind == "ssh-psij":
        backend = profile.psij_backend or "slurm"
        where = f"SSH to {profile.host or '(host not set)'} + PSI/J ({backend})"
    elif kind == "psij":
        where = f"PSI/J ({profile.psij_backend or 'slurm'})"
    else:
        where = f"{profile.executor} execution"

    if kind in {"local", "ssh"}:
        mode = "supervised" if profile.backend_config.name == "kkrunner" else "direct"
        where = f"{where} · {mode}"
        if mode == "direct" and profile.detached:
            where += " (detached)"

    mpi = profile.mpi
    if mpi is False:
        mpi_value = "disabled"
        mpi_text = f"MPI {mpi_value}"
    elif mpi is True:
        mpi_value = "enabled"
        mpi_text = f"MPI {mpi_value}"
    elif mpi is None or mpi == "auto":
        mpi_value = "automatic"
        mpi_text = f"MPI {mpi_value}"
    elif isinstance(mpi, int):
        mpi_value = f"{mpi} processes"
        mpi_text = f"MPI: {mpi_value}"
    elif isinstance(mpi, (tuple, list)):
        mpi_value = shlex.join(str(item) for item in mpi)
        mpi_text = f"MPI: {mpi_value}"
    else:
        mpi_value = str(mpi)
        mpi_text = f"MPI: {mpi_value}"

    executable = profile.executable_dir or "PATH"
    if profile.executable_suffix:
        executable = f"{executable} (suffix {profile.executable_suffix})"
    return where, mpi_text, mpi_value, executable


def profile_description(name: str | None, *, configuration: Any | None = None) -> str:
    """Return the existing short text description of a resolved profile."""
    where, mpi_text, _mpi_value, executable = _profile_presentation(
        name, configuration=configuration
    )
    return f"{where} · {mpi_text}\nExecutables: {executable}"


def profile_summary_fields(
    name: str | None, *, configuration: Any | None = None
) -> tuple[tuple[str, str], ...]:
    """Structured monitor metadata, without parsing display strings."""
    where, _mpi_text, mpi_value, executable = _profile_presentation(
        name, configuration=configuration
    )
    return (
        ("Execution", where),
        ("Executables", executable),
        ("MPI", mpi_value),
    )


class ExecutionProfileStore:
    """Persistence boundary for ASE2SPRKKR execution profiles."""

    def __init__(self, configuration: Any | None = None) -> None:
        self._config = config if configuration is None else configuration

    @property
    def configuration(self) -> Any:
        """Underlying ASE2SPRKKR configuration shared with other settings pages."""
        return self._config

    @property
    def reserved_names(self) -> frozenset[str]:
        """Names that the backend reserves for special profile views."""
        return frozenset(str(name) for name in self._config.profiles.reserved_names)

    def drafts(self) -> dict[str, ProfileDraft]:
        """Load editable snapshots of all configured profiles."""
        return {
            str(name): ProfileDraft(
                str(name),
                profile_values(profile),
                bool(profile.read_only),
            )
            for name, profile in self._config.profiles.items()
        }

    def names(self) -> tuple[str, ...]:
        """Return configured profile names in backend order."""
        return tuple(str(name) for name in self._config.profiles)

    def default_profile_name(self) -> str | None:
        """Return the explicitly selected default (None means ASE fallback)."""
        return self._config.default_profile()

    def resolved_name(self, name: str | None) -> str | None:
        """Resolve ``None`` to the configured default profile name."""
        return self._config.profiles.resolve_name(name)

    def describe(self, name: str | None) -> str:
        """Describe a profile using this store's configuration context."""
        return profile_description(name, configuration=self._config)

    def save(
        self,
        drafts: dict[str, ProfileDraft],
        *,
        default_profile: str | None | object = _DEFAULT_UNCHANGED,
    ) -> None:
        """Persist profile edits and the selected default in one YAML write."""
        if default_profile is _DEFAULT_UNCHANGED:
            default_profile = self._config.default_profile()
            if default_profile not in drafts:
                default_profile = None
        if default_profile is not None and default_profile not in drafts:
            raise ValueError(f"Unknown default execution profile: {default_profile}")
        editable = {
            name: draft.values
            for name, draft in drafts.items()
            if not draft.read_only
        }
        self._config.profiles.set(editable)
        if self._config.default_profile() != default_profile:
            self._config.default_profile.set(default_profile)
        self._config.save_user_preferences()


class ExecutionProfileEditor:
    """Qt-independent editing model for a set of execution profiles."""

    def __init__(self, store: ExecutionProfileStore) -> None:
        self._store = store
        self.drafts = store.drafts()
        self.default_profile = store.default_profile_name()

    def set_default(self, name: str | None) -> None:
        """Stage an explicit default, or None to use automatic resolution."""
        if name is not None and name not in self.drafts:
            raise ValueError(f"Unknown execution profile: {name}")
        self.default_profile = name

    def add(self, name: str) -> ProfileDraft:
        name = name.strip()
        if not name or name in self._store.reserved_names or name in self.drafts:
            raise ValueError(f"Profile {name!r} already exists or is reserved.")
        draft = ProfileDraft(name, dict(_DEFAULT_PROFILE_VALUES))
        self.drafts[name] = draft
        return draft

    def delete(self, name: str) -> None:
        draft = self.drafts[name]
        if draft.read_only:
            raise ValueError(f"Profile {name!r} is read-only.")
        del self.drafts[name]
        if name == self.default_profile:
            self.default_profile = None

    def save(self) -> None:
        self._store.save(self.drafts, default_profile=self.default_profile)
