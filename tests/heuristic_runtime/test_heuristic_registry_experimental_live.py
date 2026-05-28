"""Tests für HeuristicRegistry experimental_live Status (T07.02)."""
import pytest
from agent.services.heuristic_runtime.heuristic_registry_service import (
    HeuristicDefinition,
    HeuristicRegistry,
    _VALID_STATUSES,
)


def _make_hdef(hid: str, status: str, domain: str = "tui_snake") -> HeuristicDefinition:
    h = HeuristicDefinition(
        heuristic_id=hid,
        version="1.0",
        domain=domain,
        strategy_kind="declarative_rules",
        description="Test",
        deterministic=True,
        safety_class="ui_motion_only",
        capabilities=(),
        inputs=(),
        outputs=(),
        parameters={},
        status=status,
    )
    object.__setattr__(h, "_raw_def", {"heuristic_id": hid})
    return h


def _registry_with(*hdefs: HeuristicDefinition) -> HeuristicRegistry:
    reg = HeuristicRegistry.__new__(HeuristicRegistry)
    reg._heuristics = {}
    reg._definitions = {}
    reg._all = []
    reg._loaded = True
    reg._base_path = "/nonexistent"
    for h in hdefs:
        reg._all.append(h)
        if h.status == "active":
            reg._definitions[h.heuristic_id] = h
    return reg


def test_experimental_live_in_valid_statuses():
    assert "experimental_live" in _VALID_STATUSES


def test_list_experimental_live_returns_only_experimental_live():
    h1 = _make_hdef("h1", "experimental_live", "tui_snake")
    h2 = _make_hdef("h2", "candidate", "tui_snake")
    h3 = _make_hdef("h3", "active", "tui_snake")
    h4 = _make_hdef("h4", "experimental_live", "tui_snake")

    reg = _registry_with(h1, h2, h3, h4)
    result = reg.list_experimental_live(domain="tui_snake")
    ids = {h.heuristic_id for h in result}

    assert ids == {"h1", "h4"}


def test_list_experimental_live_different_domain_excluded():
    h1 = _make_hdef("h1", "experimental_live", "tui_snake")
    h2 = _make_hdef("h2", "experimental_live", "chat_codecompass")

    reg = _registry_with(h1, h2)
    result = reg.list_experimental_live(domain="tui_snake")
    ids = {h.heuristic_id for h in result}

    assert "h1" in ids
    assert "h2" not in ids


def test_list_by_status_filters_correctly():
    h1 = _make_hdef("h1", "experimental_live")
    h2 = _make_hdef("h2", "candidate")
    h3 = _make_hdef("h3", "active")
    h4 = _make_hdef("h4", "rejected")

    reg = _registry_with(h1, h2, h3, h4)

    assert len(reg.list_by_status("experimental_live")) == 1
    assert reg.list_by_status("experimental_live")[0].heuristic_id == "h1"
    assert len(reg.list_by_status("candidate")) == 1
    assert len(reg.list_by_status("active")) == 1
    assert len(reg.list_by_status("rejected")) == 1
    assert len(reg.list_by_status("archived")) == 0


def test_list_by_status_with_domain_filter():
    h1 = _make_hdef("h1", "candidate", "tui_snake")
    h2 = _make_hdef("h2", "candidate", "chat_codecompass")
    h3 = _make_hdef("h3", "candidate", "tui_snake")

    reg = _registry_with(h1, h2, h3)

    snake_candidates = reg.list_by_status("candidate", domain="tui_snake")
    assert len(snake_candidates) == 2
    ids = {h.heuristic_id for h in snake_candidates}
    assert ids == {"h1", "h3"}


def test_from_dict_attaches_raw_def():
    data = {
        "heuristic_id": "test_h",
        "version": "1.0",
        "domain": "tui_snake",
        "deterministic": True,
        "safety_class": "ui_motion_only",
    }
    h = HeuristicDefinition.from_dict(data, status="experimental_live")
    assert h.heuristic_id == "test_h"
    assert h.status == "experimental_live"
    assert hasattr(h, "_raw_def")
    assert h._raw_def["heuristic_id"] == "test_h"


def test_list_experimental_live_empty_when_none():
    reg = _registry_with()
    assert reg.list_experimental_live() == []
