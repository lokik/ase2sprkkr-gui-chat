"""Shared task-choice metadata and ordering, independent of Qt dialogs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ase2sprkkr import InputParameters
from ase2sprkkr.sprkkr.executables import task_system_error
from ase2sprkkr.sprkkr.sprkkr_atoms import system_kind_for_atoms
from ase2sprkkr_gui.gui.input_parameters.task_preferences import (
    supported_task_names, effective_preferred, task_ui_name, native_task_names,
)


@dataclass(frozen=True)
class TaskChoice:
    name: str
    experimental: bool
    applicable: str
    info: str
    reason: str = ""
    details: str = ""
    preferred: bool = False
    supported: bool = False


def applicability(task: Any, system_kind: str | None) -> tuple[str, str]:
    """Present the backend's geometry support in the chooser."""
    if system_kind is None:
        return "Unknown", "No structure is loaded."
    error = task_system_error(task, system_kind)
    return ("No", error) if error else ("Yes", "The task supports this structure.")


def task_choices(
    atoms: Any = None, *, preferred: tuple[str, ...] | None = None,
) -> tuple[TaskChoice, ...]:
    """Discover registered native tasks, including experimental tasks."""
    kind = system_kind_for_atoms(atoms)
    supported = set(supported_task_names())
    preferred_names = set(effective_preferred(None) if preferred is None else preferred)
    rows = []
    seen = set()
    registry = InputParameters.task_registry
    registered = set(registry.names())
    for name in sorted(registered):
        display_name = task_ui_name(name)
        if display_name in seen:
            continue
        seen.add(display_name)
        variants = tuple(
            registry[variant] for variant in native_task_names(display_name)
            if variant in registered
        )
        task = variants[0]
        outcomes = tuple(applicability(variant, kind) for variant in variants)
        status, reason = next(
            (item for item in outcomes if item[0] == "Yes"), outcomes[0]
        )
        definition = InputParameters.input_parameters_registry.definition_for_task(task)
        # Backend info is the short summary; description is detailed help.
        info = definition.info()
        details = definition.description()
        if not info.strip() and details.strip():
            # Only a short lead sentence when no dedicated summary exists.
            info = " ".join(details.split()).split(". ", 1)[0].rstrip(".") + "."
        rows.append(TaskChoice(
            display_name, all(variant.experimental for variant in variants), status,
            " ".join(info.split()) or f"SPR-KKR {task.executable} calculation",
            reason, details.strip(),
            display_name in preferred_names,
            display_name in supported,
        ))
    return tuple(rows)


def order_choices(
    rows: tuple[TaskChoice, ...] | list[TaskChoice],
    *,
    primary: str | None = None,
    descending: bool = False,
    has_structure: bool = True,
) -> list[TaskChoice]:
    """Stable multi-column sort: possible, preferred, supported, nonexperimental, name."""
    default = (
        ("applicable", "preferred", "supported", "experimental", "name")
        if has_structure else
        ("preferred", "supported", "experimental", "name")
    )
    keys = ((primary,) + tuple(key for key in default if key != primary)
            if primary in (*default, "info") else default)

    def value(row: TaskChoice, key: str):
        if key == "applicable":
            return {"Yes": 0, "No": 1, "Unknown": 2}.get(row.applicable, 3)
        if key in ("preferred", "supported"):
            return 0 if getattr(row, key) else 1
        if key == "experimental":
            # Reverse numeric order: secondary sorting and the first
            # Experimental click both put non-experimental tasks first.
            return 0 if row.experimental else 1
        return getattr(row, key).casefold()

    ordered = list(rows)
    for key in reversed(keys[1:]):
        ordered.sort(
            key=lambda row, k=key: value(row, k),
            reverse=key == "experimental",
        )
    reverse_first = descending if primary in keys else keys[0] == "experimental"
    ordered.sort(key=lambda row: value(row, keys[0]), reverse=reverse_first)
    return ordered


def task_match_priority(row: TaskChoice, query: str) -> int | None:
    """Exact name, name prefix, name substring, then short info; never details."""
    query = query.casefold()
    if not query:
        return 0
    name = row.name.casefold()
    if name == query:
        return 0
    if name.startswith(query):
        return 1
    if query in name:
        return 2
    if query in row.info.casefold():
        return 3
    return None
