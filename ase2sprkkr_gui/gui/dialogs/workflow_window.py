from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QKeySequence, QPalette
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QStyle,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ase2sprkkr_gui.gui.application.recent_files import RecentFiles
from ase2sprkkr_gui.gui.application.settings import ApplicationSettings
from ase2sprkkr_gui.gui.application.window_geometry import manage_window_geometry
from ase2sprkkr_gui.gui.application.workspace_controller import WorkspaceController
from ase2sprkkr_gui.gui.dialogs.object_view import execute_value_action
from ase2sprkkr_gui.gui.dialogs.settings import edit_settings
from ase2sprkkr_gui.gui.misc.icons import settings_icon
from ase2sprkkr_gui.gui.flows import calculation as calculation_flow
from ase2sprkkr_gui.gui.flows import custom_calculation as custom_flow
from ase2sprkkr_gui.gui.flows import files as file_flows
from ase2sprkkr_gui.gui.flows import structures as structure_flows
from ase2sprkkr_gui.gui.flows import visualization as visualization_flow
from ase2sprkkr_gui.gui.recent_presentation import RecentLaunchKind
from ase2sprkkr_gui.gui.style import (
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
    SPACE_XXL,
    button_stylesheet,
    group_panel_stylesheet,
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
        self.settings = settings
        self._track_launch_monitor = track_launch_monitor
        self.project_repository = project_repository
        self._workflow_planner = WorkflowPlanner(settings=settings)
        self.controller.structureChanged.connect(self._on_structure_changed)
        # Results have their own presenter; they do not change workflow
        # availability unless the controller also emits structureChanged.
        self.recent_history.changed.connect(self._refresh_recent_history)
        self._build_ui()
        self._refresh()

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
        root = QHBoxLayout(central)
        root.setContentsMargins(SPACE_XXL, SPACE_XL, SPACE_XL, SPACE_XL)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        root.addWidget(splitter)
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
        structure_body_layout.addWidget(self._structure_overview)
        self._structure_actions = WorkflowActionsWidget(
            structure_body, column="structure"
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
        content.setMinimumWidth(300)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, SPACE_XL, 0)
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

        self._actions = WorkflowActionsWidget(content, column="calculate")
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

        # The sidebar is only a layout container: the result view and the
        # bottom navigation must be independent, visually separate panels.
        side = QWidget(splitter)
        side.setMinimumWidth(285)
        side.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(SPACE_SM)

        result_actions_title = QLabel("Results", side)
        result_actions_title.setObjectName("workflowResultsTitle")
        result_actions_title.setFont(
            heading_font(result_actions_title.font(), "dialog")
        )
        side_layout.addWidget(result_actions_title)

        self._result_actions = QFrame(side)
        self._result_actions.setFrameShape(QFrame.Shape.StyledPanel)
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

        # Keep the application-level navigation actions together at the
        # bottom of the sidebar, below the workspace results.
        navigation = QGroupBox("", side)
        navigation.setObjectName("workflowNavigation")
        navigation.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        navigation_layout = QHBoxLayout(navigation)
        navigation_layout.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        navigation_layout.setSpacing(SPACE_XS)

        def add_navigation_button(
            label: str, icon: QIcon, tooltip: str, callback: Callable[[], None]
        ) -> QToolButton:
            button = QToolButton(navigation)
            button.setText(label)
            button.setIcon(icon)
            button.setIconSize(QSize(24, 24))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setMinimumHeight(72)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            button.setStyleSheet(
                button_stylesheet(self.palette(), selector="QToolButton")
            )
            button.setToolTip(tooltip)
            button.setAccessibleName(label)
            button.clicked.connect(callback)
            navigation_layout.addWidget(button, 1)
            return button

        self._project_browser_button = add_navigation_button(
            "Project Browser",
            QIcon.fromTheme(
                "folder-open",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
            ),
            "Browse projects and past calculations",
            lambda _checked=False: self.projectBrowserRequested.emit(),
        )
        self._load_input_button = add_navigation_button(
            "Load Input File",
            QIcon.fromTheme(
                "document-open",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
            ),
            "Load, edit, save or calculate from an SPR-KKR input file",
            self._load_input_file,
        )
        self._settings_button = add_navigation_button(
            "Settings", settings_icon(), "Configure Ase2sprkkr (Ctrl+,)", self._open_settings
        )
        self._settings_button.setShortcut(QKeySequence("Ctrl+,"))
        self._close_button = add_navigation_button(
            "Close",
            QIcon.fromTheme(
                "application-exit",
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton),
            ),
            "Close Ase2sprkkr (Ctrl+Q)",
            self.close,
        )
        self._close_button.setShortcut(QKeySequence("Ctrl+Q"))
        navigation.setStyleSheet(
            group_panel_stylesheet(
                self.palette(), self.palette().color(QPalette.ColorRole.Highlight)
            )
        )
        side_layout.addWidget(navigation, alignment=Qt.AlignmentFlag.AlignBottom)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([880, 380])

    def _sync_action_card_heights(self) -> None:
        height = max(
            self._structure_actions.natural_card_height,
            self._actions.natural_card_height,
        )
        self._structure_actions.set_uniform_card_height(height)
        self._actions.set_uniform_card_height(height)

    def _refresh_recent_history(self) -> None:
        # Recent files affect only the empty start screen, not a loaded
        # structure or a hostless CLUPOT awaiting its host.
        if self.controller.atoms is None and self.controller.unresolved_cluster_path is None:
            self._refresh()

    def _refresh(self) -> None:
        plan = self._workflow_planner.for_workspace(
            self.controller,
            recent=self.recent_history.read_snapshot(),
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
        if atoms is None:
            self._calculate_notice.setText(
                "Load or create a structure to enable calculations."
            )
        else:
            self._calculate_notice.setText(
                "No calculation is available for the current structure."
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
        if isinstance(command, RepeatRecentInput):
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
