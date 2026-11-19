from __future__ import annotations

from ase import Atoms

from ase2sprkkr_gui.gui.application.settings import ApplicationSettings
from ase2sprkkr_gui.gui.flows import visualization


def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = ApplicationSettings(path)

    settings.load()
    assert settings.viewer == "ase"

    settings.viewer = "vmd"
    settings.share_python_shell_history = False
    settings.atom_size_percent = 125
    settings.atom_relative_sizes = False
    settings.atom_potential_radii = False
    assert settings.save()

    loaded = ApplicationSettings(path)
    loaded.load()
    assert loaded.viewer == "vmd"
    assert loaded.share_python_shell_history is False
    assert loaded.atom_size_percent == 125
    assert loaded.atom_relative_sizes is False
    assert loaded.atom_potential_radii is False


def test_corrupt_settings_keep_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    settings = ApplicationSettings(path)
    settings.load()

    assert settings.viewer == "ase"
    assert settings.share_python_shell_history is True
    assert settings.atom_size_percent == 100
    assert settings.atom_relative_sizes is True
    assert settings.atom_potential_radii is True


def test_visualization_uses_configured_viewer(monkeypatch):
    class SpecializedAtoms(Atoms):
        pass

    atoms = SpecializedAtoms(
        "Fe",
        positions=[[1.0, 2.0, 3.0]],
        cell=[4.0, 5.0, 6.0],
        pbc=[True, False, True],
    )
    atoms.info["non_viewer_state"] = lambda: None

    class Controller:
        @property
        def atoms(self):
            return atoms

    opened = []
    monkeypatch.setattr(
        visualization,
        "ase_view",
        lambda value, *, viewer: opened.append((value, viewer)),
    )

    settings = ApplicationSettings()
    settings.viewer = "ngl"
    # This test checks handoff to the configured viewer, not availability.
    monkeypatch.setattr(visualization, "viewer_unavailability", lambda name: None)

    assert visualization.visualize_structure(
        Controller(),
        settings,
        parent=None,
    )
    assert len(opened) == 1
    snapshot, viewer = opened[0]
    assert viewer == "ngl"
    assert type(snapshot) is Atoms
    assert snapshot is not atoms
    assert snapshot.symbols == atoms.symbols
    assert (snapshot.positions == atoms.positions).all()
    assert (snapshot.cell == atoms.cell).all()
    assert (snapshot.pbc == atoms.pbc).all()
    assert snapshot.info == {}


class _FakeProfileStore:
    """Isolate settings UI behavior from user configuration files."""

    reserved_names = frozenset()

    def __init__(self):
        self.saved = []
        self.defaults = []

    def default_profile_name(self):
        return None

    def resolved_name(self, name):
        return name

    def drafts(self):
        from ase2sprkkr_gui.gui.application.execution_profiles import ProfileDraft

        return {
            "local": ProfileDraft(
                "local", {"executor": "local", "mpi": "auto"}
            )
        }

    def save(self, drafts, *, default_profile=None):
        self.saved.append({
            name: dict(draft.values) for name, draft in drafts.items()
        })
        self.defaults.append(default_profile)


def test_settings_has_independent_frame_tabs(tmp_path):
    from PyQt6.QtWidgets import QFrame

    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog
    from ase2sprkkr_gui.gui.widgets.settings.general import GeneralSettingsPage
    from ase2sprkkr_gui.gui.widgets.settings.ase2sprkkr import Ase2sprkkrSettingsPage
    from ase2sprkkr_gui.gui.widgets.settings.run_configurations import RunConfigurationsPage

    dialog = SettingsDialog(
        ApplicationSettings(tmp_path / "settings.json"),
        profile_store=_FakeProfileStore(),
    )
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "General", "Run configurations", "Ase2sprkkr"
    ]
    assert all(isinstance(dialog.tabs.widget(i), QFrame) for i in range(3))
    assert isinstance(dialog.general_page, GeneralSettingsPage)
    assert isinstance(dialog.run_configurations_page, RunConfigurationsPage)
    assert isinstance(dialog.ase2sprkkr_page, Ase2sprkkrSettingsPage)
    assert "not GUI-specific" in dialog.ase2sprkkr_page.scope_notice.text()
    assert dialog.general_page.visualization_group.title() == "Visualization"
    assert dialog.general_page.atom_appearance_group.title() == "Atom appearance"
    from PyQt6.QtWidgets import QLabel, QSlider
    assert any(
        item.text() == "External structure viewer:"
        for item in dialog.general_page.visualization_group.findChildren(QLabel)
    )
    assert not dialog.general_page.visualization_group.findChildren(QSlider)
    assert dialog.general_page.atom_appearance_group.findChildren(QSlider)
    assert dialog.general_page.sprkkr_group.title() == "SPR-KKR"
    assert dialog.general_page.python_shell_group.title() == "Python shell"
    assert dialog.share_python_shell_history is (
        dialog.general_page.share_python_shell_history
    )
    assert dialog.share_python_shell_history.isChecked()
    assert dialog.viewer is dialog.general_page.viewer
    assert dialog.dataset_default_mode is dialog.general_page.dataset_default_mode
    dialog.close()


def test_about_is_available_from_settings_without_accepting_changes(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog

    calls = []
    monkeypatch.setattr(QMessageBox, "about", lambda *args: calls.append(args))
    dialog = SettingsDialog(
        ApplicationSettings(tmp_path / "settings.json"),
        profile_store=_FakeProfileStore(),
    )
    dialog.about_button.click()
    assert len(calls) == 1
    assert calls[0][1] == "About Ase2sprkkr"
    assert dialog.result() == 0  # About is informational, not Apply or Cancel.
    dialog.reject()


def test_settings_accept_commits_run_profiles_and_application_defaults(tmp_path):
    from PyQt6.QtWidgets import QDialog

    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog

    settings = ApplicationSettings(tmp_path / "settings.json")
    store = _FakeProfileStore()
    dialog = SettingsDialog(settings, profile_store=store)
    page = dialog.run_configurations_page
    page.form.executable_dir.setText("/opt/sprkkr")
    dataset = dialog.general_page.dataset_default_mode
    dataset.setCurrentIndex(dataset.findData("uuid"))
    dialog.share_python_shell_history.setChecked(False)
    dialog.atom_size_percent.setValue(150)
    dialog.atom_potential_radii.setChecked(False)

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert store.saved[0]["local"]["executable_dir"] == "/opt/sprkkr"
    reloaded = ApplicationSettings(settings.path)
    reloaded.load()
    assert reloaded.dataset_default_mode == "uuid"
    assert reloaded.share_python_shell_history is False
    assert reloaded.atom_size_percent == 150
    assert reloaded.atom_potential_radii is False
    dialog.close()


def test_settings_without_run_edits_does_not_rewrite_profiles(tmp_path):
    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog

    settings = ApplicationSettings(tmp_path / "settings.json")
    store = _FakeProfileStore()
    dialog = SettingsDialog(settings, profile_store=store)
    dialog.accept()
    assert store.saved == []
    dialog.close()


def test_run_profile_edits_survive_switching_profiles(tmp_path):
    from ase2sprkkr_gui.gui.application.execution_profiles import ProfileDraft
    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog

    class TwoProfiles(_FakeProfileStore):
        def drafts(self):
            return {
                "first": ProfileDraft("first", {"executor": "local"}),
                "second": ProfileDraft("second", {"executor": "local"}),
            }

    store = TwoProfiles()
    dialog = SettingsDialog(
        ApplicationSettings(tmp_path / "settings.json"), profile_store=store
    )
    page = dialog.run_configurations_page
    assert page.list.currentRow() == 0
    page.form.executable_dir.setText("/opt/first")
    page.list.setCurrentRow(1)
    page.form.executable_dir.setText("/opt/second")
    dialog.accept()
    assert store.saved[0]["first"]["executable_dir"] == "/opt/first"
    assert store.saved[0]["second"]["executable_dir"] == "/opt/second"
    dialog.close()


def test_settings_cancel_does_not_commit_run_profiles(tmp_path):
    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog

    settings = ApplicationSettings(tmp_path / "settings.json")
    store = _FakeProfileStore()
    dialog = SettingsDialog(settings, profile_store=store)
    dialog.run_configurations_page.form.executable_dir.setText("/opt/other")
    dialog.reject()

    assert store.saved == []
    assert not settings.path.exists()
    dialog.close()


def test_settings_run_profile_save_failure_keeps_dialog_open(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog

    class FailingStore(_FakeProfileStore):
        def save(self, drafts, *, default_profile=None):
            raise OSError("profiles are read-only")

    messages = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda _parent, title, message: messages.append((title, message))
    )
    dialog = SettingsDialog(
        ApplicationSettings(tmp_path / "settings.json"),
        profile_store=FailingStore(),
    )
    dialog.run_configurations_page.form.executable_dir.setText("/opt/test")
    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert messages == [
        ("Cannot Save Run Configurations", "profiles are read-only")
    ]
    dialog.close()


def test_bundled_settings_icon_is_renderable():
    from PyQt6.QtGui import QIcon

    from ase2sprkkr_gui.gui.misc.icons import settings_icon
    from ase2sprkkr_gui.gui.misc.resources import icon_path

    icon = QIcon(str(icon_path("settings-cog.svg")))
    assert not icon.isNull()
    assert not icon.pixmap(24, 24).isNull()
    assert not settings_icon().pixmap(24, 24).isNull()


def test_yaml_parse_error_on_profile_save_does_not_close_settings(tmp_path, monkeypatch):
    from ruamel.yaml.error import YAMLError
    from PyQt6.QtWidgets import QDialog, QMessageBox
    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog

    class BrokenYamlStore(_FakeProfileStore):
        def save(self, drafts, *, default_profile=None):
            raise YAMLError("invalid YAML document")

    messages = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda _parent, title, message: messages.append((title, message)),
    )
    dialog = SettingsDialog(
        ApplicationSettings(tmp_path / "settings.json"),
        profile_store=BrokenYamlStore(),
    )
    dialog.run_configurations_page.form.executable_dir.setText("/new/path")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert messages == [(
        "Cannot Save Run Configurations", "invalid YAML document"
    )]
    dialog.close()


def test_logarithmic_atom_size_range_and_preview_uses_real_marker_areas(tmp_path):
    import numpy as np
    from ase import Atoms
    from ase2sprkkr_gui.gui.plots.atom_sizes import AtomAppearance, marker_areas
    from ase2sprkkr_gui.gui.dialogs.settings import SettingsDialog
    from ase2sprkkr_gui.gui.widgets.settings.general import (
        atom_size_from_slider, slider_from_atom_size,
    )

    settings = ApplicationSettings(tmp_path / "settings.json")
    dialog = SettingsDialog(settings, profile_store=_FakeProfileStore())
    try:
        page = dialog.general_page
        assert (page.atom_size_slider.minimum(), page.atom_size_slider.maximum()) == (-100, 100)
        assert [atom_size_from_slider(position) for position in (-100, 0, 100)] == [10, 100, 1000]
        assert slider_from_atom_size(100) == 0
        for position, expected in ((-100, 10), (0, 100), (100, 1000)):
            page.atom_size_slider.setValue(position)
            assert page.atom_size_percent.value() == expected
        page.atom_size_percent.setValue(250)
        assert page.atom_size_percent.value() == 250  # Exact entry survives rounding.
        assert page.atom_size_slider.value() == slider_from_atom_size(250)
        expected_areas = marker_areas(
            Atoms("CAu"), 48,
            appearance=AtomAppearance(size_percent=250, relative_sizes=True),
        )
        np.testing.assert_allclose(
            page.atom_size_example.markers.get_sizes(), expected_areas
        )
        page.atom_relative_sizes.setChecked(False)
        np.testing.assert_allclose(
            page.atom_size_example.markers.get_sizes(), [300, 300]
        )
        dialog.accept()
        reloaded = ApplicationSettings(settings.path)
        reloaded.load()
        assert reloaded.atom_size_percent == 250
    finally:
        dialog.close()


def test_legacy_and_large_atom_sizes_are_loaded(tmp_path):
    path = tmp_path / "settings.json"
    for percent in (25, 200, 750, 1000):
        settings = ApplicationSettings(path)
        settings.atom_size_percent = percent
        assert settings.save()
        loaded = ApplicationSettings(path)
        loaded.load()
        assert loaded.atom_size_percent == percent
