"""Avoid loading ASE viewers and their optional IO/SciPy chain at GUI startup."""

import os
import subprocess
import sys
import textwrap


def test_workflow_import_does_not_construct_settings_or_viewer_registry():
    code = textwrap.dedent("""
        import sys
        import traceback

        # Preserve the startup constraint and identify the first importer
        # on ASE/SciPy versions with a different transitive import graph.
        def trace_integrate(event, args):
            if event == "import" and args[0] == "scipy.integrate":
                traceback.print_stack(file=sys.stderr)

        sys.addaudithook(trace_integrate)
        from ase2sprkkr_gui.gui.application.viewers import viewer_unavailability

        assert "ase.visualize.viewers" not in sys.modules
        assert viewer_unavailability("ase") is None
        assert "ase.visualize.viewers" not in sys.modules

        from ase2sprkkr_gui.gui.dialogs.workflow_window import WorkflowWindow

        assert WorkflowWindow is not None
        assert "ase2sprkkr_gui.gui.dialogs.settings" not in sys.modules
        assert "ase.visualize.viewers" not in sys.modules
        assert "scipy.integrate" not in sys.modules
    """)
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
