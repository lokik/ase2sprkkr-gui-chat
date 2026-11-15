"""Optional extras are explicit, user-approved, and action-scoped."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ase2sprkkr_gui.gui.application import optional_dependencies as extras


def test_requirements_use_core_distribution_metadata(monkeypatch):
    distribution = SimpleNamespace(requires=(
        'h5py>=3.8; extra == "nexus"',
        'nexusformat>=1.0; extra == "nexus"',
        'scipy; extra == "plot"',
        'scikit-image; extra == "plot"',
        'pytest>=9',
    ))
    monkeypatch.setattr(extras.metadata, "distribution", lambda _: distribution)
    assert extras.requirements_for_extra("nexus") == ("h5py>=3.8", "nexusformat>=1.0")
    assert extras.requirements_for_extra("plot") == ("scipy", "scikit-image")


def test_undeclared_extra_is_not_installed(monkeypatch):
    distribution = SimpleNamespace(requires=('pytest>=9',))
    monkeypatch.setattr(extras.metadata, "distribution", lambda _: distribution)
    with pytest.raises(ValueError, match="does not declare"):
        extras.requirements_for_extra("plot")


def test_relevant_missing_module_only():
    h5py = ModuleNotFoundError("No module named 'h5py'", name="h5py")
    assert extras.action_extra("export_nexus", h5py) == "nexus"
    assert extras.action_extra("plot", h5py) is None
    assert extras.action_extra("open", h5py) is None
    broken = ModuleNotFoundError("No module named 'unrelated'", name="unrelated")
    assert extras.action_extra("plot", broken) is None
    assert extras.action_extra("export_nexus", broken) is None


def test_environment_chooses_user_pip_outside_venv(monkeypatch):
    monkeypatch.setattr(extras, "requirements_for_extra", lambda extra: ("scipy",))
    monkeypatch.setattr(extras, "_in_environment", lambda: False)
    monkeypatch.setattr(extras.sysconfig, "get_path", lambda _: "/unlikely-stdlib-path")
    monkeypatch.setattr(extras.site, "ENABLE_USER_SITE", True)
    monkeypatch.setattr(extras.importlib.util, "find_spec", lambda _: object())
    assert extras.pip_command("plot")[-2:] == ("--user", "scipy")


def test_environment_omits_user_pip_in_venv(monkeypatch):
    monkeypatch.setattr(extras, "requirements_for_extra", lambda extra: ("scipy",))
    monkeypatch.setattr(extras, "_in_environment", lambda: True)
    monkeypatch.setattr(extras.importlib.util, "find_spec", lambda _: object())
    assert "--user" not in extras.pip_command("plot")


def test_declining_install_does_not_start_a_process(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(extras, "pip_command", lambda extra: ("python", "-m", "pip", "install", "scipy"))
    monkeypatch.setattr(extras, "requirements_for_extra", lambda extra: ("scipy",))
    monkeypatch.setattr(
        extras.QMessageBox, "question",
        lambda *args: QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(
        extras, "_InstallDialog",
        lambda *args: pytest.fail("Installer must not start after cancellation"),
    )
    assert extras.install_extra("plot", None) is False


def test_pep668_environment_is_not_modified(monkeypatch, tmp_path):
    marker = tmp_path / "EXTERNALLY-MANAGED"
    marker.write_text("managed", encoding="utf-8")
    monkeypatch.setattr(extras, "requirements_for_extra", lambda extra: ("scipy",))
    monkeypatch.setattr(extras, "_in_environment", lambda: False)
    monkeypatch.setattr(extras.sysconfig, "get_path", lambda _: str(tmp_path))
    with pytest.raises(RuntimeError, match="externally managed"):
        extras.pip_command("plot")
