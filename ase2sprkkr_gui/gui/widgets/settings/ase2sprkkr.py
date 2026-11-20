"""Editor for ase2sprkkr's shared (non-GUI) user configuration."""
from __future__ import annotations

import ast
import re
from typing import Any

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QPushButton,
    QSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr import config
from ase2sprkkr.bindings.empty_spheres import (
    INHOUSE_EMPTY_SPHERES_DEFAULTS,
    configured_empty_spheres_params,
)
from ase2sprkkr.common.configuration_transaction import ConfigurationTransaction
from ase2sprkkr.common.output_printing import (
    normalize_output_filter,
    output_filter_from_gui as output_filter_value,
)


def _legacy_info_value(value: Any) -> Any:
    """Recognize older stringified info tuples without reinterpreting regexes."""
    if not isinstance(value, str) or not value.strip().startswith("("):
        return value
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value
    if parsed == "info":
        return parsed
    if (isinstance(parsed, (tuple, list)) and parsed
            and parsed[0] == "info"
            and all(isinstance(part, str) for part in parsed)):
        return parsed
    return value


def output_filter_selection(value: Any) -> tuple[bool, bool, bool, str] | None:
    """Project core normalization onto three checkboxes and a regex field."""
    try:
        normalized = normalize_output_filter(_legacy_info_value(value))
        if normalized.listeners:
            return None
        return (
            normalized.all_output,
            normalized.info,
            bool(normalized.regexes),
            normalized.regex_text,
        )
    except (TypeError, ValueError, re.error):
        return None


def selected_output_filter_value(
    print_all: bool, print_info: bool, print_regex: bool, regex: str
) -> Any:
    """Build a canonical core configuration value from checkbox state."""
    if print_all:
        return output_filter_value("all", "")
    if print_regex:
        return output_filter_value("info_regex" if print_info else "regex", regex)
    if print_info:
        return output_filter_value("info_regex", "")
    return output_filter_value("none", "")


class Ase2sprkkrSettingsPage(QFrame):
    """Stage global ase2sprkkr settings without changing GUI preferences."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        configuration: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config if configuration is None else configuration
        self._original_empty = self._config.running.empty_spheres()
        self._original_empty_params = configured_empty_spheres_params(self._config)
        self._reset_empty_params = False
        self._original_output = self._config.running.print_output()
        self._original_token = self._config.nomad.token()

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        heading = QLabel("Ase2sprkkr configuration", self)
        font = heading.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        self.scope_notice = QLabel(
            "<b>These are ase2sprkkr library settings, not GUI-specific "
            "preferences.</b> They affect ase2sprkkr Python scripts and the "
            "<code>ase2sprkkr</code> command-line interface as well as "
            "calculations launched from the GUI. GUI-only preferences are "
            "on the General tab.",
            self,
        )
        self.scope_notice.setWordWrap(True)
        layout.addWidget(self.scope_notice)

        running_group = QGroupBox("Calculation defaults", self)
        running_form = QFormLayout(running_group)

        self.empty_spheres = QComboBox(running_group)
        self.empty_spheres.addItem("Automatic (recommended)", "auto")
        self.empty_spheres.addItem("Always", True)
        self.empty_spheres.addItem("Never", False)
        self.empty_spheres.setCurrentIndex(
            self.empty_spheres.findData(self._original_empty)
        )
        self.empty_spheres.setToolTip(
            "Default for finding empty spheres before SPR-KKR calculations. "
            "A calculation may override this setting."
        )
        running_form.addRow("Empty spheres:", self.empty_spheres)

        self.print_all = QCheckBox("Print all output", running_group)
        self.print_info = QCheckBox("Print summary info", running_group)
        self.print_regex = QCheckBox("Print lines matching regex", running_group)
        self.print_all.setToolTip(
            "Print the complete SPR-KKR output. This overrides the other filters."
        )
        self.print_info.setToolTip(
            "Print the processed SCF iteration and convergence summary."
        )
        self.print_regex.setToolTip(
            "Print complete output lines matching the regular expression below."
        )
        output_controls = QWidget(running_group)
        output_layout = QVBoxLayout(output_controls)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(4)
        for option in (self.print_all, self.print_info, self.print_regex):
            output_layout.addWidget(option)
        running_form.addRow("Program output:", output_controls)

        self.output_filter = QLineEdit(running_group)
        self.output_filter.setPlaceholderText("e.g. ERROR|WARNING")
        self.output_filter.setToolTip(
            'One regex (ERROR|WARNING), or separate regexes '
            '("ERROR"|"WARNING"). Double quotes inside quoted patterns.'
        )
        self.regex_label = QLabel("Regex:", running_group)
        self.regex_hint = QLabel(
            'One regex: ERROR|WARNING. Separate regexes: "ERROR"|"WARNING".',
            running_group,
        )
        self.regex_hint.setWordWrap(True)

        selected = output_filter_selection(self._original_output)
        if selected is None:
            # Keep unrepresentable settings unchanged until explicitly edited.
            all_output, info, regex_enabled, regex = False, False, False, ""
        else:
            all_output, info, regex_enabled, regex = selected
        self.print_all.setChecked(all_output)
        self.print_info.setChecked(info)
        self.print_regex.setChecked(regex_enabled)
        self.output_filter.setText(regex)
        # Migrate accidentally stringified info tuples on the next Save.
        self._output_edited = (
            selected is not None
            and _legacy_info_value(self._original_output) != self._original_output
        )

        running_form.addRow(self.regex_label, self.output_filter)
        running_form.addRow("", self.regex_hint)

        self.output_compatibility_note = QLabel(running_group)
        self.output_compatibility_note.setWordWrap(True)
        self.output_compatibility_note.setText(
            "The existing output configuration cannot be represented by "
            "these controls. It will be preserved unless you edit the output filters."
        )
        running_form.addRow("", self.output_compatibility_note)
        self.output_compatibility_note.setVisible(selected is None)

        self.print_all.toggled.connect(self._all_output_changed)
        self.print_info.toggled.connect(self._output_checkbox_changed)
        self.print_regex.toggled.connect(self._output_checkbox_changed)
        self.output_filter.textChanged.connect(self._regex_edited)
        self._update_output_controls()

        layout.addWidget(running_group)

        spheres_group = QGroupBox("Empty-sphere search defaults (XBand)", self)
        spheres_form = QFormLayout(spheres_group)
        default_params = {
            **INHOUSE_EMPTY_SPHERES_DEFAULTS,
            **self._original_empty_params,
        }
        self.empty_min_radius = QDoubleSpinBox(spheres_group)
        self.empty_max_radius = QDoubleSpinBox(spheres_group)
        for widget, value in (
            (self.empty_min_radius, default_params["min_radius"]),
            (self.empty_max_radius, default_params["max_radius"]),
        ):
            widget.setDecimals(4)
            widget.setRange(0.0, 100.0)
            widget.setSingleStep(0.05)
            widget.setSuffix(" Å")
            widget.setValue(float(value))
        spheres_form.addRow("Minimum radius:", self.empty_min_radius)
        spheres_form.addRow("Maximum radius:", self.empty_max_radius)

        self.empty_max_spheres = QSpinBox(spheres_group)
        self.empty_max_spheres.setRange(1, 100000)
        self.empty_max_spheres.setValue(int(default_params["max_spheres"]))
        spheres_form.addRow("Maximum number of spheres:", self.empty_max_spheres)

        mesh_widget = QWidget(spheres_group)
        mesh_layout = QGridLayout(mesh_widget)
        mesh_layout.setContentsMargins(0, 0, 0, 0)
        self.empty_mesh = []
        mesh = default_params["mesh"]
        if isinstance(mesh, int):
            mesh = (mesh,) * 3
        for column, (axis, value) in enumerate(zip("XYZ", mesh)):
            widget = QSpinBox(mesh_widget)
            widget.setRange(1, 10000)
            widget.setValue(int(value))
            mesh_layout.addWidget(QLabel(axis, mesh_widget), 0, column)
            mesh_layout.addWidget(widget, 1, column)
            self.empty_mesh.append(widget)
        spheres_form.addRow("Search mesh:", mesh_widget)

        self.restore_empty_params = QPushButton("Restore library defaults", spheres_group)
        self.restore_empty_params.clicked.connect(self._restore_empty_defaults)
        spheres_form.addRow("", self.restore_empty_params)
        self.empty_params_notice = QLabel(
            "Only changed parameters are saved. These defaults also apply to "
            "Python calculations unless explicitly overridden.",
            spheres_group,
        )
        self.empty_params_notice.setWordWrap(True)
        spheres_form.addRow("", self.empty_params_notice)
        self._initial_empty_values = self._empty_param_values()
        layout.addWidget(spheres_group)

        nomad_group = QGroupBox("NOMAD", self)
        nomad_form = QFormLayout(nomad_group)
        self.nomad_token = QLineEdit(nomad_group)
        self.nomad_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.nomad_token.setPlaceholderText(
            "Configured — leave blank to keep" if self._original_token
            else "Enter an authentication token"
        )
        self.nomad_token.setToolTip(
            "The token is stored in ase2sprkkr's user configuration, "
            "not in the GUI settings."
        )
        self.remove_token = QCheckBox("Remove configured token", nomad_group)
        self.remove_token.setEnabled(self._original_token is not None)
        self.remove_token.toggled.connect(
            lambda checked: self.nomad_token.setEnabled(not checked)
        )
        nomad_form.addRow("Authentication token:", self.nomad_token)
        nomad_form.addRow("", self.remove_token)
        layout.addWidget(nomad_group)
        layout.addStretch(1)

    def _empty_param_values(self) -> dict[str, Any]:
        return {
            "min_radius": self.empty_min_radius.value(),
            "max_radius": self.empty_max_radius.value(),
            "max_spheres": self.empty_max_spheres.value(),
            "mesh": tuple(widget.value() for widget in self.empty_mesh),
        }

    def _restore_empty_defaults(self) -> None:
        defaults = INHOUSE_EMPTY_SPHERES_DEFAULTS
        self.empty_min_radius.setValue(defaults["min_radius"])
        self.empty_max_radius.setValue(defaults["max_radius"])
        self.empty_max_spheres.setValue(defaults["max_spheres"])
        for widget, value in zip(self.empty_mesh, defaults["mesh"]):
            widget.setValue(value)
        self._reset_empty_params = True

    def _all_output_changed(self, checked: bool) -> None:
        if checked:
            # All already includes every raw line, so extra filters are
            # redundant. Keep the controls aligned with core normalization.
            self.print_info.setChecked(False)
            self.print_regex.setChecked(False)
        self._output_checkbox_changed()

    def _output_checkbox_changed(self, _checked: bool = False) -> None:
        self._output_edited = True
        self.output_compatibility_note.setVisible(False)
        self._update_output_controls()

    def _regex_edited(self, _text: str) -> None:
        self._output_edited = True
        self.output_compatibility_note.setVisible(False)

    def _update_output_controls(self) -> None:
        selective = not self.print_all.isChecked()
        self.print_info.setEnabled(selective)
        self.print_regex.setEnabled(selective)
        self.output_filter.setEnabled(
            selective and self.print_regex.isChecked()
        )

    def changes(self) -> list[tuple[Any, Any]]:
        """Validate the draft and return only actual changes."""
        out = []
        empty = self.empty_spheres.currentData()
        if empty != self._original_empty:
            out.append((self._config.running.empty_spheres, empty))

        values = self._empty_param_values()
        if values["min_radius"] > values["max_radius"]:
            raise ValueError("Minimum empty-sphere radius cannot exceed maximum radius.")
        section = self._config.running.empty_spheres_params
        for name, value in values.items():
            if self._reset_empty_params:
                # Restore deletes overrides rather than persisting today's defaults.
                requested = None if value == INHOUSE_EMPTY_SPHERES_DEFAULTS[name] else value
                if requested != self._original_empty_params.get(name):
                    out.append((getattr(section, name), requested))
            elif value != self._initial_empty_values[name]:
                out.append((getattr(section, name), value))

        # Parsing may normalize an existing multi-regex value for display.
        # Preserve the original representation until the user edits the mode
        # or regex text.
        if self._output_edited:
            output = selected_output_filter_value(
                self.print_all.isChecked(),
                self.print_info.isChecked(),
                self.print_regex.isChecked(),
                self.output_filter.text(),
            )
            if output != self._original_output:
                out.append((self._config.running.print_output, output))

        if self.remove_token.isChecked():
            if self._original_token is not None:
                out.append((self._config.nomad.token, None))
        elif self.nomad_token.text():
            token = self.nomad_token.text().strip()
            if not token:
                raise ValueError("NOMAD token cannot consist only of whitespace.")
            if token != self._original_token:
                out.append((self._config.nomad.token, token))
        return out

    def save(self) -> None:
        """Save through ase2sprkkr, rolling back staged values on I/O failure."""
        changes = self.changes()
        if not changes:
            return
        with ConfigurationTransaction.use(self._config):
            for option, value in changes:
                option.set(value)
            self._config.save_user_preferences()

        self._original_empty = self._config.running.empty_spheres()
        self._original_empty_params = configured_empty_spheres_params(self._config)
        self._initial_empty_values = self._empty_param_values()
        self._reset_empty_params = False
        self._original_output = self._config.running.print_output()
        self._output_edited = False
        self._original_token = self._config.nomad.token()
        self.nomad_token.clear()
        self.remove_token.setChecked(False)
        self.remove_token.setEnabled(self._original_token is not None)
        self.nomad_token.setPlaceholderText(
            "Configured — leave blank to keep" if self._original_token
            else "Enter an authentication token"
        )
