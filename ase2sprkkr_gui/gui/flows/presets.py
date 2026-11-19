"""Shared native-preset actions for Guided and Expert input editors."""
from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtWidgets import QInputDialog, QMessageBox, QWidget

from ase2sprkkr.input_parameters.presets import PresetStore
from ase2sprkkr_gui.gui.input_parameters.session import InputParametersSession


def load_preset(
    session: InputParametersSession,
    parent: QWidget | None,
    *,
    clear_first: bool = True,
    compatible_tasks: Iterable[str] | None = None,
) -> bool:
    """Apply a scoped preset as one undoable action.

    Expert mode allows an intentional task switch within the same IPD.
    Guided mode limits the result to backend tasks supported by its editor.
    """
    store = PresetStore()
    schema = session.working_parameters.definition_name
    allowed = (
        None if compatible_tasks is None
        else {str(task).upper() for task in compatible_tasks}
    )
    try:
        # A schema editor discovers all task subscopes, not only the effective
        # task inferred from its current parameter values.
        names = store.list(f"input_parameters:{schema}")
        if allowed is not None:
            names = tuple(
                name for name in names
                if not name.startswith("task:")
                or name.split(":", 1)[1].split(".", 1)[0].upper() in allowed
            )
    except Exception as exc:
        QMessageBox.critical(parent, "Load preset failed", str(exc))
        return False
    if not names:
        QMessageBox.information(
            parent, "Presets", f"No saved {schema.upper()} presets.",
        )
        return False
    name, ok = QInputDialog.getItem(
        parent, "Load preset", "Preset:", names, editable=False,
    )
    if not ok:
        return False

    def apply(candidate):
        candidate.load_preset(
            name, clear_first=clear_first, store=store,
            required_task=(next(iter(allowed)) if allowed and len(allowed) == 1 else None),
        )
        if allowed is not None and candidate.task_name not in allowed:
            raise ValueError(
                f"Preset selects {candidate.task_name}, expected "
                + "/".join(sorted(allowed))
            )

    try:
        if clear_first:
            candidate = session.result()
            apply(candidate)
            session.replace_parameters(
                candidate, text=f"Replace with preset {name}", source_page="preset",
            )
        else:
            session.mutate(
                apply, text=f"Merge preset {name}", source_page="preset",
            )
    except Exception as exc:
        QMessageBox.critical(parent, "Load preset failed", str(exc))
        return False
    return True


def save_preset(session: InputParametersSession, parent: QWidget | None) -> bool:
    """Save explicit values, choosing task scope automatically where needed.

    Qualified names let users force a particular scope, including in shared
    JXC/DMI schemas where 'jxc' is both an IPD name and a task name.
    """
    store = PresetStore()
    schema = session.working_parameters.definition_name
    task = session.working_parameters.task_name
    name, ok = QInputDialog.getText(
        parent, "Save as preset",
        f"Preset name (or input_parameters:{schema}.name"
        + (f" / task:{task.lower()}.name" if task else "")
        + "):",
    )
    if not ok or not name.strip():
        return False
    name = name.strip()
    try:
        session.result().save_preset(name, store=store)
    except FileExistsError:
        reply = QMessageBox.question(
            parent, "Replace preset?",
            f"A preset named {name!r} already exists in the selected scope. Replace it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        try:
            session.result().save_preset(name, overwrite=True, store=store)
        except Exception as exc:
            QMessageBox.critical(parent, "Save preset failed", str(exc))
            return False
    except Exception as exc:
        QMessageBox.critical(parent, "Save preset failed", str(exc))
        return False
    return True
