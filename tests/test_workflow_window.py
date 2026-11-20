import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ase2sprkkr_gui-test-matplotlib")

from ase import Atoms
from types import SimpleNamespace
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import QApplication, QAbstractButton, QFrame, QGroupBox, QLabel, QMessageBox, QSplitter, QToolButton, QWidget


from ase2sprkkr_gui.gui.application.workspace import WorkspaceState
from ase2sprkkr_gui.gui.dialogs import workflow_window as workflow_module
from ase2sprkkr_gui.gui.workflow.model import (
    Build2D,
    Build2DKind,
    BuildEmbedding,
    ExplainEmbedding,
    ContinueRecent,
    CreateStructure,
    CreateStructureKind,
    EditStructure,
    Load,
    LoadKind,
    LoadHostPotential,
    AttachHostPotential,
    CreateHostPotential,
    RunTask,
    ReconvergeScf,
    CustomTask,
    SaveStructure,
    StartOver,
    UpdateEmptySpheres,
    UseForNewCalculation,
    VisualizeStructure,
)
from ase2sprkkr_gui.main import GuiApplication


def _workflow(tmp_path, *, workspace=None):
    gui = GuiApplication(
        workspace=workspace,
        recent_files_path=tmp_path / "recent.json",
        settings_path=tmp_path / "settings.json",
    )
    return gui, gui.create_workflow_window()


def _commands(window):
    return tuple(
        action.command
        for group in window.workflow_plan.groups
        for action in group.actions
    )


def test_results_load_button_imports_output_in_guided(tmp_path, monkeypatch):
    _application = QApplication.instance() or QApplication([])
    gui, guided = _workflow(tmp_path)
    imports = []
    monkeypatch.setattr(
        workflow_module.file_flows, "choose_and_load_output",
        lambda controller, history, parent: imports.append((controller, history, parent)),
    )
    assert guided.result_actions_widget.load_result_button.isEnabled()
    guided.result_actions_widget.load_result_button.click()
    assert imports == [(gui.controller, gui.recent_files, guided)]
    guided.close()

def test_result_import_directory_is_wired_in_guided(tmp_path, monkeypatch):
    _application = QApplication.instance() or QApplication([])
    gui, guided = _workflow(tmp_path)
    imports = []
    monkeypatch.setattr(
        workflow_module.file_flows,
        "choose_and_load_output_directory",
        lambda controller, history, parent: imports.append((controller, history, parent)),
    )

    assert guided.result_actions_widget.import_directory_button.isEnabled()
    guided.result_actions_widget.import_directory_button.click()
    assert imports == [(gui.controller, gui.recent_files, guided)]
    guided.close()


def test_navigation_shortcuts_are_exposed_in_tooltips(tmp_path):
    _application = QApplication.instance() or QApplication([])
    _gui, guided = _workflow(tmp_path)
    assert guided._settings_button.shortcut().toString() == "Ctrl+,"
    assert guided._close_button.shortcut().toString() == "Ctrl+Q"
    assert "Ctrl+," in guided._settings_button.toolTip()
    assert "Ctrl+Q" in guided._close_button.toolTip()
    guided.close()


def test_workflow_uses_one_workspace_without_expert_shell(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    workspace = WorkspaceState()
    gui, window = _workflow(tmp_path, workspace=workspace)

    assert not hasattr(gui, "create_main_window")
    gui.controller.replace_structure(Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True))
    application.processEvents()

    assert workspace.structure.value.atoms is gui.controller.atoms
    assert RunTask("scf") in _commands(window)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )
    window.close()

def test_action_columns_classify_embedded_and_restart_workflows():
    from ase2sprkkr_gui.gui.widgets.workflow_actions import _action_column
    from ase2sprkkr_gui.gui.workflow.model import (
        ActionIcon, AvailableAction, ConvergeHostScf,
    )

    def column(command):
        return _action_column(
            AvailableAction(command, "test", "", "neutral", ActionIcon.OPEN)
        )

    assert column(RunTask("dos")) == "calculate"
    assert column(ReconvergeScf()) == "calculate"
    assert column(UpdateEmptySpheres()) == "calculate"
    assert column(ConvergeHostScf()) == "calculate"
    assert column(ConvergeHostScf(restart_scf=True)) == "calculate"
    assert column(UseForNewCalculation()) == "calculate"
    assert column(EditStructure()) == "structure"
    assert column(Load(LoadKind.STRUCTURE)) == "structure"
    assert column(CreateStructure(CreateStructureKind.THREE_D)) == "structure"
    assert column(LoadHostPotential()) == "structure"
    assert column(AttachHostPotential()) == "structure"
    assert column(CreateHostPotential()) == "structure"
    assert column(BuildEmbedding()) == "structure"
    assert column(StartOver()) == "structure"
    assert column(CustomTask()) == "calculate"


def test_structure_and_calculation_columns_track_workspace_state(tmp_path):
    application = QApplication.instance() or QApplication([])
    _gui, window = _workflow(tmp_path)

    def names(widget):
        return {
            button.accessibleName()
            for button in widget.findChildren(QAbstractButton)
        }

    assert window._structure_title.text() == "Start with..."
    assert window._subtitle.isHidden()
    assert window.findChild(QLabel, "workflowCalculateTitle").text() == "Calculate"
    assert "Create a 3D Structure" in names(window._structure_actions)
    assert "Load a Structure" in names(window._structure_actions)
    assert window._actions.has_actions
    assert "Custom input file" in names(window._actions)

    window.controller.replace_structure(
        Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
    )
    application.processEvents()

    assert window._structure_title.text() == "Structure"
    assert not window._structure_overview.isHidden()
    assert "Edit Structure" in names(window._structure_actions)
    assert "Visualize Structure" in names(window._structure_actions)
    assert "Save Structure" in names(window._structure_actions)
    assert window._actions.has_actions
    assert "Edit Structure" not in names(window._actions)
    assert "Visualize Structure" not in names(window._actions)
    assert all("SCF" not in name for name in names(window._structure_actions))

    window.controller.reset()
    application.processEvents()
    assert window._structure_title.text() == "Start with..."
    assert window._actions.has_actions
    assert "Custom input file" in names(window._actions)
    window.close()


def test_column_headings_align_and_results_resize_both_action_columns(tmp_path):
    application = QApplication.instance() or QApplication([])
    _gui, window = _workflow(tmp_path)
    window.show()
    application.processEvents()

    structure = window.findChild(QWidget, "workflowStructureColumn")
    action_columns = window.findChild(QWidget, "workflowActionColumns")
    assert structure is not None
    assert action_columns is not None
    calculate = window.findChild(QLabel, "workflowCalculateTitle")
    results = window.findChild(QLabel, "workflowResultsTitle")
    assert calculate is not None and results is not None

    headings = (window._structure_title, calculate, results)
    assert len({heading.font().pointSizeF() for heading in headings}) == 1
    assert len({heading.mapTo(window, QPoint(0, 0)).y() for heading in headings}) == 1

    # The sections and their first action cards must share a top baseline,
    # not just the three top-level column titles.
    create_heading = next(
        label
        for label in window._structure_actions.findChildren(QLabel, "workflowActionGroupLabel")
        if label.text() == "Create structure"
    )
    custom_heading = next(
        label
        for label in window._actions.findChildren(QLabel, "workflowActionGroupLabel")
        if label.text() == "Custom"
    )
    assert create_heading.mapTo(window, QPoint(0, 0)).y() == custom_heading.mapTo(window, QPoint(0, 0)).y()

    create_card = next(
        card
        for card in window._structure_actions.findChildren(QAbstractButton)
        if card.accessibleName() == "Create a 3D Structure"
    )
    custom_card = next(
        card
        for card in window._actions.findChildren(QAbstractButton)
        if card.accessibleName() == "Custom input file"
    )
    assert create_card.mapTo(window, QPoint(0, 0)).y() == custom_card.mapTo(window, QPoint(0, 0)).y()

    splitter = window._layout_splitter
    assert isinstance(splitter, QSplitter)
    assert splitter.count() == 2
    assert splitter.widget(0) is action_columns
    assert splitter.widget(1) is results.parentWidget()

    # Both action columns have equal stretch, and share the space released
    # when the user makes the Results sidebar wider.
    initial_pair_width = action_columns.width()
    initial_structure_width = structure.width()
    initial_calculation_width = calculate.parentWidget().width()
    initial_results_width = splitter.widget(1).width()
    assert abs(initial_structure_width - initial_calculation_width) <= 1

    splitter.moveSplitter(splitter.sizes()[0] - 80, 1)
    application.processEvents()

    assert splitter.widget(1).width() > initial_results_width
    assert action_columns.width() < initial_pair_width
    assert structure.width() < initial_structure_width
    assert calculate.parentWidget().width() < initial_calculation_width
    assert abs(structure.width() - calculate.parentWidget().width()) <= 1
    window.close()



def test_recent_potential_load_rebuilds_workflow_once(tmp_path, monkeypatch):
    """Changing a recent position must not rebuild the loaded structure twice."""
    application = QApplication.instance() or QApplication([])
    gui, window = _workflow(tmp_path)
    old_path = tmp_path / "old.pot"
    other_path = tmp_path / "other.pot"
    old_path.touch()
    other_path.touch()
    gui.recent_files.remember("structure", old_path)
    gui.recent_files.remember("structure", other_path)

    atoms = Atoms("Fe", cell=(3, 3, 3), pbc=True)
    monkeypatch.setattr(
        workflow_module.file_flows.Potential, "is_cluster_file",
        staticmethod(lambda _path: False),
    )

    def load_candidate(_path, _parent, *, reader, on_loaded):
        candidate = SimpleNamespace(atoms=atoms)
        on_loaded(candidate)
        return candidate

    monkeypatch.setattr(
        workflow_module.file_flows, "load_structure_candidate", load_candidate,
    )

    rendered = []
    for widget in (window._structure_actions, window._actions):
        original = widget.set_plan

        def count(plan, *, original=original):
            rendered.append(plan)
            original(plan)

        monkeypatch.setattr(widget, "set_plan", count)

    window._continue_recent("structure", str(old_path))
    assert gui.controller.atoms is atoms
    assert gui.recent_files.paths("structure")[0] == str(old_path)
    assert len(rendered) == 2  # One refresh, two action columns.

    window.close()


def test_recent_history_still_refreshes_empty_workspace(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    gui, window = _workflow(tmp_path)
    rendered = []
    original = window._structure_actions.set_plan

    def count(plan):
        rendered.append(plan)
        original(plan)

    monkeypatch.setattr(window._structure_actions, "set_plan", count)
    gui.recent_files.remember("structure", tmp_path / "earlier.xyz")
    assert len(rendered) == 1
    window.close()


def test_workspace_signals_refresh_semantic_plan(tmp_path):
    application = QApplication.instance() or QApplication([])
    _gui, window = _workflow(tmp_path)

    assert CreateStructure(CreateStructureKind.THREE_D) in _commands(window)

    window.controller.replace_structure(
        Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
    )
    application.processEvents()
    assert RunTask("scf") in _commands(window)
    assert CreateStructure(CreateStructureKind.THREE_D) not in _commands(window)

    window.controller.reset()
    application.processEvents()
    assert CreateStructure(CreateStructureKind.THREE_D) in _commands(window)
    window.close()


def test_python_bulk_keeps_embedding_action_reachable_in_small_window(tmp_path, monkeypatch):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QPushButton, QScrollArea

    from ase2sprkkr_gui.gui.application.shell_history import PythonReplHistory, ShellHistory
    from ase2sprkkr_gui.gui.dialogs.structures import shell

    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard
    )
    _gui, window = _workflow(tmp_path)

    def select_from_python(parent, **kwargs):
        dialog = shell.StructureShellDialog(
            parent, **kwargs,
            history=ShellHistory(tmp_path / "shell.json"),
            python_history=PythonReplHistory(tmp_path / "python_history"),
        )
        QTimer.singleShot(0, lambda: dialog.console.process_input(
            "use(bulk('Fe', 'bcc', a=2.87, cubic=True))"
        ))
        assert dialog.exec() == dialog.DialogCode.Accepted
        return dialog.candidate

    monkeypatch.setattr(shell, "select_shell_structure", select_from_python)
    try:
        window.show()
        window.execute_workflow_command(CreateStructure(CreateStructureKind.SHELL))
        application.processEvents()
        assert window.controller.structure_kind() == "3d"
        assert BuildEmbedding() in _commands(window)

        window.resize(1120, 600)
        application.processEvents()
        assert window.height() == 600
        scroll = window.findChild(QScrollArea, "workflowStructureActionsScrollArea")
        assert scroll is not None
        buttons = window._structure_actions.findChildren(QPushButton)
        embedding = next(b for b in buttons if b.accessibleName() == "Build Embedded Cluster")
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.ensureWidgetVisible(embedding, 0, 0)
        application.processEvents()
        top_left = embedding.mapTo(scroll.viewport(), embedding.rect().topLeft())
        assert scroll.viewport().rect().contains(top_left)
        assert scroll.viewport().rect().contains(
            embedding.mapTo(scroll.viewport(), embedding.rect().bottomRight())
        )
        # Scrolling must preserve complete cards and their spacing inside a group.
        siblings = embedding.parentWidget().findChildren(QPushButton)
        for above, below in zip(siblings, siblings[1:]):
            assert above.geometry().bottom() < below.geometry().top()
    finally:
        window.close()


def test_typed_commands_are_dispatched_without_title_interpretation(
    tmp_path, monkeypatch
):
    _application = QApplication.instance() or QApplication([])
    _gui, window = _workflow(tmp_path)
    events = []

    monkeypatch.setattr(
        window,
        "_prepare_task",
        lambda task: events.append(("task", task)),
    )
    monkeypatch.setattr(window, "_update_empty_spheres", lambda: events.append("spheres"))
    monkeypatch.setattr(window, "_create_3d", lambda: events.append("create-3d"))
    monkeypatch.setattr(window, "_create_shell", lambda: events.append("create-shell"))
    monkeypatch.setattr(
        window, "_create_from_database", lambda: events.append("prototype")
    )
    monkeypatch.setattr(window, "_create_surface", lambda: events.append("surface"))
    monkeypatch.setattr(
        window, "_create_transition", lambda: events.append("transition")
    )
    monkeypatch.setattr(
        window,
        "_build_2d",
        lambda _checked=False, *, surface_mode=False: events.append(
            ("build-2d", surface_mode)
        ),
    )
    monkeypatch.setattr(
        workflow_module.structure_flows, "build_embedding_structure",
        lambda *args: events.append("build-embedding"),
    )
    monkeypatch.setattr(window, "_download_structure", lambda: events.append("download"))
    monkeypatch.setattr(window, "_load_structure", lambda: events.append("load-structure"))
    monkeypatch.setattr(window, "_load_output", lambda: events.append("load-output"))
    monkeypatch.setattr(
        window,
        "_continue_recent",
        lambda kind, path: events.append(("recent", kind, path)),
    )
    monkeypatch.setattr(window, "_visualize_structure", lambda: events.append("view"))
    monkeypatch.setattr(
        workflow_module.structure_flows, "edit_structure",
        lambda *_args, **_kwargs: events.append("edit"),
    )
    monkeypatch.setattr(window, "_save_structure", lambda: events.append("save"))
    monkeypatch.setattr(
        workflow_module.file_flows, "load_host_potential",
        lambda *_args: events.append("load-host"),
    )
    monkeypatch.setattr(
        workflow_module.file_flows, "attach_host_potential",
        lambda *_args: events.append("attach-host"),
    )
    monkeypatch.setattr(
        workflow_module.file_flows, "create_host_potential",
        lambda *_args: events.append("create-host"),
    )
    monkeypatch.setattr(
        window, "_use_for_new_calculation", lambda: events.append("new-calculation")
    )
    monkeypatch.setattr(window, "_start_over", lambda: events.append("reset"))

    commands = (
        RunTask("scf"),
        UpdateEmptySpheres(),
        CreateStructure(CreateStructureKind.THREE_D),
        CreateStructure(CreateStructureKind.PROTOTYPE),
        CreateStructure(CreateStructureKind.SHELL),
        CreateStructure(CreateStructureKind.SURFACE),
        CreateStructure(CreateStructureKind.TWO_D),
        Build2D(Build2DKind.SURFACE),
        Build2D(Build2DKind.STRUCTURE),
        BuildEmbedding(),
        Load(LoadKind.ONLINE_STRUCTURE),
        Load(LoadKind.STRUCTURE),
        Load(LoadKind.OUTPUT),
        ContinueRecent("output", "/tmp/result.out"),
        VisualizeStructure(),
        EditStructure(),
        SaveStructure(),
        LoadHostPotential(),
        AttachHostPotential(),
        CreateHostPotential(),
        UseForNewCalculation(),
        StartOver(),
    )
    for command in commands:
        window.execute_workflow_command(command)

    assert events == [
        ("task", "scf"),
        "spheres",
        "create-3d",
        "prototype",
        "create-shell",
        "surface",
        "transition",
        ("build-2d", True),
        ("build-2d", False),
        "build-embedding",
        "download",
        "load-structure",
        "load-output",
        ("recent", "output", "/tmp/result.out"),
        "view",
        "edit",
        "save",
        "load-host",
        "attach-host",
        "create-host",
        "new-calculation",
        "reset",
    ]
    window.close()


def test_cancelled_first_stage_preserves_existing_structure(tmp_path, monkeypatch):
    _gui, window = _workflow(tmp_path)
    existing = Atoms("Cu", cell=(2.8, 2.8, 2.8), pbc=True)
    window.controller.replace_structure(existing)
    calls = []
    monkeypatch.setattr(
        workflow_module.structure_flows, "create_structure_candidate",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        workflow_module.structure_flows, "select_build_2d_structure",
        lambda *args, **kwargs: calls.append(True),
    )

    window._create_surface()

    assert calls == []
    assert window.controller.atoms is existing
    window.close()


def test_new_2d_flow_only_publishes_final_structure(tmp_path, monkeypatch):
    from ase2sprkkr_gui.gui.flows.structure_candidates import StructureCandidate

    _gui, window = _workflow(tmp_path)
    bulk = Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
    layer = Atoms("Ni", cell=(3, 3, 9), pbc=(True, True, False))
    original_generation = window.controller.structure_generation
    original_atoms = window.controller.atoms
    monkeypatch.setattr(
        workflow_module.structure_flows, "create_structure_candidate",
        lambda *args, **kwargs: StructureCandidate(bulk),
    )

    def select(candidate, **kwargs):
        assert candidate is not bulk
        assert window.controller.atoms is original_atoms
        assert window.controller.structure_generation == original_generation
        assert kwargs["surface_mode"] is True
        return layer

    monkeypatch.setattr(
        workflow_module.structure_flows, "select_build_2d_structure", select,
    )
    window._create_surface()

    assert window.controller.atoms is layer
    assert window.controller.structure_generation == original_generation.next()
    window.close()


import pytest


@pytest.mark.parametrize("keep_bulk", [False, True])
def test_cancel_new_2d_prompts_to_keep_or_discard_3d(tmp_path, monkeypatch, keep_bulk):
    from PyQt6.QtWidgets import QMessageBox
    from ase2sprkkr_gui.gui.flows.structure_candidates import StructureCandidate

    _gui, window = _workflow(tmp_path)
    original = Atoms("Cu", cell=(2.8, 2.8, 2.8), pbc=True)
    window.controller.replace_structure(original)
    original_generation = window.controller.structure_generation
    bulk = Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
    prompts = []
    monkeypatch.setattr(
        workflow_module.structure_flows, "create_structure_candidate",
        lambda *args, **kwargs: StructureCandidate(bulk),
    )
    monkeypatch.setattr(
        workflow_module.structure_flows, "select_build_2d_structure",
        lambda *args, **kwargs: None,
    )

    def ask(*args, **kwargs):
        prompts.append(args)
        assert window.controller.atoms is original
        return QMessageBox.StandardButton.Yes if keep_bulk else QMessageBox.StandardButton.No

    monkeypatch.setattr(workflow_module.structure_flows.QMessageBox, "question", ask)
    window._create_transition()

    assert len(prompts) == 1
    assert window.controller.atoms is (bulk if keep_bulk else original)
    assert window.controller.structure_generation == (
        original_generation.next() if keep_bulk else original_generation
    )
    window.close()


def test_workflow_navigation_is_one_bottom_panel_with_four_icon_buttons(
    tmp_path, monkeypatch
):
    _application = QApplication.instance() or QApplication([])
    gui, window = _workflow(tmp_path)

    navigation = window.findChild(QGroupBox, "workflowNavigation")
    assert navigation is not None
    # The navigation footer is outside the results frame, and below it.
    sidebar = navigation.parentWidget()
    assert sidebar is window._result_actions.parentWidget()
    assert isinstance(window._result_actions, QFrame)
    assert window._result_actions.frameShape() == QFrame.Shape.StyledPanel
    assert not window._result_actions.isAncestorOf(navigation)
    sidebar_layout = sidebar.layout()
    assert sidebar_layout.indexOf(window._result_actions) < sidebar_layout.indexOf(
        navigation
    )
    assert sidebar_layout.itemAt(sidebar_layout.count() - 1).widget() is navigation
    assert not hasattr(window, 'browser_panel')

    buttons = navigation.findChildren(QToolButton)
    assert buttons == [
        window._project_browser_button,
        window._load_input_button,
        window._settings_button,
        window._close_button,
    ]
    assert [button.text() for button in buttons] == [
        "Project Browser", "Load Input File", "Settings", "Close"
    ]
    assert all(not button.icon().isNull() for button in buttons)
    assert all(
        button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
        for button in buttons
    )

    actions = []
    monkeypatch.setattr(
        workflow_module.custom_flow, "choose_custom_input",
        lambda *_args, **_kwargs: actions.append("load"),
    )
    monkeypatch.setattr(
        workflow_module,
        "edit_settings",
        lambda settings, parent: actions.append(("settings", settings, parent)),
    )
    window.projectBrowserRequested.connect(lambda: actions.append("projects"))

    window._project_browser_button.click()
    window._load_input_button.click()
    window._settings_button.click()

    assert actions == [
        "projects",
        "load",
        ("settings", gui.settings, window),
    ]
    # The application composition root must still connect this button to
    # the same non-modal project browser as the main window.
    assert gui._project_browser is not None
    assert gui._project_browser.isVisible()
    window.show()
    _application.processEvents()
    window._close_button.click()
    _application.processEvents()
    assert not window.isVisible()
    assert gui._project_browser is None or not gui._project_browser.isVisible()


def test_main_window_close_also_closes_project_browser(tmp_path):
    application = QApplication.instance() or QApplication([])
    gui, window = _workflow(tmp_path)
    window.show()
    gui.show_project_browser()
    assert gui._project_browser is not None
    assert gui._project_browser.isVisible()

    # Window-manager close uses the same application shutdown route.
    window.close()
    application.processEvents()

    assert not window.isVisible()
    assert gui._project_browser is None or not gui._project_browser.isVisible()


def test_cancelled_exit_keeps_workflow_and_browser_open(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    gui, window = _workflow(tmp_path)
    window.show()
    gui.show_project_browser()
    monkeypatch.setattr(gui, "_confirm_unsaved_work", lambda: False)

    window._close_button.click()
    application.processEvents()

    assert window.isVisible()
    assert gui._project_browser is not None
    assert gui._project_browser.isVisible()

    # A cancelled shutdown does not poison a subsequent accepted exit.
    monkeypatch.setattr(gui, "_confirm_unsaved_work", lambda: True)
    window.close()
    application.processEvents()
    assert not window.isVisible()
    assert gui._project_browser is None or not gui._project_browser.isVisible()




def test_guided_save_structure_writes_normal_atoms_without_running(tmp_path, monkeypatch):
    from ase2sprkkr_gui.gui.flows import files as file_flows
    from ase2sprkkr_gui.gui.workflow.model import WorkflowSection

    _application = QApplication.instance() or QApplication([])
    _gui, window = _workflow(tmp_path)
    structure = Atoms("Fe", cell=[2.87] * 3, pbc=True)
    window.controller.replace_structure(structure)
    expected = tmp_path / "bulk.xyz"
    monkeypatch.setattr(
        file_flows.QFileDialog, "getSaveFileName",
        lambda *_args: (str(expected), "XYZ (*.xyz)"),
    )
    commands = tuple(
        action.command
        for action in window.workflow_plan.group(WorkflowSection.VIEW).actions
    )
    assert commands == (VisualizeStructure(), SaveStructure())
    window.execute_workflow_command(SaveStructure())
    assert expected.is_file()
    assert not window.controller.has_unsaved_structure
    assert window.controller.atoms is structure
    window.close()


def test_save_embedded_cluster_from_shutdown_keeps_host_and_writes_both_files(
    tmp_path, monkeypatch,
):
    from ase2sprkkr_gui.ase.embedding import EmbeddingDraft
    from ase2sprkkr_gui.gui.flows import files as file_flows

    application = QApplication.instance() or QApplication([])
    gui, workflow = _workflow(tmp_path)
    periodic_host = Atoms("Fe", cell=[2.87] * 3, pbc=True)
    draft = EmbeddingDraft(periodic_host)
    draft.include([draft.center])
    cluster = draft.to_atoms()
    gui.controller.replace_structure(cluster)
    assert gui.controller.has_unsaved_structure
    assert gui.controller.atoms.embedding_host is periodic_host

    destination = tmp_path / "embedded.clu"
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Save,
    )
    monkeypatch.setattr(
        file_flows.QFileDialog, "getSaveFileName",
        lambda *_args: (str(destination), "SPR-KKR cluster potential (*.clu *.pot)"),
    )

    assert gui._confirm_unsaved_work()
    assert destination.is_file()
    assert (tmp_path / "embedded.host.pot").is_file()
    assert not gui.controller.has_unsaved_structure
    assert gui.controller.atoms.embedding_host is periodic_host
    workflow.close()


def test_guided_structure_sidebar_tracks_current_structure(tmp_path):
    """Structure facts stay separate from actions and results."""
    from ase.build import bulk
    from PyQt6.QtWidgets import QSplitter

    application = QApplication.instance() or QApplication([])
    _gui, window = _workflow(tmp_path)
    window.show()
    application.processEvents()

    assert window._layout_splitter.count() == 3
    assert window._structure_overview.isHidden()

    atoms = bulk("Fe", "bcc", a=2.87, cubic=True)
    window.controller.replace_structure(atoms)
    application.processEvents()

    inspector = window._structure_overview
    assert not inspector.isHidden()
    assert "Type: Bulk (3D)" in inspector.details.text()
    assert "Elements: Fe" in inspector.details.text()
    assert "Periodicity: x, y, z (3D)" in inspector.details.text()
    assert "Cell (Å)" in inspector.details.text()
    assert inspector.preview_source.count() == 1
    assert not window._subtitle.isVisible()

    window.controller.reset()
    application.processEvents()
    assert inspector.isHidden()
    assert window._subtitle.isVisible()
    window.close()

def test_action_cards_are_equal_height_across_both_guided_columns(tmp_path):
    application = QApplication.instance() or QApplication([])
    _gui, window = _workflow(tmp_path)
    window.show()
    application.processEvents()

    def card_heights():
        cards = (
            window._structure_actions.findChildren(QAbstractButton)
            + window._actions.findChildren(QAbstractButton)
        )
        assert cards
        return {card.height() for card in cards}

    assert len(card_heights()) == 1
    window.controller.replace_structure(
        Atoms("Fe", cell=(2.8, 2.8, 2.8), pbc=True)
    )
    application.processEvents()
    assert len(card_heights()) == 1

    window._layout_splitter.moveSplitter(window._layout_splitter.sizes()[0] - 60, 1)
    application.processEvents()
    assert len(card_heights()) == 1
    window.close()


def test_introductory_cluster_action_appears_without_structure_and_can_be_hidden(
    tmp_path, monkeypatch,
):
    gui, window = _workflow(tmp_path)
    assert ExplainEmbedding() in _commands(window)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args: QMessageBox.StandardButton.No,
    )

    window.execute_workflow_command(ExplainEmbedding())

    assert gui.settings.show_embedded_cluster_hint is False
    assert ExplainEmbedding() not in _commands(window)
    saved = gui.settings.__class__(gui.settings.path)
    saved.load()
    assert saved.show_embedded_cluster_hint is False
    window.close()
