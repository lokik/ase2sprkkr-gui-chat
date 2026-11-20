"""Shared task selection policy, independent of Qt widgets.

Supported means that the GUI has a dedicated editor; possible means that
the backend permits the current structure. These are separate properties.
"""
from __future__ import annotations

from ase2sprkkr import InputParameters
from ase2sprkkr.sprkkr.executables import task_system_error

from ase2sprkkr_gui.gui.input_parameters.tasks import guided_task_definitions


def supported_task_names() -> tuple[str, ...]:
    """Registered native task names with a GUI-defined editor, alphabetically."""
    available = set(InputParameters.task_registry.names())
    return tuple(sorted(
        {name.upper() for definition in guided_task_definitions()
         for name in definition.backend_tasks if name.upper() in available}
    ))


def all_task_names() -> tuple[str, ...]:
    return tuple(sorted(name.upper() for name in InputParameters.task_registry.names()))


def effective_preferred(
    selected: tuple[str, ...] | None,
    supported_at_save: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Follow supported defaults, or retain an explicit ordered preference.

    Newly supported tasks are offered to the user rather than silently added.
    The saved supported set is a baseline for detecting additions, not a
    second configuration of task support.
    """
    supported = supported_task_names()
    if selected is None:
        return supported
    available = set(all_task_names())
    result = list(dict.fromkeys(name for name in selected if name in available))
    return tuple(result)


def applicable_task_names(system_kind: str | None) -> tuple[str, ...]:
    """Native applicability only; readiness to run is evaluated elsewhere."""
    if system_kind is None:
        return ()
    return tuple(
        name for name in all_task_names()
        if task_system_error(InputParameters.task_registry[name], system_kind) is None
    )


def displayed_tasks(
    system_kind: str | None,
    preferred: tuple[str, ...],
    *,
    maximum_fallback: int = 8,
) -> tuple[str, ...]:
    """First nonempty group: preferred, guided, ordinary, experimental.

    Preferred order is user-defined; fallback groups are alphabetical. Too
    many tasks in a fallback group suppress cards, not the Custom task action.
    """
    possible = set(applicable_task_names(system_kind))
    if not possible:
        return ()
    chosen = tuple(name for name in preferred if name in possible)
    if chosen:
        return chosen
    supported = set(supported_task_names())
    registered = InputParameters.task_registry
    for candidates in (
        sorted(possible & supported),
        sorted(name for name in possible if not registered[name].experimental),
        sorted(possible),
    ):
        if candidates:
            return tuple(candidates) if len(candidates) <= maximum_fallback else ()
    return ()
