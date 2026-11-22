"""SPLEED INFO page, using the shared text-only parser event view."""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from ase2sprkkr.outputs.readers.spleed import SpleedProgressParser
from ase2sprkkr_gui.gui.dialogs.info_progress import InfoProgressPage


class SpleedProgressPage(InfoProgressPage):
    """Display SPLEED parser events without inventing completion percentages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            parser_factory=SpleedProgressParser,
            initial_info="Preparing SPLEED calculation...",
        )
