"""Shared pytest fixtures for GUI tests."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def _qt_application() -> QApplication:
    """Keep the process-wide Qt application alive for the whole test session."""
    application = QApplication.instance() or QApplication([])
    return application


@pytest.fixture(autouse=True)
def _cleanup_qt_widgets(_qt_application: QApplication):
    """Destroy top-level widgets deterministically between GUI tests.

    PyQt wrappers can participate in Python reference cycles even though Qt owns
    their underlying QObject tree. Leaving top-level widgets for cyclic GC can
    therefore run Qt destruction at an arbitrary point in a later test. Queue
    deletion while the session QApplication is certainly alive and flush only
    deferred-delete events; dispatching arbitrary queued GUI callbacks here can
    invoke slots on a partially destroyed widget tree.
    """
    yield

    # Cleanup is destruction, not a user-initiated close: closeEvent may
    # open a modal confirmation even on an already rejected, hidden dialog.
    for widget in QApplication.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def native_embedded_potentials(tmp_path):
    """Independent genuine host/CLUPOT files, freshly copied for each test."""
    from pathlib import Path
    from shutil import copyfile

    import ase2sprkkr

    examples = Path(ase2sprkkr.__file__).parent / "potentials" / "examples"
    host, cluster = tmp_path / "host.pot", tmp_path / "cluster.pot"
    copyfile(examples / "Fe3CoFe3.host.pot", host)
    copyfile(examples / "Fe3CoFe3.clu.pot", cluster)
    return host, cluster



@pytest.fixture
def make_job_source(tmp_path):
    """Supply real session and project dependencies for job source tests."""
    from kkrunner import MemoryRunStore, RunManager
    from kkrunner.projects import ProjectRepository
    from ase2sprkkr_gui.gui.application.persistent_jobs import PersistentJobSource

    def make(manager, *, projects=None, session_manager=None):
        return PersistentJobSource(
            manager,
            projects=projects if projects is not None else ProjectRepository(tmp_path / "source-projects"),
            session_manager=session_manager if session_manager is not None else RunManager(MemoryRunStore()),
        )
    return make
