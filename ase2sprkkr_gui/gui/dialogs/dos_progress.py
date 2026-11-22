"""DOS INFO page using the common text and numerical-progress widgets."""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from ase2sprkkr.outputs.readers.dos import DosProgressParser
from ase2sprkkr_gui.gui.dialogs.info_progress import InfoProgressPage


class DosProgressPage(InfoProgressPage):
    """Use the native DOS parser for structured progress and INFO."""

    def __init__(self, parent: QWidget | None = None, *, total_points: int | None = None) -> None:
        super().__init__(
            parent,
            parser_factory=lambda **kwargs: DosProgressParser(**kwargs, total_points=total_points),
            initial_info="Preparing DOS calculation...",
        )
