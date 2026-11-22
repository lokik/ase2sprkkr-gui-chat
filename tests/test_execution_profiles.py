from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.application import execution_profiles
from ase2sprkkr_gui.gui.application.execution_profiles import ProfileDraft
from ase2sprkkr_gui.gui.dialogs import profiles as profiles_dialogs
from ase2sprkkr_gui.gui.widgets.execution_profiles import ProfileForm


def _app():
    return QApplication.instance() or QApplication([])


class _ReadOnlyStore:
    def default_profile_name(self):
        return None

    def resolved_name(self, name):
        return "ase" if name is None else name

    def drafts(self):
        return {
            "ase": ProfileDraft(
                "ase",
                {"executor": "local"},
                read_only=True,
            )
        }


def test_profile_form_changes_fields_with_execution_type():
    _application = _app()
    form = ProfileForm()

    form.kind.setCurrentIndex(form.kind.findData("local"))
    assert form.host.isHidden()
    assert form.psij_backend.isHidden()

    form.kind.setCurrentIndex(form.kind.findData("ssh"))
    assert not form.host.isHidden()
    assert form.psij_backend.isHidden()

    form.kind.setCurrentIndex(form.kind.findData("ssh-psij"))
    assert not form.host.isHidden()
    assert not form.psij_backend.isHidden()
    form.close()


def test_profile_form_parses_mpi_and_backend_specific_values():
    _application = _app()
    form = ProfileForm()
    form.kind.setCurrentIndex(form.kind.findData("ssh-psij"))
    form.mpi.set_value(8)
    form.host.setText("user@cluster")
    form.ssh_options.setText("-p 2222")
    form.psij_backend.setText("slurm")

    values = form.values()

    assert values["executor"] == "ssh"
    assert values["mpi"] == 8
    assert values["mpi_warning"] is True
    assert values["host"] == "user@cluster"
    assert values["ssh_options"] == ["-p", "2222"]
    assert values["psij_backend"] == "slurm"
    assert values["backend"] == "psij"
    form.close()


def test_read_only_profile_cannot_be_deleted_or_edited():
    _application = _app()
    dialog = profiles_dialogs.ProfilesDialog(store=_ReadOnlyStore())

    assert not dialog.delete_button.isEnabled()
    assert not dialog.form.form_widget.isEnabled()
    dialog.close()


def test_standalone_profile_dialog_reuses_run_settings_page():
    from ase2sprkkr_gui.gui.widgets.settings.run_configurations import RunConfigurationsPage

    dialog = profiles_dialogs.ProfilesDialog(store=_ReadOnlyStore())
    assert isinstance(dialog.page, RunConfigurationsPage)
    assert dialog.form is dialog.page.form
    assert dialog.delete_button is dialog.page.delete_button
    assert dialog.findChildren(RunConfigurationsPage) == [dialog.page]
    dialog.close()


def test_profiles_dialog_handles_yaml_error_on_save(monkeypatch):
    from ruamel.yaml.error import YAMLError
    from PyQt6.QtWidgets import QDialog, QMessageBox

    class FailingStore(_ReadOnlyStore):
        def drafts(self):
            return {
                "local": ProfileDraft(
                    "local", {"executor": "local"}, read_only=False
                )
            }

        def resolved_name(self, name):
            return "local" if name is None else name

        def save(self, drafts, *, default_profile=None):
            raise YAMLError("invalid YAML document")

    messages = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda _parent, title, message: messages.append((title, message)),
    )
    dialog = profiles_dialogs.ProfilesDialog(store=FailingStore())
    dialog.form.executable_dir.setText("/new/path")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert messages == [("Cannot Save Profiles", "invalid YAML document")]
    dialog.close()


def test_profile_summary_fields_preserve_the_legacy_description(monkeypatch):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        executor="local",
        backend_config=SimpleNamespace(name="direct"),
        mpi="auto",
        executable_dir="/home/user/SPRKKR",
        executable_suffix="",
    )
    monkeypatch.setattr(
        execution_profiles, "resolve_sprkkr_profile",
        lambda _name, *, configuration=None: profile,
    )

    assert execution_profiles.profile_summary_fields(None) == (
        ("Execution", "Local execution · direct"),
        ("Executables", "/home/user/SPRKKR"),
        ("MPI", "automatic"),
    )
    assert execution_profiles.profile_description(None) == (
        "Local execution · direct · MPI automatic\\n"
        "Executables: /home/user/SPRKKR"
    )
