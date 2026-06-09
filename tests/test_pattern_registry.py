"""Unit tests for PatternRegistry adapter.

Covers:
- get/list delegates to underlying service for base catalog
- register_pattern appends to overlay and is visible on next read
- invalid patterns are rejected before write
- duplicate pattern_id is rejected
- reset_runtime_overlay clears state
- overlay persists across get_registry() singleton calls
- test isolation via tmp_path
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typing import Iterator

from agent.services.pattern_registry import (
    DEFAULT_OVERLAY_PATH,
    PatternRegistry,
    get_registry,
    reset_registry_singleton,
)


VALID_PATTERN = {
    "pattern_id": "test.runtime_pattern",
    "version": "1.0.0",
    "category": "tooling_recipe",
    "language": "agnostic",
    "title": "Runtime Pattern",
    "description": "A pattern registered at runtime for testing.",
    "parameters": [],
    "required_artifacts": [],
    "steps": [],
    "invariants": ["output is deterministic"],
    "acceptance_gates": ["output is non-empty"],
    "examples": [],
}


@pytest.fixture
def overlay_dir(tmp_path: Path) -> Iterator[Path]:
    """Provide a fresh overlay file path and clean up before/after."""
    overlay = tmp_path / "runtime_overlay.v1.json"
    if overlay.exists():
        overlay.unlink()
    yield overlay
    if overlay.exists():
        overlay.unlink()


@pytest.fixture
def registry(overlay_dir: Path) -> PatternRegistry:  # type: ignore[no-redef]
    return PatternRegistry(overlay_path=str(overlay_dir))


def test_get_returns_base_catalog_patterns(registry: PatternRegistry) -> None:
    # The seeded base catalog contains python.function_stub
    pattern = registry.get("python.function_stub")
    assert pattern is not None
    assert pattern["category"] == "planning_renderer"


def test_get_missing_returns_none(registry: PatternRegistry) -> None:
    assert registry.get("does.not.exist") is None


def test_list_delegates_to_service(registry: PatternRegistry) -> None:
    all_patterns = registry.list()
    assert len(all_patterns) >= 5
    # Category filter
    workflows = registry.list(category="workflow_emit")
    assert all(p["category"] == "workflow_emit" for p in workflows)
    assert any(p["pattern_id"] == "workflow.sequential_emit" for p in workflows)
    # Language filter
    py_patterns = registry.list(language="python")
    assert any(p["pattern_id"] == "python.function_stub" for p in py_patterns)


def test_register_pattern_appends_to_overlay(registry: PatternRegistry) -> None:
    ok, errs = registry.register_pattern(VALID_PATTERN)
    assert ok, errs
    assert VALID_PATTERN["pattern_id"] in [p["pattern_id"] for p in registry.overlay_patterns()]
    # Visible on next read
    fetched = registry.get(VALID_PATTERN["pattern_id"])
    assert fetched is not None
    assert fetched["title"] == "Runtime Pattern"


def test_register_rejects_invalid_pattern(registry: PatternRegistry) -> None:
    bad = {"pattern_id": "broken"}  # missing all other required fields
    ok, errs = registry.register_pattern(bad)
    assert not ok
    assert errs  # non-empty
    assert registry.overlay_patterns() == []


def test_register_rejects_duplicate_pattern_id(registry: PatternRegistry) -> None:
    ok, _ = registry.register_pattern(VALID_PATTERN)
    assert ok
    ok2, errs2 = registry.register_pattern(VALID_PATTERN)
    assert not ok2
    assert "already exists" in errs2[0]


def test_reset_runtime_overlay_clears_state(registry: PatternRegistry) -> None:
    registry.register_pattern(VALID_PATTERN)
    assert registry.overlay_patterns()
    registry.reset_runtime_overlay()
    assert registry.overlay_patterns() == []
    assert registry.get(VALID_PATTERN["pattern_id"]) is None


def test_overlay_persists_across_singleton_calls(tmp_path: Path, monkeypatch) -> None:
    """Two get_registry() calls share state via the same overlay file."""
    monkeypatch.setenv("ANANTA_PATTERN_RUNTIME_OVERLAY", str(tmp_path / "shared_overlay.json"))
    reset_registry_singleton()
    r1 = get_registry()
    r1.register_pattern(VALID_PATTERN)

    # Drop the cached singleton to simulate a fresh process
    reset_registry_singleton()
    r2 = get_registry()
    assert r2.get(VALID_PATTERN["pattern_id"]) is not None


def test_default_overlay_path_is_module_constant() -> None:
    assert DEFAULT_OVERLAY_PATH == "./schemas/patterns/runtime_overlay.v1.json"


def test_atomic_overlay_write_does_not_corrupt_on_replace(
    registry: PatternRegistry, overlay_dir: Path
) -> None:
    """After register, the overlay file is valid JSON and readable."""
    registry.register_pattern(VALID_PATTERN)
    raw = overlay_dir.read_text(encoding="utf-8")
    parsed = json.loads(raw)  # must not raise
    assert isinstance(parsed, list)
    assert parsed[0]["pattern_id"] == VALID_PATTERN["pattern_id"]
