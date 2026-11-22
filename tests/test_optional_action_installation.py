"""User confirmation is required before any optional package installation."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ase2sprkkr_gui.gui.dialogs import object_view
from ase2sprkkr_gui.gui.dialogs.structures import element_selector


def test_plot_action_installs_only_after_recognized_missing_module(monkeypatch):
    app = QApplication.instance() or QApplication([])
    calls = []
    errors = []

    class Plottable:
        def plot(self):
            calls.append("plot")
            if len(calls) == 1:
                raise ModuleNotFoundError("No module named 'scipy'", name="scipy")

    monkeypatch.setattr(
        object_view, "install_extra",
        lambda extra, parent: calls.append("install:" + extra) or True,
    )
    monkeypatch.setattr(
        object_view.QMessageBox, "critical",
        lambda *args, **kwargs: errors.append(args),
    )
    object_view.execute_value_action(Plottable(), "plot", None)
    assert calls == ["plot", "install:plot", "plot"]
    assert not errors


def test_action_does_not_install_for_unrelated_error(monkeypatch):
    app = QApplication.instance() or QApplication([])
    installs = []
    errors = []

    class Plottable:
        def plot(self):
            raise ModuleNotFoundError("No module named 'internal_broken'", name="internal_broken")

    monkeypatch.setattr(
        object_view, "install_extra",
        lambda extra, parent: installs.append(extra),
    )
    monkeypatch.setattr(
        object_view.QMessageBox, "critical",
        lambda *args, **kwargs: errors.append(args),
    )
    object_view.execute_value_action(Plottable(), "plot", None)
    assert installs == []
    assert errors


def test_nexus_export_receives_only_gui_selected_destination(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    destination = str(tmp_path / "arpes.nxs")
    selections = []
    calls = []

    class Spectrum:
        def path(self):
            return str(tmp_path / "arpes.spc")

        def export_nexus(self, selected):
            calls.append(selected)

    def select(*args):
        selections.append(args)
        return destination, "NeXus files (*.nxs)"

    monkeypatch.setattr(object_view.QFileDialog, "getSaveFileName", select)
    object_view.execute_value_action(Spectrum(), "export_nexus", None)
    assert calls == [destination]
    assert len(selections) == 1


def test_nexus_install_retry_keeps_destination(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    destination = str(tmp_path / "spectrum.nxs")
    selections, calls, installations, errors = [], [], [], []

    class Spectrum:
        def path(self):
            return str(tmp_path / "spectrum.spc")

        def export_nexus(self, selected):
            calls.append(selected)
            if len(calls) == 1:
                raise ModuleNotFoundError("No module named 'h5py'", name="h5py")

    monkeypatch.setattr(
        object_view.QFileDialog, "getSaveFileName",
        lambda *args: (selections.append(args) or destination, ""),
    )
    monkeypatch.setattr(
        object_view, "install_extra",
        lambda extra, parent: installations.append(extra) or True,
    )
    monkeypatch.setattr(
        object_view.QMessageBox, "critical",
        lambda *args, **kwargs: errors.append(args),
    )
    object_view.execute_value_action(Spectrum(), "export_nexus", None)
    assert selections and len(selections) == 1
    assert calls == [destination, destination]
    assert installations == ["nexus"]
    assert not errors


def test_cancelled_nexus_export_never_installs(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    class Spectrum:
        def path(self):
            return str(tmp_path / "spectrum.spc")
        def export_nexus(self, destination):
            raise AssertionError("No export on cancellation")

    monkeypatch.setattr(object_view.QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    monkeypatch.setattr(
        object_view, "install_extra",
        lambda *args: pytest.fail("No installation on cancellation"),
    )
    object_view.execute_value_action(Spectrum(), "export_nexus", None)


def test_element_information_uses_shared_core_provider(monkeypatch):
    from ase2sprkkr.physics import atomic_data

    seen = []
    monkeypatch.setattr(element_selector, "missing_modules", lambda extra: ())
    monkeypatch.setattr(
        atomic_data, "mendeleev_element",
        lambda z: seen.append(z) or SimpleNamespace(
            name="Nickel", symbol="Ni", atomic_weight=58.69,
        ),
        raising=False,
    )
    info = element_selector._load_element_info(28)
    assert seen == [28]
    assert info["name"] == "Nickel"
    assert info["atomic_weight"] == 58.69


def test_element_info_requires_no_mendeleev(monkeypatch):
    monkeypatch.setattr(element_selector, "missing_modules", lambda extra: ("mendeleev",))
    assert element_selector._load_element_info(28) is None
