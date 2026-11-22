"""XAS INFO page using the common text and numerical-progress widgets."""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from ase2sprkkr.outputs.readers.xas import XasProgressParser
from ase2sprkkr_gui.gui.dialogs.info_progress import InfoProgressPage


class XasProgressPage(InfoProgressPage):
    """Use the native XAS parser for structured progress and INFO."""

    def __init__(self, parent: QWidget | None = None, *, total_points: int | None = None) -> None:
        super().__init__(
            parent,
            parser_factory=lambda **kwargs: XasProgressParser(**kwargs, total_points=total_points),
            initial_info="Preparing XAS calculation...",
        )
