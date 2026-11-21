from __future__ import annotations

from threading import Thread
from typing import Any, Callable

from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QKeySequence, QPalette, QResizeEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QMenu,
    QScrollArea,
    QStyle,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr_gui.gui.application.recent_files import (
    RECENT_KINDS,
    RecentFiles,
    RecentFilesSnapshot,
    find_missing_recent_paths,
)
from ase2sprkkr_gui.gui.application.settings import ApplicationSettings
from ase2sprkkr_gui.gui.application.window_geometry import manage_window_geometry
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.dialogs.object_view import execute_value_action
from ase2sprkkr_gui.gui.dialogs.settings import edit_settings
from ase2sprkkr_gui.gui.misc.colors import blend
from ase2sprkkr_gui.gui.misc.icons import settings_icon
from ase2sprkkr_gui.gui.misc.resources import icon_path
from ase2sprkkr_gui.gui.flows import calculation as calculation_flow
from ase2sprkkr_gui.gui.flows import custom_calculation as custom_flow
from ase2sprkkr_gui.gui.flows import files as file_flows
from ase2sprkkr_gui.gui.flows import structures as structure_flows
from ase2sprkkr_gui.gui.flows import visualization as visualization_flow
from ase2sprkkr_gui.gui.recent_presentation import RecentLaunchKind, recent_menu_items
from ase2sprkkr_gui.gui.style import (
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
    SPACE_XXL,
    button_stylesheet,
    heading_font,
    secondary_text_stylesheet,
)
from ase2sprkkr_gui.gui.widgets.result_actions import ResultActionsWidget
from ase2sprkkr_gui.gui.widgets.structures.overview import StructureOverviewWidget
from ase2sprkkr_gui.gui.widgets.workflow_actions import WorkflowActionsWidget
from ase2sprkkr_gui.gui.workflow.model import (
    Build2D,
    Build2DKind,
    BuildEmbedding,
    ExplainEmbedding,
    ConvergeHostScf,
    ContinueRecent,
    LoadAllRecentResults,
    RepeatRecentInput,
    CreateStructure,
    CreateStructureKind,
    Load,
    LoadKind,
    RunTask,
    ReconvergeScf,
    CustomTask,
    StartOver,
    UpdateEmptySpheres,
    UseForNewCalculation,
    VisualizeStructure,
    EditStructure,
    SaveStructure,
    LoadHostPotential,
    AttachHostPotential,
    CreateHostPotential,
    WorkflowCommand,
    WorkflowPlan,
)
from ase2sprkkr_gui.gui.workflow.planner import WorkflowPlanner


class _RecentValidationSignals(QObject):
    """Deliver a one-shot filesystem check to the GUI thread."""

    finished = pyqtSignal(object, object)


def _check_recent_files(snapshot: RecentFilesSnapshot, signals: _RecentValidationSignals) -> None:
    signals.finished.emit(snapshot, find_missing_recent_paths(snapshot))


class _RecentFooterButton(QToolButton):
    """A split-menu navigation button showing the most recent path."""

    def __init__(self, title: str, parent: QWidget):
        super().__init__(parent)
        self._title = title
        self._recent_path: str | None = None
        self._base_tooltip = ""
        self.setText(title)
        # Keep the action name on one line; only the path may be elided.
        self.setFixedWidth(194)

    def set_recent_path(self, path: str | None) -> None:
        self._recent_path = path
        self._refresh_label()

    def set_base_tooltip(self, tooltip: str) -> None:
        self._base_tooltip = tooltip
        self._refresh_label()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_label()

    def _refresh_label(self) -> None:
        if self._recent_path:
            # Reserve space for the icon and the enlarged menu split control.
            available = max(45, self.width() - 82)
            short_path = self.fontMetrics().elidedText(
                self._recent_path, Qt.TextElideMode.ElideMiddle, available
            )
            label = f"{self._title}\n{short_path}"
            tooltip = f"{self._base_tooltip}\n{self._recent_path}"
        else:
            label = self._title
            tooltip = self._base_tooltip
        if self.text() != label:
            self.setText(label)
        if self.toolTip() != tooltip:
            self.setToolTip(tooltip)


class WorkflowWindow(QMainWindow):
    """Primary task-oriented Ase2sprkkr application window."""

    projectBrowserRequested = pyqtSignal()

    def __init__(
        self,
        controller: WorkspaceController,
        recent_files: RecentFiles,
        *,
        settings: ApplicationSettings,
        track_launch_monitor: calculation_flow.LaunchMonitorTracker,
        project_repository=None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Ase2sprkkr - Workflow")
        manage_window_geometry(
            self, "workflow", default_size=(1320, 760), default_maximized=True
        )
        self.controller = controller
        self.recent_history = recent_files
        self._displayed_recent_inputs = recent_files.paths("input")
        self.settings = settings
        self._track_launch_monitor = track_launch_monitor
        self.project_repository = project_repository
        self._workflow_planner = WorkflowPlanner(settings=settings)
        self._recent_validation_running = False
        self._recent_validation_signals: _RecentValidationSignals | None = None
        self._navigation_snapshot: RecentFilesSnapshot | None = None
        self.controller.structureChanged.connect(self._on_structure_changed)
        # Results have their own presenter; they do not change workflow
        # availability unless the controller also emits structureChanged.
        self.recent_history.changed.connect(self._refresh_recent_history)
        self.controller.inputParametersChanged.connect(
            lambda _parameters: self._update_navigation(self.recent_history.read_snapshot())
        )
        self._build_ui()
        self._refresh()
        self._validate_recent_files_async()

    @property
    def result_actions_widget(self) -> ResultActionsWidget:
        """Return the result browser owned by this window."""
        return self._result_actions_widget

    @property
    def workflow_plan(self) -> WorkflowPlan:
        """Return the semantic action plan currently rendered by the window."""
        plan = self._actions.plan
        if plan is None:
            raise RuntimeError("Workflow plan has not been rendered yet.")
        return plan

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(SPACE_XXL, SPACE_XL, SPACE_XL, SPACE_XL)
        root.setSpacing(SPACE_SM)


        # Fixed navigation is separate from the scrollable guided cards.
        # Keep these controls small and neutral, like the Results controls.
        def navigation_group(parent: QWidget, name: str) -> QWidget:
            group = QWidget(parent)
            group.setObjectName(name)
            layout = QHBoxLayout(group)
            inset = SPACE_SM if name == "workflowResultsNavigation" else 0
            layout.setContentsMargins(inset, 0, inset, 0)
            layout.setSpacing(SPACE_XS)
            group.setFixedHeight(46)
            return group

        def navigation_button(
            group: QWidget,
            label: str,
            icon: QIcon,
            tooltip: str,
            callback: Callable[[], None],
            *,
            recent_menu: bool = False,
            height: int = 40,
        ) -> QToolButton:
            button = (
                _RecentFooterButton(label, group)
                if recent_menu else QToolButton(group)
            )
            if not recent_menu:
                button.setText(label)
            button.setIcon(icon)
            button.setIconSize(QSize(18, 18))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setFixedHeight(height)
            button.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
            mid = button.palette().color(QPalette.ColorRole.Mid).name()
            button.setStyleSheet(
                button_stylesheet(button.palette(), selector="QToolButton")
                + "QToolButton { padding: 2px 6px; } "
                + f"QToolButton::menu-button {{ width: 28px; border-left: 1px solid {mid}; }}"
            )
            if recent_menu:
                button.set_base_tooltip(tooltip)
                button.setPopupMode(
                    QToolButton.ToolButtonPopupMode.MenuButtonPopup
                )
                button.setMenu(QMenu(button))
            else:
                button.setToolTip(tooltip)
            button.setAccessibleName(label)
            button.clicked.connect(callback)
            group.layout().addWidget(button)
            return button

        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        root.addWidget(splitter, 1)
        self._layout_splitter = splitter

        # Treat the first two columns as one resize unit. Their equal
        # layout stretches keep them the same width when Results is resized.
        action_columns = QWidget(splitter)
        action_columns.setObjectName("workflowActionColumns")
        action_columns_layout = QHBoxLayout(action_columns)
        action_columns_layout.setContentsMargins(0, 0, 0, 0)
        action_columns_layout.setSpacing(splitter.handleWidth())

        # The first column owns both current-structure information and all
        # structural workflows. Its content scrolls as a single unit.
        structure_column = QWidget(action_columns)
        structure_column.setObjectName("workflowStructureColumn")
        structure_column.setMinimumWidth(300)
        structure_layout = QVBoxLayout(structure_column)
        structure_layout.setContentsMargins(0, 0, SPACE_XL, 0)
        structure_column_navigation = navigation_group(
            structure_column, "workflowStructureNavigation"
        )
        structure_layout.addWidget(structure_column_navigation)

        self._structure_title = QLabel("Structure", structure_column)
        self._structure_title.setObjectName("workflowStructureTitle")
        self._structure_title.setFont(
            heading_font(self._structure_title.font(), "dialog")
        )
        structure_layout.addWidget(self._structure_title)

        self._subtitle = QLabel(structure_column)
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(secondary_text_stylesheet(self.palette()))
        structure_layout.addWidget(self._subtitle)

        structure_scroll = QScrollArea(structure_column)
        structure_scroll.setObjectName("workflowStructureActionsScrollArea")
        structure_scroll.setFrameShape(QFrame.Shape.NoFrame)
        structure_scroll.setWidgetResizable(True)
        structure_scroll.setAlignment(Qt.AlignmentFlag.AlignTop)
        structure_body = QWidget(structure_scroll)
        structure_body_layout = QVBoxLayout(structure_body)
        structure_body_layout.setContentsMargins(0, 0, SPACE_SM, 0)
        structure_body_layout.setSpacing(SPACE_XL)
        structure_body_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._structure_overview = StructureOverviewWidget(structure_body)
        self._structure_overview.visualizeRequested.connect(self._visualize_structure)
        structure_body_layout.addWidget(self._structure_overview)
        self._structure_actions = WorkflowActionsWidget(
            structure_body, column="structure",
            excluded_commands=(ContinueRecent, StartOver, VisualizeStructure),
        )
        self._structure_actions.commandRequested.connect(self.execute_workflow_command)
        structure_body_layout.addWidget(self._structure_actions)
        structure_body_layout.addStretch(1)
        structure_scroll.setWidget(structure_body)
        structure_layout.addWidget(structure_scroll, 1)
        action_columns_layout.addWidget(structure_column, 1)

        # Calculation commands have their own column, independently scrollable
        # from structure actions and from the persistent results sidebar.
        content = QWidget(action_columns)
        content.setObjectName("workflowCalculateColumn")
        content.setMinimumWidth(300)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, SPACE_XL, 0)
        content_navigation = navigation_group(
            content, "workflowCalculationNavigation"
        )
        content_layout.addWidget(content_navigation)

        title = QLabel("Calculate", content)
        title.setObjectName("workflowCalculateTitle")
        title.setFont(heading_font(title.font(), "dialog"))
        content_layout.addWidget(title)

        self._calculate_notice = QLabel(content)
        self._calculate_notice.setWordWrap(True)
        self._calculate_notice.setStyleSheet(
            secondary_text_stylesheet(self.palette())
        )
        content_layout.addWidget(self._calculate_notice)

        self._actions = WorkflowActionsWidget(
            content, column="calculate",
            excluded_commands=(CustomTask, RepeatRecentInput),
        )
        self._actions.commandRequested.connect(self.execute_workflow_command)
        for cards in (self._structure_actions, self._actions):
            cards.naturalCardHeightChanged.connect(self._sync_action_card_heights)
        action_scroll = QScrollArea(content)
        action_scroll.setObjectName("workflowActionScrollArea")
        action_scroll.setFrameShape(QFrame.Shape.NoFrame)
        action_scroll.setWidgetResizable(True)
        action_scroll.setAlignment(Qt.AlignmentFlag.AlignTop)
        action_scroll.setWidget(self._actions)
        content_layout.addWidget(action_scroll, 1)

        action_columns_layout.addWidget(content, 1)
        splitter.addWidget(action_columns)

        # Results share the same borderless column treatment as the actions.
        side = QWidget(splitter)
        side.setObjectName("workflowResultsColumn")
        side.setMinimumWidth(285)
        side.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(SPACE_SM)
        side_navigation = navigation_group(
            side, "workflowResultsNavigation"
        )
        side_layout.addWidget(side_navigation)

        result_actions_title = QLabel("Results", side)
        result_actions_title.setObjectName("workflowResultsTitle")
        result_actions_title.setFont(
            heading_font(result_actions_title.font(), "dialog")
        )
        side_layout.addWidget(result_actions_title)

        self._result_actions = QFrame(side)
        self._result_actions.setFrameShape(QFrame.Shape.NoFrame)
        result_actions_layout = QVBoxLayout(self._result_actions)
        result_actions_layout.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        result_actions_layout.setSpacing(SPACE_XS)
        self._result_actions_widget = ResultActionsWidget(
            lambda value, action: execute_value_action(value, action, self),
            show_values_without_actions=False,
            show_history_button=False,
            empty_text="",  # The Results heading and Load Result button suffice.
            parent=self._result_actions,
        )
        result_actions_layout.addWidget(self._result_actions_widget, 1)
        side_layout.addWidget(self._result_actions, 1)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([880, 380])

        # Stable commands belong to the headers of their workflow columns.
        # Recent Structures retains its position when Start from Scratch appears.
        self._recent_structures_button = navigation_button(
            structure_column_navigation,
            "Recent Structures",
            QIcon(str(icon_path("bulk_crystal.png"))),
            "Open the latest structure or choose another",
            lambda: self._open_recent("structure"),
            recent_menu=True,
        )
        self._start_over_button = navigation_button(
            structure_column_navigation,
            "Start from Scratch",
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            "Clear the current workspace; existing results are kept",
            self._start_over,
        )
        structure_column_navigation.layout().addStretch(1)

        self._repeat_button = navigation_button(
            content_navigation,
            "Repeat Calculation",
            QIcon(str(icon_path("system-run.svg"))),
            "Repeat the most recent input or choose another",
            lambda: self._open_recent("input"),
            recent_menu=True,
        )
        self._load_input_button = navigation_button(
            content_navigation,
            "Load Input File",
            QIcon.fromTheme(
                "document-open",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
            ),
            "Load, edit, save or calculate from an SPR-KKR input file",
            self._load_input_file,
        )
        self._custom_button = navigation_button(
            content_navigation,
            "Custom Input File",
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon),
            "Choose a registered SPR-KKR task and configure its parameters",
            self._custom_task,
        )
        content_navigation.layout().addStretch(1)

        self._recent_results_button = navigation_button(
            side_navigation,
            "Recent Results",
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            "Open the latest result or choose another",
            lambda: self._open_recent("output"),
            recent_menu=True,
        )
        side_navigation.layout().addStretch(1)

        # The original Results-only utility toolbar is restored at the bottom.
        # Its three global commands are always visible, independently of scroll.
        results_footer = QFrame(side)
        results_footer.setObjectName("workflowResultsFooter")
        results_footer.setFrameShape(QFrame.Shape.NoFrame)
        footer_layout = QHBoxLayout(results_footer)
        footer_layout.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, 0)
        footer_layout.setSpacing(SPACE_XS)
        results_footer.setFixedHeight(44)
        self._project_browser_button = navigation_button(
            results_footer,
            "Project Browser",
            QIcon.fromTheme(
                "folder-open",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
            ),
            "Browse projects and past calculations",
            lambda: self.projectBrowserRequested.emit(),
            height=36,
        )
        footer_layout.addStretch(1)
        self._settings_button = navigation_button(
            results_footer,
            "Settings", settings_icon(), "Configure Ase2sprkkr (Ctrl+,)",
            self._open_settings, height=36,
        )
        self._settings_button.setShortcut(QKeySequence("Ctrl+,"))
        self._close_button = navigation_button(
            results_footer,
            "Close",
            QIcon.fromTheme(
                "application-exit",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton),
            ),
            "Close Ase2sprkkr (Ctrl+Q)",
            self.close, height=36,
        )
        self._close_button.setShortcut(QKeySequence("Ctrl+Q"))
        side_layout.addWidget(results_footer)
        self._results_footer = results_footer

        # Avoid clipping the header or the fixed Results toolbar if the
        # splitter is dragged towards the minimum allowed column width.
        structure_column.setMinimumWidth(max(
            structure_column.minimumWidth(),
            structure_column_navigation.sizeHint().width() + SPACE_XL,
        ))
        content.setMinimumWidth(max(
            content.minimumWidth(),
            content_navigation.sizeHint().width() + SPACE_XL,
        ))
        side.setMinimumWidth(max(
            side.minimumWidth(),
            side_navigation.sizeHint().width(),
            results_footer.sizeHint().width(),
        ))

    def _open_recent(self, kind: str, path: str | None = None) -> None:
        """Use the latest path for the main button, or an explicit menu item."""
        paths = self.recent_history.paths(kind)
        chosen = path if path is not None else (paths[0] if paths else None)
        if chosen is None:
            return
        if kind == "input":
            self.execute_workflow_command(RepeatRecentInput(chosen))
        else:
            self._continue_recent(kind, chosen)

    def _update_navigation(self, snapshot: RecentFilesSnapshot) -> None:
        """Refresh button availability and rebuild menus only for changed history."""
        has_structure = self.controller.atoms is not None
        has_workspace = (
            has_structure
            or self.controller.unresolved_cluster_path is not None
            or self.controller.input_parameters is not None
        )
        self._start_over_button.setVisible(has_workspace)
        self._start_over_button.setEnabled(has_workspace)
        label = "Custom Calculation" if has_structure else "Custom Input File"
        self._custom_button.setText(label)
        self._custom_button.setAccessibleName(label)

        if snapshot == self._navigation_snapshot:
            return
        self._navigation_snapshot = snapshot
        for kind, button in (
            ("structure", self._recent_structures_button),
            ("input", self._repeat_button),
            ("output", self._recent_results_button),
        ):
            paths = snapshot.paths(kind)
            button.setEnabled(bool(paths))
            button.set_recent_path(paths[0] if paths else None)
            menu = button.menu()
            menu.clear()
            for item in recent_menu_items(paths):
                action = menu.addAction(item.label)
                action.setToolTip(item.tooltip)
                action.triggered.connect(
                    lambda _checked=False, item_kind=kind, item_path=item.path:
                    self._open_recent(item_kind, item_path)
                )
            if kind == "output" and paths:
                menu.addSeparator()
                menu.addAction("Load all results").triggered.connect(
                    lambda _checked=False: self.execute_workflow_command(
                        LoadAllRecentResults()
                    )
                )

    def _sync_action_card_heights(self) -> None:
        height = max(
            self._structure_actions.natural_card_height,
            self._actions.natural_card_height,
        )
        self._structure_actions.set_uniform_card_height(height)
        self._actions.set_uniform_card_height(height)

    def _refresh_recent_history(self) -> None:
        self._update_navigation(self.recent_history.read_snapshot())
        # Only input-history changes affect the planned action cards in a loaded workspace.
        inputs_changed = self.recent_history.paths("input") != self._displayed_recent_inputs
        if inputs_changed or (
            self.controller.atoms is None
            and self.controller.unresolved_cluster_path is None
        ):
            self._refresh()
        self._validate_recent_files_async()

    def _validate_recent_files_async(self) -> None:
        """Keep the initial menus responsive; inspect paths in one daemon worker."""
        if self._recent_validation_running:
            return
        snapshot = self.recent_history.read_snapshot()
        if not any(snapshot.paths(kind) for kind in RECENT_KINDS):
            return
        signals = _RecentValidationSignals()
        signals.finished.connect(self._recent_files_checked)
        self._recent_validation_signals = signals
        self._recent_validation_running = True
        Thread(
            target=_check_recent_files, args=(snapshot, signals),
            name="recent-files-check", daemon=True,
        ).start()

    def _recent_files_checked(self, snapshot: RecentFilesSnapshot, missing: dict) -> None:
        self._recent_validation_running = False
        self._recent_validation_signals = None
        if self.recent_history.read_snapshot() != snapshot:
            # History changed while the worker was checking a filesystem.
            # Its result is obsolete; validate the latest snapshot instead.
            self._validate_recent_files_async()
            return
        # The history emits one changed signal, replacing the whole menu
        # presentation once, and only when missing files were actually found.
        self.recent_history.remove_known_missing(snapshot, missing)

    def _refresh(self) -> None:
        snapshot = self.recent_history.read_snapshot()
        self._displayed_recent_inputs = snapshot.input
        plan = self._workflow_planner.for_workspace(
            self.controller,
            recent=snapshot,
        )
        atoms = self.controller.atoms
        kind = self.controller.structure_kind()
        self._structure_overview.set_structure(
            atoms, kind=kind, scf_status=self.controller.scf_status(),
        )
        self._structure_overview.setVisible(atoms is not None)
        # Formula, dimensionality and SCF state belong to the structure panel.
        # Keep onboarding and diagnostic messages in the structure column.
        invalid_cluster = (
            kind == "0d" and getattr(atoms, "embedding_host", None) is None
        )
        self._structure_title.setText(
            "Structure" if atoms is not None else "Start with..."
        )
        self._subtitle.setText(plan.subtitle)
        self._subtitle.setVisible(bool(plan.subtitle) and (atoms is None or invalid_cluster))
        self._structure_actions.set_plan(plan)
        self._actions.set_plan(plan)
        self._update_navigation(snapshot)
        if atoms is None:
            self._calculate_notice.setText(
                "Create or load a structure to see available guided calculations."
            )
        else:
            self._calculate_notice.setText(
                "No guided calculation is available for the current structure."
            )
        self._calculate_notice.setVisible(not self._actions.has_actions)

    def execute_workflow_command(self, command: WorkflowCommand) -> None:
        """Mechanically route one already-approved workflow command."""
        if isinstance(command, RunTask):
            self._prepare_task(command.backend_task)
            return
        if isinstance(command, ReconvergeScf):
            self._prepare_task("scf", restart_scf=True)
            return
        if isinstance(command, CustomTask):
            self._custom_task()
            return
        if isinstance(command, ConvergeHostScf):
            calculation_flow.configure_and_run_host_scf(
                self.controller, self.recent_history, self,
                dataset_default_mode=self.settings.dataset_default_mode,
                restart_host_scf=command.restart_scf,
                track_launch_monitor=self._track_launch_monitor,
                project_repository=self.project_repository,
            )
            return
        if isinstance(command, UpdateEmptySpheres):
            self._update_empty_spheres()
            return
        if isinstance(command, CreateStructure):
            {
                CreateStructureKind.THREE_D: self._create_3d,
                CreateStructureKind.PROTOTYPE: self._create_from_database,
                CreateStructureKind.SHELL: self._create_shell,
                CreateStructureKind.SURFACE: self._create_surface,
                CreateStructureKind.TWO_D: self._create_transition,
            }[command.kind]()
            return
        if isinstance(command, ExplainEmbedding):
            self._explain_embedded_cluster()
            return
        if isinstance(command, BuildEmbedding):
            structure_flows.build_embedding_structure(self.controller, self)
            return
        if isinstance(command, Build2D):
            surface_mode = {
                Build2DKind.SURFACE: True,
                Build2DKind.STRUCTURE: False,
            }[command.kind]
            self._build_2d(surface_mode=surface_mode)
            return
        if isinstance(command, Load):
            {
                LoadKind.ONLINE_STRUCTURE: self._download_structure,
                LoadKind.STRUCTURE: self._load_structure,
                LoadKind.OUTPUT: self._load_output,
            }[command.kind]()
            return
        if isinstance(command, ContinueRecent):
            self._continue_recent(command.kind, command.path)
            return
        if isinstance(command, LoadAllRecentResults):
            file_flows.load_all_recent_outputs(
                self.controller, self.recent_history, self,
            )
            return
        if isinstance(command, RepeatRecentInput):
            if command.path is None:
                return
            custom_flow.load_custom_input(
                self.controller, self.recent_history, self, command.path,
                track_launch_monitor=self._track_launch_monitor,
                project_repository=self.project_repository,
            )
            return
        if isinstance(command, VisualizeStructure):
            self._visualize_structure()
            return
        if isinstance(command, EditStructure):
            structure_flows.edit_structure(
                self.controller, self, recent_files=self.recent_history
            )
            return
        if isinstance(command, SaveStructure):
            self._save_structure()
            return
        if isinstance(command, LoadHostPotential):
            file_flows.load_host_potential(
                self.controller, self.recent_history, self
            )
            return
        if isinstance(command, AttachHostPotential):
            file_flows.attach_host_potential(
                self.controller, self.recent_history, self
            )
            return
        if isinstance(command, CreateHostPotential):
            file_flows.create_host_potential(
                self.controller, self.recent_history, self
            )
            return
        if isinstance(command, UseForNewCalculation):
            self._use_for_new_calculation()
            return
        if isinstance(command, StartOver):
            self._start_over()
            return
        raise TypeError(f"Unsupported workflow command {type(command).__name__}.")

    def _explain_embedded_cluster(self) -> None:
        answer = QMessageBox.question(
            self, "Creating an Embedded Cluster",
            "An embedded cluster needs a periodic host. First create or load "
            "a bulk (3D) or layered (2D) structure; then choose "
            "'Build Embedded Cluster' from the structure actions.\n\n"
            "Show this introductory action again?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.No:
            self.settings.show_embedded_cluster_hint = False
            if self.settings.save():
                self._refresh()
            else:
                self.settings.show_embedded_cluster_hint = True
                QMessageBox.warning(
                    self, "Cannot Save Settings",
                    "Could not save the preference; the introductory action "
                    "will remain visible.",
                )

    def _on_structure_changed(self, _atoms: Any) -> None:
        self._refresh()

    def _create_3d(self) -> None:
        structure_flows.create_structure(self.controller, self)

    def _create_shell(self) -> None:
        structure_flows.create_structure_by_shell(
            self.controller, self, settings=self.settings
        )

    def _create_from_database(self) -> None:
        structure_flows.create_structure_from_database(self.controller, self)

    def _download_structure(self) -> None:
        structure_flows.download_structure(self.controller, self)

    def _create_surface(self) -> None:
        structure_flows.create_2d_from_new_structure(
            self.controller, self.recent_history, self, surface_mode=True
        )

    def _create_transition(self) -> None:
        structure_flows.create_2d_from_new_structure(
            self.controller, self.recent_history, self, surface_mode=False
        )

    def _load_structure(self) -> None:
        file_flows.choose_and_load_structure(
            self.controller, self.recent_history, self
        )

    def _load_output(self) -> None:
        file_flows.choose_and_load_output(
            self.controller, self.recent_history, self
        )

    def _continue_recent(self, kind: RecentLaunchKind, file_path: str) -> None:
        file_flows.continue_recent(
            self.controller, self.recent_history, kind, file_path, self,
        )

    def _build_2d(self, _checked: bool = False, *, surface_mode: bool = False) -> None:
        structure_flows.build_2d_structure(
            self.controller,
            self.recent_history,
            self,
            surface_mode=surface_mode,
        )

    def _prepare_task(self, task: str, *, restart_scf: bool = False) -> None:
        from ase2sprkkr_gui.gui.input_parameters.tasks import is_guided_task
        if not is_guided_task(task):
            from ase2sprkkr import InputParameters
            from ase2sprkkr_gui.gui.input_parameters.dataset import apply_default_dataset

            parameters = InputParameters.for_task(task)
            apply_default_dataset(
                parameters, self.settings.dataset_default_mode, self.controller.atoms,
            )
            custom_flow.edit_custom_parameters(
                self.controller, self.recent_history, self, parameters,
                track_launch_monitor=self._track_launch_monitor,
                project_repository=self.project_repository,
            )
            return
        calculation_flow.configure_and_run_task(
            self.controller,
            self.recent_history,
            self,
            task,
            dataset_default_mode=self.settings.dataset_default_mode,
            restart_scf=restart_scf,
            track_launch_monitor=self._track_launch_monitor,
            project_repository=self.project_repository,
        )

    def _update_empty_spheres(self) -> None:
        structure_flows.update_empty_spheres(self.controller, self)

    def _use_for_new_calculation(self) -> None:
        calculation_flow.use_for_new_calculation(self.controller, self)

    def _start_over(self) -> None:
        answer = QMessageBox.question(
            self,
            "Start Over?",
            "Really start over? The current structure and calculation settings "
            "will be cleared. Existing calculation results will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.controller.reset()

    def _visualize_structure(self) -> None:
        visualization_flow.visualize_structure(self.controller, self.settings, self)

    def _save_structure(self) -> None:
        file_flows.save_structure(self.controller, self.recent_history, self)

    def _open_settings(self, *, preferred_tasks: bool = False) -> None:
        accepted = (
            edit_settings(self.settings, self, initial_tab="Preferred tasks")
            if preferred_tasks else edit_settings(self.settings, self)
        )
        if accepted:
            self._refresh()

    def _custom_task(self) -> None:
        custom_flow.choose_custom_task(
            self.controller, self.recent_history, self,
            dataset_default_mode=self.settings.dataset_default_mode,
            track_launch_monitor=self._track_launch_monitor,
            project_repository=self.project_repository,
        )

    def _load_input_file(self) -> None:
        custom_flow.choose_custom_input(
            self.controller, self.recent_history, self,
            track_launch_monitor=self._track_launch_monitor,
            project_repository=self.project_repository,
        )
