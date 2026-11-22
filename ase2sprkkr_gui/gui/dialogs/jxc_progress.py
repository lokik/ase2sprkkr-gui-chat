"""JXC INFO page, using the shared text-only parser event view."""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from ase2sprkkr.outputs.readers.jxc import JxcProgressParser
from ase2sprkkr_gui.gui.dialogs.info_progress import InfoProgressPage


class JxcProgressPage(InfoProgressPage):
    """Display JXC parser events without inventing completion percentages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            parser_factory=JxcProgressParser,
            initial_info="Preparing calculation...",
        )
