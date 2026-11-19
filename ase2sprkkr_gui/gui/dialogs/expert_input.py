"""Modal workflow for editing an isolated copy of ``InputParameters``."""
from __future__ import annotations

from typing import Any, Callable

from ase2sprkkr import InputParameters
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr_gui.gui.application.window_geometry import manage_window_geometry
from ase2sprkkr_gui.gui.flows import presets as preset_flows
from ase2sprkkr_gui.gui.flows.input_parameters import (
    choose_and_load_input_parameters,
    choose_and_save_input_parameters,
    edit_input_parameters_file,
)
from ase2sprkkr_gui.gui.input_parameters.dataset import apply_default_dataset
from ase2sprkkr_gui.gui.input_parameters.session import (
    InputParametersSession,
    create_input_parameters_session,
)
from ase2sprkkr_gui.gui.input_parameters.validation import (
    InputParametersValidationError,
    validate_setup,
)
from ase2sprkkr_gui.gui.widgets.input_parameters.dataset import DatasetEditor
from ase2sprkkr_gui.gui.widgets.input_parameters.expert_tree import ExpertInputTreeEditor


class InputParametersDialog(QDialog):
    """Own the expert editor's draft and modal accept/cancel workflow."""

    def __init__(
        self,
        parameters: InputParameters | InputParametersSession,
        parent: QWidget | None = None,
        *,
        show_changed_only: bool = False,
        atoms: Any = None,
        calculation_mode: bool = False,
        calculation_unavailable_reason: Callable[[InputParameters], str | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._calculation_mode = calculation_mode
        self._calculation_unavailable_reason = calculation_unavailable_reason
        if isinstance(parameters, InputParametersSession):
            self.session = parameters.fork(self)
        else:
            self.session = create_input_parameters_session(parameters, self)

        self.setWindowTitle('Edit Input Parameters')
        manage_window_geometry(
            self,
            "expert-input",
            default_size=(1200, 750),
            default_maximized=True,
        )
        root = QVBoxLayout(self)

        self.dataset_editor = DatasetEditor(self.session, self)
        root.addWidget(self.dataset_editor)

        self.tree_editor = ExpertInputTreeEditor(
            self.session,
            atoms=atoms,
            show_changed_only=show_changed_only,
            parent=self,
        )
        root.addWidget(self.tree_editor, 1)

        self._validation_error = QLabel(self)
        self._validation_error.setWordWrap(True)
        self._validation_error.hide()
        root.addWidget(self._validation_error)
        self.tree_editor.validationChanged.connect(
            lambda _has_errors: self._validation_error.hide()
        )

        file_actions = QHBoxLayout()
        root.addLayout(file_actions)
        footer = QHBoxLayout()
        footer.addStretch(1)
        root.addLayout(footer)

        def add_button(text: str, slot: Any, *, finish: bool = False) -> QPushButton:
            button = QPushButton(text, self)
            button.clicked.connect(slot)
            (footer if finish else file_actions).addWidget(button)
            return button

        self.load_btn = add_button('Load input…', self._load_input)
        self.save_btn = add_button('Save Input…', self._save_input)
        self.load_preset_btn = add_button('Replace by preset…', self._replace_preset)
        self.merge_preset_btn = add_button('Load preset…', self._merge_preset)
        self.save_preset_btn = add_button('Save as preset…', self._save_preset)
        self.edit_input_btn = add_button('Edit input file…', self._edit_input_file)
        file_actions.addStretch(1)
        self.cancel_btn = add_button('Cancel', self.reject, finish=True)
        self.ok_btn = add_button(
            'Calculate' if calculation_mode else 'OK', self._on_ok, finish=True
        )
        self.calculate_button = self.ok_btn if calculation_mode else None
        self.tree_editor.validationChanged.connect(
            lambda _invalid: self._update_calculation_availability()
        )
        self.session.valueChanged.connect(self._update_calculation_availability)
        self.session.parametersReplaced.connect(self._update_calculation_availability)
        self._update_calculation_availability()

    def _update_calculation_availability(self, *_args) -> None:
        if not self._calculation_mode:
            return
        parameters = self.session.working_parameters
        try:
            validate_setup(parameters)
            reason = (
                self._calculation_unavailable_reason(parameters)
                if self._calculation_unavailable_reason is not None else None
            )
        except (InputParametersValidationError, ValueError) as error:
            reason = str(error)
        self.ok_btn.setEnabled(not reason)
        self.ok_btn.setToolTip(
            reason or "Run a calculation with the current input parameters"
        )

    def _load_input(self) -> None:
        if choose_and_load_input_parameters(self.session, self):
            self.tree_editor.set_show_changed_only(True)

    def _edit_input_file(self) -> None:
        self.dataset_editor.commit()
        if not self.tree_editor.commit_pending():
            return
        edit_input_parameters_file(self.session, self)

    def _save_input(self) -> None:
        self.dataset_editor.commit()
        if not self.tree_editor.commit_pending():
            return
        choose_and_save_input_parameters(self.session.result(), self)

    def _replace_preset(self) -> None:
        self.dataset_editor.commit()
        if self.tree_editor.commit_pending():
            preset_flows.load_preset(self.session, self, clear_first=True)

    def _merge_preset(self) -> None:
        self.dataset_editor.commit()
        if self.tree_editor.commit_pending():
            preset_flows.load_preset(self.session, self, clear_first=False)

    def _save_preset(self) -> None:
        self.dataset_editor.commit()
        if self.tree_editor.commit_pending():
            preset_flows.save_preset(self.session, self)

    def _on_ok(self) -> None:
        self._validation_error.hide()
        self.dataset_editor.commit()
        if not self.tree_editor.commit_pending():
            return
        try:
            validate_setup(self.session.working_parameters)
        except InputParametersValidationError as error:
            self._validation_error.setText(str(error))
            self._validation_error.show()
            return
        self._update_calculation_availability()
        if self._calculation_mode and not self.ok_btn.isEnabled():
            self._validation_error.setText(self.ok_btn.toolTip())
            self._validation_error.show()
            return
        self.accept()

    def parameters(self) -> InputParameters:
        """Return a detached copy of the currently edited parameters."""
        return self.session.result()


def edit_input_parameters(
    params: InputParameters,
    parent: QWidget | None = None,
    *,
    show_changed_only: bool = False,
    atoms: Any = None,
) -> InputParameters | None:
    """Open the expert editor and return its accepted isolated draft."""
    session = create_input_parameters_session(params)
    accepted = edit_input_parameters_session(
        session,
        parent=parent,
        show_changed_only=show_changed_only,
        atoms=atoms,
    )
    if accepted is None:
        return None
    return accepted.result()


def edit_input_parameters_session(
    session: InputParametersSession,
    parent: QWidget | None = None,
    *,
    show_changed_only: bool = False,
    atoms: Any = None,
) -> InputParametersSession | None:
    """Run Expert over an isolated fork and return the fork only on Accept."""
    dialog = InputParametersDialog(
        session,
        parent=parent,
        show_changed_only=show_changed_only,
        atoms=atoms,
    )
    code = dialog.exec()
    if code == QDialog.DialogCode.Accepted:
        return dialog.session
    return None


def select_input_parameters(
    atoms: Any,
    parent: QWidget | None = None,
    task: str = 'scf',
    *,
    dataset_default_mode: str,
) -> InputParameters | None:
    """Create fresh parameters for a task and open the expert editor."""
    from importlib import import_module

    task_module = import_module(
        f'ase2sprkkr.input_parameters.definitions.{task.lower()}'
    )
    params_def = task_module.input_parameters()
    params = params_def.create_object()
    apply_default_dataset(params, dataset_default_mode, atoms)
    return edit_input_parameters(params, parent=parent, atoms=atoms)