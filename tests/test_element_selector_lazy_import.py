"""The optional mendeleev package is imported only when element details are needed."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from ase2sprkkr_gui.gui.dialogs.structures import element_selector


def test_element_info_imports_optional_package_on_demand(monkeypatch):
    fake = ModuleType("mendeleev")
    calls = []

    def element(z):
        calls.append(z)
        return SimpleNamespace(name="Iron", symbol="Fe", atomic_weight=55.845)

    fake.element = element
    monkeypatch.setitem(sys.modules, "mendeleev", fake)
    monkeypatch.setattr(element_selector, "_MENDELEEV_AVAILABLE", True)

    assert calls == []
    info = element_selector._load_element_info(26)
    assert calls == [26]
    assert info["name"] == "Iron"
    assert info["symbol"] == "Fe"


def test_missing_optional_element_details_are_nonfatal(monkeypatch):
    monkeypatch.setattr(element_selector, "_MENDELEEV_AVAILABLE", False)
    assert element_selector._load_element_info(26) is None

    # An unavailable optional package should never queue background work.
    loader = element_selector._ElementInfoLoader()
    loader.request(26)
    assert loader._pending == set()
