"""Shared ASE2SPRKKR settings are not GUI-only preferences."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QLineEdit
from PyQt6.QtCore import Qt

from ase2sprkkr.configuration import definition
from ase2sprkkr_gui.gui.widgets.settings.ase2sprkkr import (
    Ase2sprkkrSettingsPage,
    output_filter_value,
    output_filter_selection,
    selected_output_filter_value,
)


def _app():
    return QApplication.instance() or QApplication([])


def _configuration():
    configuration = definition.create_object()
    configuration.profiles.add("local").executor.set("local")
    return configuration


def _capture_saves(monkeypatch, configuration):
    persisted = []
    monkeypatch.setattr(
        type(configuration),
        "save_user_preferences",
        lambda self: persisted.append(
            self.as_dict(only_changed="explicit", generated=False)
        ),
    )
    return persisted


def test_output_filter_value_translates_modes_and_validates_regex():
    assert output_filter_value("all", "") is True
    assert output_filter_value("none", "") is False
    assert output_filter_value("info_regex", "") == "info"
    assert output_filter_value("info_regex", "ERROR|WARNING") == (
        "info", "ERROR|WARNING"
    )
    assert output_filter_value("regex", "[Ee]rror") == "[Ee]rror"
    assert output_filter_value("regex", '"ERROR"|"WARNING"') == (
        "ERROR", "WARNING"
    )
    assert output_filter_value("info_regex", '"ERROR"|"WARNING"') == (
        "info", "ERROR", "WARNING"
    )
    # Bare "info" is a special value; wrap it to retain regex semantics.
    assert output_filter_value("regex", "info") == "(?:info)"
    assert output_filter_value("info_regex", "info") == ("info", "(?:info)")
    with pytest.raises(ValueError, match="Enter a regular expression"):
        output_filter_value("regex", " ")
    with pytest.raises(ValueError, match="Invalid output regular expression"):
        output_filter_value("info_regex", "[unfinished")


@pytest.mark.parametrize(
    ("stored", "all_output", "info", "matching", "regex"),
    [
        (True, True, False, False, ""),
        (False, False, False, False, ""),
        ("info", False, True, False, ""),
        (("info",), False, True, False, ""),
        (("info", "info"), False, True, False, ""),
        (("ERROR", "WARNING"), False, False, True, '"ERROR"|"WARNING"'),
        (("info", "ERROR", "WARNING"), False, True, True,
         '"ERROR"|"WARNING"'),
        ((False, "info", "WARN"), False, True, True, "WARN"),
        ((True, "info", "WARN"), True, False, False, ""),
        ((False, False), False, False, False, ""),
        ((), False, False, False, ""),
        (("", "ERROR"), False, False, True, '""|"ERROR"'),
        ("('info')", False, True, False, ""),
        ('("info",)', False, True, False, ""),
        ("('info', 'WARN')", False, True, True, "WARN"),
    ],
)
def test_existing_filter_combinations_map_to_checkboxes(
    stored, all_output, info, matching, regex
):
    assert output_filter_selection(stored) == (
        all_output, info, matching, regex
    )


def test_checkbox_state_converts_to_core_filter():
    assert selected_output_filter_value(True, True, True, "[") is True
    assert selected_output_filter_value(False, True, False, "[") == "info"
    assert selected_output_filter_value(False, False, False, "ERROR") is False
    assert selected_output_filter_value(False, True, True, "ERROR") == (
        "info", "ERROR"
    )
    assert selected_output_filter_value(
        False, False, True, '"ERROR"|"WARNING"'
    ) == ("ERROR", "WARNING")
    with pytest.raises(ValueError, match="Enter a regular expression"):
        selected_output_filter_value(False, False, True, "")
    with pytest.raises(ValueError, match="Invalid output regular expression"):
        selected_output_filter_value(False, True, True, "[")


def test_regex_combination_preserves_inline_flags():
    all_output, info, matching, regex = output_filter_selection(
        ("(?i)error", "(?m)^warning")
    )
    assert (all_output, info, matching) == (False, False, True)
    assert regex == '"(?i)error"|"(?m)^warning"'
    from ase2sprkkr.common.output_printing import parse_output_regexes

    patterns = parse_output_regexes(regex)
    assert any(pattern.search("ERROR") for pattern in patterns)
    assert any(pattern.search("warning") for pattern in patterns)
    assert not any(pattern.search("WARNING") for pattern in patterns)


def test_unrepresentable_output_filter_is_detected():
    assert output_filter_selection(("ERROR", 42)) is None


def test_default_output_filter_is_reflected_without_changes():
    _application = _app()
    configuration = _configuration()
    page = Ase2sprkkrSettingsPage(configuration=configuration)

    selected = output_filter_selection(configuration.running.print_output())
    assert selected is not None
    assert (
        page.print_all.isChecked(),
        page.print_info.isChecked(),
        page.print_regex.isChecked(),
        page.output_filter.text(),
    ) == selected
    assert page.changes() == []
    page.close()


def test_shared_settings_page_stages_changes_until_save(monkeypatch):
    _application = _app()
    configuration = _configuration()
    configuration.running.print_output.set("info")
    saved = _capture_saves(monkeypatch, configuration)
    page = Ase2sprkkrSettingsPage(configuration=configuration)

    assert "ase2sprkkr library settings" in page.scope_notice.text()
    page.empty_spheres.setCurrentIndex(page.empty_spheres.findData(True))
    assert page.print_info.isChecked()
    page.print_regex.setChecked(True)
    page.output_filter.setText("ERROR")
    page.nomad_token.setText("new-secret")

    assert configuration.running.empty_spheres() == "auto"
    assert configuration.running.print_output() == "info"
    assert configuration.nomad.token() is None
    assert saved == []

    page.save()
    assert configuration.running.empty_spheres() is True
    assert tuple(configuration.running.print_output()) == ("info", "ERROR")
    assert configuration.nomad.token() == "new-secret"
    assert len(saved) == 1
    assert page.nomad_token.text() == ""
    page.close()


def test_existing_nomad_token_is_masked_and_preserved(monkeypatch):
    _application = _app()
    configuration = _configuration()
    configuration.nomad.token.set("already-configured")
    saved = _capture_saves(monkeypatch, configuration)
    page = Ase2sprkkrSettingsPage(configuration=configuration)

    assert page.nomad_token.echoMode() == QLineEdit.EchoMode.Password
    assert page.nomad_token.text() == ""
    assert "already-configured" not in page.nomad_token.placeholderText()
    page.empty_spheres.setCurrentIndex(page.empty_spheres.findData(False))
    page.save()

    assert configuration.nomad.token() == "already-configured"
    assert len(saved) == 1
    page.close()


def test_nomad_token_can_be_explicitly_removed(monkeypatch):
    _application = _app()
    configuration = _configuration()
    configuration.nomad.token.set("already-configured")
    saved = _capture_saves(monkeypatch, configuration)
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    page.remove_token.setChecked(True)
    page.save()
    assert configuration.nomad.token() is None
    assert len(saved) == 1
    page.close()


def test_no_changes_do_not_rewrite_user_configuration(monkeypatch):
    _application = _app()
    configuration = _configuration()
    saved = _capture_saves(monkeypatch, configuration)
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    page.save()
    assert saved == []
    page.close()


def test_io_failure_rolls_back_staged_configuration(monkeypatch):
    _application = _app()
    configuration = _configuration()
    original = configuration.as_dict(only_changed="explicit", generated=False)

    def fail(_self):
        raise OSError("read-only config")

    monkeypatch.setattr(type(configuration), "save_user_preferences", fail)
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    page.empty_spheres.setCurrentIndex(page.empty_spheres.findData(True))
    with pytest.raises(OSError, match="read-only config"):
        page.save()
    assert configuration.as_dict(only_changed="explicit", generated=False) == original
    page.close()


def test_cancelling_settings_dialog_never_saves_shared_values(tmp_path, monkeypatch):
    from ase2sprkkr_gui.gui.application.settings import ApplicationSettings
    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog

    _application = _app()
    configuration = _configuration()
    saved = _capture_saves(monkeypatch, configuration)
    dialog = SettingsDialog(
        ApplicationSettings(tmp_path / "settings.json"),
        ase_configuration=configuration,
    )
    dialog.ase2sprkkr_page.empty_spheres.setCurrentIndex(
        dialog.ase2sprkkr_page.empty_spheres.findData(True)
    )
    dialog.reject()

    assert configuration.running.empty_spheres() == "auto"
    assert saved == []
    assert not (tmp_path / "settings.json").exists()
    dialog.close()


def test_output_checkboxes_and_regex_field_enablement():
    _application = _app()
    configuration = _configuration()
    configuration.running.print_output.set("info")
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    assert page.print_all.text() == "Print all output"
    assert page.print_info.text() == "Print summary info"
    assert page.print_regex.text() == "Print lines matching regex"
    assert not page.print_all.isChecked()
    assert page.print_info.isChecked()
    assert not page.print_regex.isChecked()
    assert page.output_filter.text() == ""
    assert not page.output_filter.isHidden()
    assert not page.regex_label.isHidden()
    assert not page.output_filter.isEnabled()

    page.print_regex.setChecked(True)
    assert page.output_filter.isEnabled()
    page.output_filter.setText("WARNING")
    assert page.changes() == [
        (page._config.running.print_output, ("info", "WARNING"))
    ]

    page.print_info.setChecked(False)
    assert page.changes() == [
        (page._config.running.print_output, "WARNING")
    ]

    page.print_all.setChecked(True)
    assert page.print_all.isChecked()
    assert not page.print_info.isChecked()
    assert not page.print_regex.isChecked()
    assert not page.print_info.isEnabled()
    assert not page.print_regex.isEnabled()
    assert not page.output_filter.isEnabled()
    assert not page.output_filter.isHidden()
    assert page.changes() == [
        (page._config.running.print_output, True)
    ]

    page.print_all.setChecked(False)
    assert page.print_info.isEnabled()
    assert page.print_regex.isEnabled()
    assert page.changes() == [
        (page._config.running.print_output, False)
    ]
    page.close()


@pytest.mark.parametrize(
    ("stored", "all_output", "info", "matching", "regex"),
    [
        (True, True, False, False, ""),
        (False, False, False, False, ""),
        ("info", False, True, False, ""),
        ("ERROR|WARNING", False, False, True, "ERROR|WARNING"),
        (("info", "ERROR|WARNING"), False, True, True, "ERROR|WARNING"),
    ],
)
def test_output_checkboxes_roundtrip_existing_values(
    stored, all_output, info, matching, regex
):
    _application = _app()
    configuration = _configuration()
    configuration.running.print_output.set(stored)
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    assert page.print_all.isChecked() is all_output
    assert page.print_info.isChecked() is info
    assert page.print_regex.isChecked() is matching
    assert page.output_filter.text() == regex
    assert page.changes() == []
    page.close()


def test_custom_regex_requires_valid_pattern_before_save():
    _application = _app()
    configuration = _configuration()
    configuration.running.print_output.set("info")
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    page.print_info.setChecked(False)
    page.print_regex.setChecked(True)
    with pytest.raises(ValueError, match="Enter a regular expression"):
        page.changes()
    page.output_filter.setText("[")
    with pytest.raises(ValueError, match="Invalid output regular expression"):
        page.changes()
    page.print_regex.setChecked(False)
    assert page.changes() == [(page._config.running.print_output, False)]
    page.close()


def test_settings_dialog_accept_saves_shared_configuration(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QDialog

    from ase2sprkkr_gui.gui.application.execution_profiles import ExecutionProfileStore
    from ase2sprkkr_gui.gui.application.settings import ApplicationSettings
    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog

    _application = _app()
    configuration = _configuration()
    saved = _capture_saves(monkeypatch, configuration)
    dialog = SettingsDialog(
        ApplicationSettings(tmp_path / "settings.json"),
        profile_store=ExecutionProfileStore(configuration),
    )
    profiles = dialog.run_configurations_page
    assert profiles.list.currentItem().data(Qt.ItemDataRole.UserRole) == "local"
    profiles.make_default_button.click()
    assert configuration.default_profile() is None  # Staged until Save.
    page = dialog.ase2sprkkr_page
    page.empty_spheres.setCurrentIndex(page.empty_spheres.findData(False))

    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert configuration.running.empty_spheres() is False
    assert configuration.default_profile() == "local"
    assert saved
    dialog.close()


def test_explicit_default_is_highlighted_and_editable_on_profiles_page(monkeypatch):
    from ase2sprkkr_gui.gui.application.execution_profiles import ExecutionProfileStore
    from ase2sprkkr_gui.gui.widgets.settings.run_configurations import RunConfigurationsPage

    _application = _app()
    configuration = _configuration()
    saved = _capture_saves(monkeypatch, configuration)
    page = RunConfigurationsPage(store=ExecutionProfileStore(configuration))
    item = page.list.currentItem()
    assert item.data(Qt.ItemDataRole.UserRole) == "local"
    assert page.make_default_button.isEnabled()
    assert configuration.default_profile() is None

    page.make_default_button.click()
    assert configuration.default_profile() is None  # Save/OK not pressed.
    item = page.list.currentItem()
    assert item.text().startswith("★ local")
    assert "default" in item.text()
    assert item.font().bold()
    assert not page.make_default_button.isEnabled()
    assert page.automatic_default_button.isEnabled()

    page.save()
    assert configuration.default_profile() == "local"
    assert len(saved) == 1
    page.close()


def test_default_can_be_cleared_without_editing_profile_fields(monkeypatch):
    from ase2sprkkr_gui.gui.application.execution_profiles import ExecutionProfileStore
    from ase2sprkkr_gui.gui.widgets.settings.run_configurations import RunConfigurationsPage

    _application = _app()
    configuration = _configuration()
    configuration.default_profile.set("local")
    saved = _capture_saves(monkeypatch, configuration)
    page = RunConfigurationsPage(store=ExecutionProfileStore(configuration))
    assert page.list.currentItem().text().startswith("★ local")
    assert page.default_status.text() == "Default: local"

    page.automatic_default_button.click()
    assert configuration.default_profile() == "local"
    assert not page.list.currentItem().text().startswith("★")
    assert "Automatic" in page.default_status.text()
    page.save()
    assert configuration.default_profile() is None
    assert len(saved) == 1
    page.close()


def test_deleting_explicit_default_reverts_to_automatic(monkeypatch):
    from ase2sprkkr_gui.gui.application.execution_profiles import ExecutionProfileStore
    from ase2sprkkr_gui.gui.widgets.settings.run_configurations import RunConfigurationsPage

    _application = _app()
    configuration = _configuration()
    configuration.default_profile.set("local")
    saved = _capture_saves(monkeypatch, configuration)
    page = RunConfigurationsPage(store=ExecutionProfileStore(configuration))
    page.delete_button.click()
    assert page._editor.default_profile is None
    assert configuration.default_profile() == "local"  # Still staged.
    page.save()
    assert configuration.default_profile() is None
    assert "local" not in configuration.profiles
    assert len(saved) == 1
    page.close()


def test_loading_multiple_regexes_does_not_rewrite_them_on_other_changes(monkeypatch):
    _application = _app()
    configuration = _configuration()
    original = ("info", "ERROR", "WARNING")
    configuration.running.print_output.set(original)
    saves = _capture_saves(monkeypatch, configuration)

    page = Ase2sprkkrSettingsPage(configuration=configuration)
    assert page.print_info.isChecked()
    assert page.print_regex.isChecked()
    assert not page.print_all.isChecked()
    assert page.output_filter.text() == '"ERROR"|"WARNING"'
    assert page.changes() == []

    page.empty_spheres.setCurrentIndex(page.empty_spheres.findData(True))
    page.save()
    assert tuple(configuration.running.print_output()) == original
    assert configuration.running.empty_spheres() is True
    assert len(saves) == 1

    page.output_filter.setText("ERROR|CRITICAL")
    page.save()
    assert tuple(configuration.running.print_output()) == ("info", "ERROR|CRITICAL")
    assert len(saves) == 2
    page.close()


def test_all_combination_maps_to_all_and_preserves_existing_until_changed(monkeypatch):
    _application = _app()
    configuration = _configuration()
    configuration.running.print_output.set(("info", "ERROR", "WARNING"))
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    assert page.print_info.isChecked()
    assert page.print_regex.isChecked()
    assert page.changes() == []
    page.print_all.setChecked(True)
    assert not page.print_info.isChecked()
    assert not page.print_regex.isChecked()
    assert page.changes() == [(configuration.running.print_output, True)]
    page.close()


@pytest.mark.parametrize("stored", [
    "info",
    ("info",),
    "('info')",
    '("info",)',
])
def test_info_is_a_checkbox_not_a_regex(stored, monkeypatch):
    _application = _app()
    configuration = _configuration()
    configuration.running.print_output.set(stored)
    saved = _capture_saves(monkeypatch, configuration)
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    assert page.print_info.isChecked()
    assert not page.print_regex.isChecked()
    assert not page.print_all.isChecked()
    assert page.output_filter.text() == ""
    if isinstance(stored, str) and stored != "info":
        assert page.changes() == [(configuration.running.print_output, "info")]
        page.save()
        assert configuration.running.print_output() == "info"
        assert len(saved) == 1
    else:
        assert page.changes() == []
    page.close()


def test_regex_text_is_preserved_when_checkbox_disabled(monkeypatch):
    _application = _app()
    configuration = _configuration()
    configuration.running.print_output.set(("info", "ERROR"))
    saved = _capture_saves(monkeypatch, configuration)
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    assert page.print_regex.isChecked()
    page.print_regex.setChecked(False)
    assert not page.output_filter.isEnabled()
    assert page.output_filter.text() == "ERROR"
    page.save()
    assert configuration.running.print_output() == "info"
    assert len(saved) == 1
    page.print_regex.setChecked(True)
    page.save()
    assert tuple(configuration.running.print_output()) == ("info", "ERROR")
    assert len(saved) == 2
    page.close()

def test_empty_sphere_settings_save_partial_overrides_and_restore(monkeypatch):
    _application = _app()
    configuration = _configuration()
    saved = _capture_saves(monkeypatch, configuration)
    page = Ase2sprkkrSettingsPage(configuration=configuration)

    assert page.changes() == []
    page.empty_min_radius.setValue(0.8)
    page.empty_mesh[1].setValue(32)
    assert configuration.running.empty_spheres_params.min_radius() is None
    page.save()

    section = configuration.running.empty_spheres_params
    assert section.min_radius() == 0.8
    assert section.max_radius() is None
    assert tuple(section.mesh()) == (24, 32, 24)
    assert len(saved) == 1

    page.restore_empty_params.click()
    page.save()
    assert section.min_radius() is None
    assert section.mesh() is None
    assert len(saved) == 2
    page.close()


def test_invalid_empty_sphere_radius_range_rejected(monkeypatch):
    _application = _app()
    configuration = _configuration()
    saved = _capture_saves(monkeypatch, configuration)
    page = Ase2sprkkrSettingsPage(configuration=configuration)
    page.empty_min_radius.setValue(3.0)
    with pytest.raises(ValueError, match="cannot exceed"):
        page.save()
    assert saved == []
    page.close()
