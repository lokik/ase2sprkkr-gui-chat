"""Standalone custom task and input-file flows without workspace IP documents."""
from __future__ import annotations

from pathlib import Path

from ase2sprkkr import InputParameters
from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox, QWidget

from ase2sprkkr_gui.gui.application.recent_files import RecentFiles
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.dialogs.expert_input import InputParametersDialog
from ase2sprkkr_gui.gui.dialogs.guided_input import GuidedInputParametersDialog
from ase2sprkkr_gui.gui.dialogs.task_chooser import TaskChooserDialog, applicability
from ase2sprkkr_gui.gui.flows import calculation as calculation_flow
from ase2sprkkr_gui.gui.input_parameters.dataset import apply_default_dataset
from ase2sprkkr_gui.gui.input_parameters.tasks import is_guided_task


_INPUT_FILTER = "SPR-KKR input files (*.inp *.in *.txt);;All files (*)"


def calculation_unavailable_reason(
    controller: WorkspaceController,
    parameters: InputParameters,
) -> str | None:
    """Check geometry/readiness without banning unsupported input editing."""
    if controller.atoms is None:
        return "Load or create a structure before running a calculation."
    task = parameters.task_definition
    if task is None:
        return "The input does not resolve to a registered SPR-KKR task."
    status, reason = applicability(task, controller.structure_kind(), atoms=controller.atoms)
    if status == "No":
        return reason
    if controller.structure_kind() == "0d":
        if not controller.can_run_embedding():
            return "Attach a native host potential before running an embedded calculation."
        if controller.embedding_host_scf_status() == "START":
            return "Converge the host potential before running an embedded calculation."
    if task.name.upper() != "SCF" and controller.potential is None:
        return "This task requires an existing SPR-KKR potential."
    return None


def edit_custom_parameters(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
    parameters: InputParameters,
    *,
    track_launch_monitor=None,
    project_repository=None,
) -> bool:
    """Edit an isolated input file, then optionally submit a single run."""
    expected_generation = controller.structure_generation
    task = parameters.task_name
    explanation = lambda candidate: calculation_unavailable_reason(controller, candidate)
    if task is not None and is_guided_task(task):
        dialog = GuidedInputParametersDialog(
            task, parameters, parent=parent, atoms=controller.atoms,
            calculation_unavailable_reason=explanation,
        )
    else:
        dialog = InputParametersDialog(
            parameters, parent=parent, atoms=controller.atoms,
            calculation_mode=True,
            calculation_unavailable_reason=explanation,
        )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return True  # The file may have been saved even if running was cancelled.

    request = calculation_flow._one_off_task_request(
        controller, dialog.parameters(),
        expected_structure_generation=expected_generation, parent=parent,
    )
    if request is None:
        return False
    calculation_flow.run_calculation(
        controller, recent_files, parent,
        request_override=request,
        position_like_window="guided-input",
        track_launch_monitor=track_launch_monitor,
        project_repository=project_repository,
    )
    return True


def load_custom_input(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
    path: str | Path,
    *,
    track_launch_monitor=None,
    project_repository=None,
) -> bool:
    """Read input into a local editor session, never a workspace document."""
    path = Path(path).expanduser().resolve()
    try:
        parameters = InputParameters.from_file(path)
    except Exception as exc:  # noqa: BLE001 - parser errors vary by input schema
        QMessageBox.critical(parent, "Cannot Load Input", str(exc))
        return False
    recent_files.remember("input", path)
    return edit_custom_parameters(
        controller, recent_files, parent, parameters,
        track_launch_monitor=track_launch_monitor,
        project_repository=project_repository,
    )


def choose_custom_input(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
    *,
    track_launch_monitor=None,
    project_repository=None,
) -> bool:
    path, _ = QFileDialog.getOpenFileName(
        parent, "Load SPR-KKR Input File", "", _INPUT_FILTER
    )
    return bool(path and load_custom_input(
        controller, recent_files, parent, path,
        track_launch_monitor=track_launch_monitor,
        project_repository=project_repository,
    ))


def choose_custom_task(
    controller: WorkspaceController,
    recent_files: RecentFiles,
    parent: QWidget,
    *,
    dataset_default_mode: str,
    track_launch_monitor=None,
    project_repository=None,
) -> bool:
    picker = TaskChooserDialog(controller.atoms, parent)
    if picker.exec() != QDialog.DialogCode.Accepted or not picker.selected_task:
        return False
    try:
        parameters = InputParameters.for_task(picker.selected_task)
        apply_default_dataset(parameters, dataset_default_mode, controller.atoms)
    except Exception as exc:  # noqa: BLE001 - native task registry definitions vary
        QMessageBox.critical(parent, "Cannot Create Custom Input", str(exc))
        return False
    return edit_custom_parameters(
        controller, recent_files, parent, parameters,
        track_launch_monitor=track_launch_monitor,
        project_repository=project_repository,
    )
