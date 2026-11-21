"""The module defining the space-group UI must not eagerly import pyxtal."""
from __future__ import annotations

import os
import subprocess
import sys


def test_spacegroup_selector_import_does_not_load_pyxtal():
    program = """
import sys
from ase2sprkkr_gui.gui.dialogs.structures import spacegroup_selector
assert spacegroup_selector.SpaceGroupSelectorDialog is not None
assert "pyxtal" not in sys.modules
assert "pyxtal.symmetry" not in sys.modules
"""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", MPLBACKEND="Agg",
               ASE2SPRKKR_NO_USER_PROFILE="1")
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
