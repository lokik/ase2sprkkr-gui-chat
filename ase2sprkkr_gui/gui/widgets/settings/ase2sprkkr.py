"""Editor for ase2sprkkr's shared (non-GUI) user configuration."""
from __future__ import annotations

import ast
import re
from typing import Any

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr import config
from ase2sprkkr.common.configuration_transaction import ConfigurationTransaction
from ase2sprkkr.common.output_printing import (
    normalize_output_filter,
    output_filter_from_gui as output_filter_value,
)


def _legacy_info_value(value: Any) -> Any:
    """Migrate legacy quoted alternatives containing the special info mode.

    Some older settings contain regex strings such as ('info' | 'ERROR'),
    rather than the tuple ('info', 'ERROR'). Interpret only a complete
    parenthesized expression made of quoted string literals, separated
    by | or commas, and containing 'info'. Other regexes are untouched.
    """
    if not isinstance(value, str):
        return value
    source = value.strip()
    if not (source.startswith("(") and source.endswith(")")):
        return value
    try:
        expression = ast.parse(source, mode="eval").body
    except SyntaxError:
        return value

    def string_items(node: ast.AST) -> tuple[str, ...] | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return (node.value,)
        if isinstance(node, (ast.Tuple, ast.List)):
            items = [string_items(item) for item in node.elts]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            items = [string_items(node.left), string_items(node.right)]
        else:
            return None
        if any(item is None for item in items):
            return None
        return tuple(part for item in items for part in item)

    items = string_items(expression)
    if not items or "info" not in items:
        return value
    return items if len(items) > 1 else "info"


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
