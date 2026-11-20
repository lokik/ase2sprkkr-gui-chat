"""Preferred tasks: persistence, fallback policy, editor and optional task icons."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from ase2sprkkr_gui.gui.application.settings import ApplicationSettings
from ase2sprkkr_gui.gui.dialogs.task_chooser import TaskChoice, order_choices
from ase2sprkkr_gui.gui.input_parameters import task_preferences as prefs
from ase2sprkkr_gui.gui.misc import resources
from ase2sprkkr_gui.gui.misc.icons import task_icon
from ase2sprkkr_gui.gui.widgets.settings.preferred_tasks import PreferredTasksPage


def test_settings_do_not_persist_default_preferred_tasks(tmp_path):
    path = tmp_path / "settings.json"
    settings = ApplicationSettings(path)
    expected = prefs.supported_task_names()
    assert settings.effective_preferred_tasks() == expected
    assert settings.save()
    stored = json.loads(path.read_text())
    assert "preferred_tasks" not in stored
    assert "preferred_supported_tasks" not in stored

    settings.preferred_tasks = ("DOS", "SCF")
    settings.preferred_supported_snapshot = expected
    assert settings.save()
    stored = json.loads(path.read_text())
    assert stored["preferred_tasks"] == ["DOS", "SCF"]
    assert stored["preferred_supported_tasks"] == list(expected)

    loaded = ApplicationSettings(path)
    loaded.load()
    assert loaded.effective_preferred_tasks() == ("DOS", "SCF")
    loaded.preferred_tasks = None
    loaded.preferred_supported_snapshot = None
    assert loaded.save()
    assert "preferred_tasks" not in json.loads(path.read_text())


def test_new_supported_tasks_require_confirmation_and_removed_scf_stays_removed(
    monkeypatch,
):
    monkeypatch.setattr(
        prefs, "supported_task_names", lambda: ("DOS", "SCF", "XAS"),
    )
    monkeypatch.setattr(
        prefs, "all_task_names", lambda: ("DOS", "SCF", "XAS", "ARPES"),
    )
    assert prefs.effective_preferred(None) == ("DOS", "SCF", "XAS")
    assert prefs.effective_preferred(
        ("DOS", "ARPES"), ("DOS", "SCF"),
    ) == ("DOS", "ARPES")


@pytest.mark.parametrize(
    ("preferred", "supported", "ordinary", "experimental", "expected"),
    (
        (("B", "A"), ("A",), ("A", "B"), ("A", "B", "C"), ("B", "A")),
        ((), ("B", "A"), ("B", "A", "C"), ("A", "B", "C"), ("A", "B")),
        ((), (), ("C", "A"), ("A", "B", "C"), ("A", "C")),
        ((), (), (), ("Z", "A"), ("A", "Z")),
    ),
)
def test_task_fallback_priority(
    monkeypatch, preferred, supported, ordinary, experimental, expected,
):
    names = set((*preferred, *supported, *ordinary, *experimental))
    registry = {
        name: SimpleNamespace(experimental=name not in ordinary)
        for name in names
    }
    monkeypatch.setattr(prefs.InputParameters, "task_registry", registry)
    monkeypatch.setattr(prefs, "supported_task_names", lambda: supported)
    monkeypatch.setattr(prefs, "applicable_task_names", lambda _kind: experimental)
    # Preferred action order must never be alphabetized. Fallbacks must.
    assert prefs.displayed_tasks("3d", preferred) == expected


def test_fallback_above_eight_suppresses_tasks_but_not_custom_action(monkeypatch):
    names = tuple(f"T{i:02d}" for i in range(9))
    monkeypatch.setattr(
        prefs.InputParameters, "task_registry",
        {name: SimpleNamespace(experimental=False) for name in names},
    )
    monkeypatch.setattr(prefs, "supported_task_names", lambda: names)
    monkeypatch.setattr(prefs, "applicable_task_names", lambda _kind: names)
    assert prefs.displayed_tasks("3d", ()) == ()
    assert prefs.displayed_tasks("3d", names) == names


def test_task_ordering_prioritizes_possible_preferred_supported_and_nonexperimental():
    rows = [
        TaskChoice("D", True, "Yes", "", preferred=False, supported=False),
        TaskChoice("B", False, "Yes", "", preferred=True, supported=False),
        TaskChoice("C", False, "Yes", "", preferred=False, supported=True),
        TaskChoice("A", True, "No", "", preferred=True, supported=True),
        TaskChoice("E", False, "Yes", "", preferred=True, supported=True),
    ]
    assert [row.name for row in order_choices(rows)] == ["E", "B", "C", "D", "A"]
    assert [row.name for row in order_choices(rows, has_structure=False)] == [
        "E", "A", "B", "C", "D",
    ]


def test_preferred_editor_confirms_scf_removal_and_restores_defaults(
    tmp_path, monkeypatch,
):
    settings = ApplicationSettings(tmp_path / "settings.json")
    page = PreferredTasksPage(settings)
    defaults = prefs.supported_task_names()
    assert page.selection() == defaults
    assert page.preference_override() is None
    index = defaults.index("SCF")
    page.preferred.setCurrentRow(index)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args: QMessageBox.StandardButton.No,
    )
    page.remove_button.click()
    assert "SCF" in page.selection()

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    page.remove_button.click()
    assert "SCF" not in page.selection()
    assert page.preference_override() is not None
    page.restore_button.click()
    assert page.selection() == defaults
    assert page.preference_override() is None
    page.close()


def test_named_task_icons_are_optional_and_reject_path_traversal(
    tmp_path, monkeypatch,
):
    assets = tmp_path / "assets"
    directory = assets / "task-icons"
    directory.mkdir(parents=True)
    (directory / "scf.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">'
        '<circle cx="12" cy="12" r="9"/></svg>',
        encoding="utf-8",
    )
    monkeypatch.setattr(resources, "_ASSETS", assets)
    assert not task_icon("SCF").isNull()
    assert task_icon("DOES_NOT_EXIST").isNull()
    assert resources.task_icon_path("../../anything") is None


@pytest.mark.parametrize(("answer", "open_settings"), (
    (QMessageBox.StandardButton.Yes, False),
    (QMessageBox.StandardButton.No, True),
))
def test_newly_supported_tasks_are_confirmed_once_on_open(
    tmp_path, monkeypatch, answer, open_settings,
):
    from ase2sprkkr_gui.main import GuiApplication
    from ase2sprkkr_gui.gui.application import settings as settings_module

    settings_path = tmp_path / "prefs.json"
    initial = ApplicationSettings(settings_path)
    initial.preferred_tasks = ("DOS",)
    initial.preferred_supported_snapshot = ("DOS",)
    assert initial.save()

    monkeypatch.setattr(settings_module, "supported_task_names",
                        lambda: ("DOS", "SCF"))
    gui = GuiApplication(
        settings_path=settings_path,
        recent_files_path=tmp_path / "recent.json",
    )
    window = gui.create_workflow_window()
    monkeypatch.setattr(gui, "_show_window", lambda *_: None)

    messages = []
    opened = []
    monkeypatch.setattr(
        window, "_open_settings",
        lambda *, preferred_tasks=False: opened.append(preferred_tasks),
    )

    def answer_message(message):
        messages.append((message.text(), message.checkBox().isChecked()))
        message.checkBox().setChecked(open_settings)
        return answer

    monkeypatch.setattr(QMessageBox, "exec", answer_message)
    gui.show_workflow_window()

    assert len(messages) == 1
    assert "SCF" in messages[0][0]
    assert messages[0][1] is False
    assert opened == ([True] if open_settings else [])
    saved = ApplicationSettings(settings_path)
    saved.load()
    assert saved.preferred_supported_snapshot == ("DOS", "SCF")
    assert saved.preferred_tasks == (
        ("DOS", "SCF") if answer == QMessageBox.StandardButton.Yes
        else ("DOS",)
    )
    assert saved.newly_supported_tasks() == ()
    gui.show_workflow_window()
    assert len(messages) == 1
    window.close()


def test_new_supported_tasks_do_not_prompt_with_default_preferences(
    tmp_path, monkeypatch,
):
    from ase2sprkkr_gui.main import GuiApplication

    gui = GuiApplication(
        settings_path=tmp_path / "prefs.json",
        recent_files_path=tmp_path / "recent.json",
    )
    monkeypatch.setattr(gui, "_show_window", lambda *_: None)
    monkeypatch.setattr(
        QMessageBox, "exec",
        lambda *_: pytest.fail("No customized preference list exists"),
    )
    gui.show_workflow_window()
    gui.create_workflow_window().close()


def test_lowercase_task_icon_name_is_required(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    directory = assets / "task-icons"
    directory.mkdir(parents=True)
    (directory / "SCF.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"/>'
    )
    monkeypatch.setattr(resources, "_ASSETS", assets)
    assert resources.task_icon_path("SCF") is None
    (directory / "scf.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"/>'
    )
    assert resources.task_icon_path("SCF") == directory / "scf.svg"
