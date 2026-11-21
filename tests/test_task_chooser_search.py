"""Search behavior and match highlighting in the expert task chooser."""
from __future__ import annotations

from PyQt6.QtWidgets import QStyledItemDelegate

from ase2sprkkr_gui.gui.dialogs.task_chooser import (
    MatchHighlightDelegate, TaskChoice, TaskChooserDialog,
    _matching_spans, task_match_priority,
)


def _names(chooser):
    column = chooser._fields.index("name")
    return [
        chooser.table.item(row, column).text()
        for row in range(chooser.table.rowCount())
    ]


def test_search_prioritizes_name_over_info_even_when_preference_sort_disagrees():
    chooser = TaskChooserDialog()
    chooser._records = (
        TaskChoice("ABSFZ", False, "Yes", "Other", preferred=True, supported=True),
        TaskChoice("BSFEXTRA", False, "Yes", "Other"),
        TaskChoice("BSF", True, "No", "Spectrum"),
        TaskChoice("BAND", False, "Yes", "Not BSF or EKREL", preferred=True),
        TaskChoice("EKREL", False, "Yes", "Use BSF for disorder", supported=True),
        TaskChoice("DETAIL_ONLY", False, "Yes", "Other", details="Long BSF description"),
    )
    chooser.search.setText("bSf")
    assert _names(chooser) == ["BSF", "BSFEXTRA", "ABSFZ", "EKREL", "BAND"]
    assert "DETAIL_ONLY" not in _names(chooser)
    chooser.close()


def test_search_matches_only_short_info_or_name_not_long_details():
    row = TaskChoice("BAND", False, "Yes", "Eigenvalues", details="Not BSF")
    assert task_match_priority(row, "bsf") is None
    assert task_match_priority(row, "EIGEN") == 3
    assert task_match_priority(row, "BAND") == 0
    assert task_match_priority(row, "BAN") == 1
    assert task_match_priority(row, "AND") == 2

    chooser = TaskChooserDialog()
    chooser._records = (row,)
    chooser.search.setText("bsf")
    assert _names(chooser) == []
    chooser.search.setText("eigen")
    assert _names(chooser) == ["BAND"]
    chooser.close()


def test_name_and_info_both_have_highlighting_delegates():
    chooser = TaskChooserDialog()
    for field in ("name", "info"):
        delegate = chooser.table.itemDelegateForColumn(chooser._fields.index(field))
        assert isinstance(delegate, MatchHighlightDelegate)
        assert isinstance(delegate, QStyledItemDelegate)
    assert chooser.table.itemDelegateForColumn(
        chooser._fields.index("supported")
    ) is not chooser._match_delegate
    chooser.close()


def test_case_insensitive_highlight_spans_and_unicode_offsets():
    assert _matching_spans("BSF and bSf", "bsf") == [(0, 3), (8, 3)]
    assert _matching_spans("Use BSF for disorder", "bsf") == [(4, 3)]
    assert _matching_spans("BSF and bSf", "not found") == []
    assert _matching_spans("X😀BSF", "bsf") == [(3, 3)]
    assert _matching_spans("Straße", "SS") == [(4, 1)]


def test_filter_uses_existing_sort_within_each_match_tier():
    chooser = TaskChooserDialog()
    chooser._records = (
        TaskChoice("BSF-2", False, "Yes", "other", preferred=False),
        TaskChoice("BSF-1", False, "Yes", "other", preferred=True),
        TaskChoice("XBSF", False, "Yes", "other"),
    )
    chooser.search.setText("bsf")
    assert _names(chooser) == ["BSF-1", "BSF-2", "XBSF"]
    chooser.close()
