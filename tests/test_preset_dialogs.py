"""Guided and Expert expose the same native input preset workflow."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from ase2sprkkr import InputParameters
from ase2sprkkr_gui.gui.dialogs.expert_input import InputParametersDialog
from ase2sprkkr_gui.gui.dialogs.guided_input import GuidedInputParametersDialog


def test_expert_has_preset_buttons():
    app = QApplication.instance() or QApplication([])
    dialog = InputParametersDialog(InputParameters.for_task("DOS"))
    assert dialog.load_preset_btn.text() == "Replace with preset…"
    assert dialog.merge_preset_btn.text() == "Merge preset…"
    assert dialog.save_preset_btn.text() == "Save as preset…"


def test_guided_has_preset_menu():
    app = QApplication.instance() or QApplication([])
    dialog = GuidedInputParametersDialog("dos", InputParameters.for_task("DOS"))
    assert dialog.presets_btn.text() == "Presets…"
    assert [a.text() for a in dialog.presets_menu.actions()] == [
        "Replace with preset…", "Merge preset…", "Save as preset…",
    ]


def test_replace_preset_resets_settings_but_preserves_dataset(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    from ase2sprkkr.input_parameters.presets import PresetStore
    from ase2sprkkr_gui.gui.flows import presets as preset_flows
    from ase2sprkkr_gui.gui.input_parameters.session import InputParametersSession

    app = QApplication.instance() or QApplication([])
    store = PresetStore(tmp_path)
    source = InputParameters.for_task("DOS")
    source.ENERGY.NE.set(402)
    source.CONTROL.DATASET.set("from_preset")
    source.save_preset("preset_1", store=store)

    original = InputParameters.for_task("DOS")
    original.ENERGY.NE.set(422)
    original.TAU.NKTAB.set(777)
    original.CONTROL.DATASET.set("my_dataset")
    session = InputParametersSession(original)
    monkeypatch.setattr(preset_flows, "PresetStore", lambda: store)
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda *_args, **_kwargs: ("preset_1", True))

    assert preset_flows.load_preset(session, None)
    result = session.result()
    assert int(result.ENERGY.NE()[0]) == 402
    assert result.TAU.NKTAB() != 777
    assert str(result.CONTROL.DATASET()) == "my_dataset"
    assert session.undo_stack.count() == 1
    session.undo_stack.undo()
    undone = session.result()
    assert int(undone.ENERGY.NE()[0]) == 422
    assert undone.TAU.NKTAB() == 777
    assert str(undone.CONTROL.DATASET()) == "my_dataset"


def test_merge_preset_keeps_unmentioned_settings(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    from ase2sprkkr.input_parameters.presets import PresetStore
    from ase2sprkkr_gui.gui.flows import presets as preset_flows
    from ase2sprkkr_gui.gui.input_parameters.session import InputParametersSession

    app = QApplication.instance() or QApplication([])
    store = PresetStore(tmp_path)
    source = InputParameters.for_task("DOS")
    source.ENERGY.NE.set(451)
    source.save_preset("mesh", store=store)
    original = InputParameters.for_task("DOS")
    original.TAU.NKTAB.set(711)
    session = InputParametersSession(original)
    monkeypatch.setattr(preset_flows, "PresetStore", lambda: store)
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda *_args, **_kwargs: ("mesh", True))

    assert preset_flows.load_preset(session, None, clear_first=False)
    assert int(session.result().ENERGY.NE()[0]) == 451
    assert session.result().TAU.NKTAB() == 711
    assert session.undo_stack.count() == 1
    session.undo_stack.undo()
    assert session.result().TAU.NKTAB() == 711
    assert int(session.result().ENERGY.NE()[0]) != 451


def test_save_preset_does_not_change_editor_history(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    from ase2sprkkr.input_parameters.presets import PresetStore
    from ase2sprkkr_gui.gui.flows import presets as preset_flows
    from ase2sprkkr_gui.gui.input_parameters.session import InputParametersSession

    app = QApplication.instance() or QApplication([])
    store = PresetStore(tmp_path)
    original = InputParameters.for_task("DOS")
    original.ENERGY.NE.set(471)
    session = InputParametersSession(original)
    monkeypatch.setattr(preset_flows, "PresetStore", lambda: store)
    monkeypatch.setattr(QInputDialog, "getText",
                        lambda *_args, **_kwargs: ("saved", True))

    assert preset_flows.save_preset(session, None)
    assert (tmp_path / "input_parameters" / "dos" / "saved.inp").is_file()
    assert session.undo_stack.count() == 0
    assert not session.is_modified()


def test_guided_bsf_preset_can_switch_task_on_replace(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    from ase2sprkkr.input_parameters.presets import PresetStore
    from ase2sprkkr_gui.gui.flows import presets as preset_flows
    from ase2sprkkr_gui.gui.input_parameters.session import InputParametersSession

    app = QApplication.instance() or QApplication([])
    store = PresetStore(tmp_path)
    source = InputParameters.for_task("BSFEK")
    source.ENERGY.NE.set([270])
    source.save_preset("task:bsfek.ek_mesh", store=store)
    original = InputParameters.for_task("BSFKK")
    session = InputParametersSession(original)
    monkeypatch.setattr(preset_flows, "PresetStore", lambda: store)
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda *_args, **_kwargs: ("task:bsfek.ek_mesh", True))

    assert preset_flows.load_preset(
        session, None, compatible_tasks=("bsfek", "bsfkk"),
    )
    assert session.result().task_name == "BSFEK"
    assert session.undo_stack.count() == 1
    session.undo_stack.undo()
    assert session.result().task_name == "BSFKK"


def test_expert_schema_editor_discovers_both_bsf_tasks(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    from ase2sprkkr.input_parameters.presets import PresetStore
    from ase2sprkkr_gui.gui.flows import presets as preset_flows
    from ase2sprkkr_gui.gui.input_parameters.session import InputParametersSession

    app = QApplication.instance() or QApplication([])
    store = PresetStore(tmp_path)
    ek = InputParameters.for_task("BSFEK")
    ek.ENERGY.NE.set([250])
    ek.save_preset("task:bsfek.ek", store=store)
    kk = InputParameters.for_task("BSFKK")
    kk.ENERGY.NE.set([1])
    kk.save_preset("task:bsfkk.kk", store=store)
    original = InputParameters.for_definition("BSF")
    original.ENERGY.NE.set([2])
    session = InputParametersSession(original)
    selected = []

    def get_item(_parent, _title, _message, choices, **kwargs):
        selected.extend(choices)
        return "task:bsfkk.kk", True

    monkeypatch.setattr(preset_flows, "PresetStore", lambda: store)
    monkeypatch.setattr(QInputDialog, "getItem", get_item)
    assert preset_flows.load_preset(session, None)
    assert "task:bsfek.ek" in selected
    assert "task:bsfkk.kk" in selected
    assert session.result().task_name == "BSFKK"
    session.undo_stack.undo()
    assert session.result().task_name == "BSFEK"


def test_guided_rejects_task_outside_supported_family(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog, QMessageBox
    from ase2sprkkr.input_parameters.presets import PresetStore
    from ase2sprkkr_gui.gui.flows import presets as preset_flows
    from ase2sprkkr_gui.gui.input_parameters.session import InputParametersSession

    app = QApplication.instance() or QApplication([])
    store = PresetStore(tmp_path)
    source = InputParameters.for_task("BSFEK")
    source.ENERGY.NE.set([220])
    source.save_preset("task:bsfek.ek", store=store)
    original = InputParameters.for_task("BSFKK")
    session = InputParametersSession(original)
    errors = []
    monkeypatch.setattr(preset_flows, "PresetStore", lambda: store)
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda *_args, **_kwargs: ("task:bsfek.ek", True))
    monkeypatch.setattr(QMessageBox, "critical",
                        lambda *_args: errors.append(_args[-1]))

    assert not preset_flows.load_preset(
        session, None, compatible_tasks=("bsfkk",),
    )
    assert errors
    assert session.undo_stack.count() == 0
    assert session.result().task_name == "BSFKK"



def test_expert_buttons_dispatch_replace_and_merge(monkeypatch):
    from ase2sprkkr_gui.gui.flows import presets as preset_flows

    app = QApplication.instance() or QApplication([])
    dialog = InputParametersDialog(InputParameters.for_task("DOS"))
    actions = []
    monkeypatch.setattr(
        preset_flows, "load_preset",
        lambda _session, _parent, **options: actions.append(options["clear_first"]),
    )
    dialog.load_preset_btn.click()
    dialog.merge_preset_btn.click()
    assert actions == [True, False]


def test_guided_menu_dispatches_replace_and_merge(monkeypatch):
    from ase2sprkkr_gui.gui.flows import presets as preset_flows

    app = QApplication.instance() or QApplication([])
    dialog = GuidedInputParametersDialog("dos", InputParameters.for_task("DOS"))
    actions = []
    monkeypatch.setattr(dialog, "_commit_pending", lambda: True)
    monkeypatch.setattr(
        preset_flows, "load_preset",
        lambda _session, _parent, **options: actions.append(options["clear_first"]),
    )
    for action in dialog.presets_menu.actions()[:2]:
        action.trigger()
    assert actions == [True, False]
