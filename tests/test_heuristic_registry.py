from __future__ import annotations

import os
import pytest

from agent.services.heuristic_runtime.heuristic_registry_service import (
    HeuristicDefinition,
    HeuristicNotFound,
    HeuristicRegistry,
)

HEURISTICS_PATH = os.path.join(os.path.dirname(__file__), "..", "heuristics")


def _registry() -> HeuristicRegistry:
    r = HeuristicRegistry(base_path=HEURISTICS_PATH)
    r.load_all()
    return r


# --- Load / List ---

def test_load_all_finds_starter_heuristics():
    r = _registry()
    all_h = r.list_all()
    assert len(all_h) >= 4


def test_active_snake_tui_heuristics():
    r = _registry()
    active = r.get_active("snake_tui")
    assert len(active) >= 2
    ids = [h.heuristic_id for h in active]
    assert "snake-follow-default" in ids
    assert "snake-lurk-default" in ids


def test_active_eclipse_heuristics():
    r = _registry()
    active = r.get_active("snake_eclipse")
    assert any(h.heuristic_id == "snake-eclipse-zone-default" for h in active)


def test_active_chat_heuristics():
    r = _registry()
    active = r.get_active("chat_codecompass")
    assert any(h.heuristic_id == "chat-codecompass-select-default" for h in active)


def test_get_by_id_active():
    r = _registry()
    h = r.get_by_id("snake-follow-default")
    assert h.heuristic_id == "snake-follow-default"
    assert h.status == "active"
    assert h.deterministic is True


def test_get_by_id_not_found_raises():
    r = _registry()
    with pytest.raises(HeuristicNotFound):
        r.get_by_id("does-not-exist")


def test_get_by_id_with_version():
    r = _registry()
    h = r.get_by_id("snake-follow-default", version="1.0.0")
    assert h.version == "1.0.0"


def test_list_by_status_active():
    r = _registry()
    active = r.list_by_status("active")
    assert all(h.status == "active" for h in active)
    assert len(active) >= 4


# --- Capability Violations ---

def test_snake_heuristic_no_capability_violations():
    r = _registry()
    h = r.get_by_id("snake-follow-default")
    assert h.has_capability_violation() == []


def test_snake_heuristic_file_write_violation():
    h = HeuristicDefinition.from_dict({
        "heuristic_id": "bad-snake",
        "version": "1.0.0",
        "domain": "snake_tui",
        "strategy_kind": "test",
        "deterministic": True,
        "safety_class": "bounded",
        "capabilities": ["read_local_context", "file_write"],
        "inputs": [],
        "outputs": [],
    })
    violations = h.has_capability_violation()
    assert any("file_write" in v for v in violations)


def test_snake_heuristic_non_deterministic_violation():
    h = HeuristicDefinition.from_dict({
        "heuristic_id": "non-det-snake",
        "version": "1.0.0",
        "domain": "snake_tui",
        "strategy_kind": "test",
        "deterministic": False,
        "safety_class": "bounded",
        "capabilities": ["read_local_context"],
        "inputs": [],
        "outputs": [],
    })
    violations = h.has_capability_violation()
    assert any("deterministic_required" in v for v in violations)


def test_chat_heuristic_no_violations():
    r = _registry()
    h = r.get_by_id("chat-codecompass-select-default")
    assert h.has_capability_violation() == []


# --- In-Memory Registration ---

def test_register_in_memory():
    r = _registry()
    h = HeuristicDefinition.from_dict({
        "heuristic_id": "test-heuristic",
        "version": "0.1.0",
        "domain": "snake_tui",
        "strategy_kind": "test",
        "deterministic": True,
        "safety_class": "readonly",
        "capabilities": ["read_local_context"],
        "inputs": [],
        "outputs": [],
    }, status="active")
    r.register_in_memory(h)
    found = r.get_by_id("test-heuristic")
    assert found.version == "0.1.0"


def test_candidate_not_returned_by_get_active():
    r = _registry()
    h = HeuristicDefinition.from_dict({
        "heuristic_id": "candidate-heuristic",
        "version": "0.1.0",
        "domain": "snake_tui",
        "strategy_kind": "test",
        "deterministic": True,
        "safety_class": "bounded",
        "capabilities": [],
        "inputs": [],
        "outputs": [],
    }, status="candidate")
    r.register_in_memory(h)
    active = r.get_active("snake_tui")
    assert not any(h2.heuristic_id == "candidate-heuristic" for h2 in active)
