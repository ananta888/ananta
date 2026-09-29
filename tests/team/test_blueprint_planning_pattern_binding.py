"""Tests for plan-pattern binding integration in BlueprintPlanningAdapter.

PAT-005 defines the binding schema, PAT-006 wires the adapter to read it.
These tests verify the additive integration without disturbing existing
behaviour: every existing test_blueprint_planning_adapter.py case must
still pass after this change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

from agent.services.blueprint_planning_adapter import BlueprintPlanningAdapter


# --- fixtures ---------------------------------------------------------


@pytest.fixture
def bindings_path(tmp_path: Path) -> Iterator[Path]:
    """Provide an isolated bindings file for the test."""
    p = tmp_path / "bindings.json"
    p.write_text(
        json.dumps(
            {
                "version": "plan_pattern_bindings.v1",
                "bindings": [
                    {
                        "binding_id": "bind_a",
                        "plan_id": "plan_alpha",
                        "pattern_id": "python.function_stub",
                        "step_id": "step_one",
                        "parameters": {"function_name": "alpha", "return_type": "int"},
                        "control": {"enabled": True, "fail_open": False, "dry_run": False},
                        "result_artifacts": ["python_source_file"],
                    },
                    {
                        "binding_id": "bind_b",
                        "plan_id": "plan_beta",
                        "pattern_id": "java.default_deny_gate",
                        "parameters": {"class_name": "BetaGate", "allowed_capabilities": ["tool.read"]},
                        "control": {"enabled": True, "fail_open": False, "dry_run": False},
                        "result_artifacts": ["java_source_file"],
                    },
                    {
                        "binding_id": "bind_disabled",
                        "plan_id": "plan_alpha",
                        "pattern_id": "python.function_stub",
                        "step_id": "step_disabled",
                        "parameters": {"function_name": "disabled"},
                        "control": {"enabled": False, "fail_open": False, "dry_run": False},
                        "result_artifacts": ["python_source_file"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    yield p
    if p.exists():
        p.unlink()


@pytest.fixture
def adapter(bindings_path: Path) -> BlueprintPlanningAdapter:
    a = BlueprintPlanningAdapter.__new__(BlueprintPlanningAdapter)
    # Skip the original __init__'s repo work; we only test the binding path.
    object.__setattr__(a, "_BINDINGS_PATH", str(bindings_path))  # bypass Literal narrowing
    return a


# --- list_plan_pattern_bindings ---------------------------------------


def test_list_returns_all_bindings(adapter: BlueprintPlanningAdapter) -> None:
    bindings = adapter.list_plan_pattern_bindings()
    assert len(bindings) == 3
    assert {b["binding_id"] for b in bindings} == {"bind_a", "bind_b", "bind_disabled"}


def test_list_handles_missing_file(tmp_path: Path) -> None:
    a = BlueprintPlanningAdapter.__new__(BlueprintPlanningAdapter)
    object.__setattr__(a, "_BINDINGS_PATH", str(tmp_path / "does_not_exist.json"))
    assert a.list_plan_pattern_bindings() == []


def test_list_filters_non_dict_entries(tmp_path: Path) -> None:
    p = tmp_path / "mixed.json"
    p.write_text(json.dumps({"bindings": [{"binding_id": "ok"}, "string", None, 42]}), encoding="utf-8")
    a = BlueprintPlanningAdapter.__new__(BlueprintPlanningAdapter)
    object.__setattr__(a, "_BINDINGS_PATH", str(p))
    result = a.list_plan_pattern_bindings()
    assert len(result) == 1
    assert result[0]["binding_id"] == "ok"


# --- resolve_pattern_binding -------------------------------------------


def test_resolve_returns_binding_for_plan_and_step(adapter: BlueprintPlanningAdapter) -> None:
    b = adapter.resolve_pattern_binding("plan_alpha", "step_one")
    assert b is not None
    assert b["binding_id"] == "bind_a"
    assert b["pattern_id"] == "python.function_stub"


def test_resolve_returns_plan_level_binding(adapter: BlueprintPlanningAdapter) -> None:
    b = adapter.resolve_pattern_binding("plan_beta")
    assert b is not None
    assert b["binding_id"] == "bind_b"


def test_resolve_skips_disabled_bindings(adapter: BlueprintPlanningAdapter) -> None:
    # The disabled binding is on a different step, so resolving with
    # the disabled step returns None (skip + no fallback).
    assert adapter.resolve_pattern_binding("plan_alpha", "step_disabled") is None


def test_resolve_returns_none_for_unknown_plan(adapter: BlueprintPlanningAdapter) -> None:
    assert adapter.resolve_pattern_binding("plan_does_not_exist") is None
    assert adapter.resolve_pattern_binding("plan_does_not_exist", "any") is None


def test_resolve_filters_by_step(adapter: BlueprintPlanningAdapter) -> None:
    # plan_alpha has two bindings: one on step_one, one on step_disabled.
    # A query for a different step must return None.
    assert adapter.resolve_pattern_binding("plan_alpha", "step_other") is None


def test_resolve_handles_empty_plan_id(adapter: BlueprintPlanningAdapter) -> None:
    assert adapter.resolve_pattern_binding("") is None
    assert adapter.resolve_pattern_binding("   ") is None


# --- additive backward compat ------------------------------------------


def test_existing_resolve_signature_unchanged(adapter: BlueprintPlanningAdapter) -> None:
    """The existing public ``resolve(query)`` method is untouched by PAT-006.

    We only assert the attribute is present and callable — exercising
    the full flow is the job of test_blueprint_planning_adapter.py.
    """
    assert callable(getattr(adapter, "resolve", None))
    # The new methods must also be present.
    assert callable(getattr(adapter, "list_plan_pattern_bindings", None))
    assert callable(getattr(adapter, "resolve_pattern_binding", None))
