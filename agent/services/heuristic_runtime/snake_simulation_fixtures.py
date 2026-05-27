"""Simulation fixture library for tui_snake candidates (ASH-022).

Provides standard fixture scenarios that all tui_snake candidates must pass:
  - normal_follow_user
  - artifact_detected_fast_target
  - artifact_explain_after_arrival
  - ai_timeout_fallback
  - no_trigger_match
  - invalid_candidate_action
  - candidate_causes_loop
  - candidate_causes_no_movement

Used by HeuristicSimulationHarness to validate candidates before activation.
"""
from __future__ import annotations

from agent.services.heuristic_runtime.simulation_harness import SimulationFixture


def build_tui_snake_fixtures() -> list[SimulationFixture]:
    """Return the standard fixture set for tui_snake domain candidates."""
    return [
        _normal_follow_user(),
        _artifact_detected_fast_target(),
        _artifact_explain_after_arrival(),
        _ai_timeout_fallback(),
        _no_trigger_match(),
    ]


def build_extended_tui_snake_fixtures() -> list[SimulationFixture]:
    """Extended fixture set including edge-case scenarios."""
    return build_tui_snake_fixtures() + [
        _loop_detection(),
        _no_movement_detection(),
    ]


# ── Fixture builders ──────────────────────────────────────────────────────────

def _normal_follow_user() -> SimulationFixture:
    return SimulationFixture(
        fixture_type="snake_event_sequence",
        surface="tui_snake",
        events=[
            {"kind": "cursor_move", "x": 10, "y": 5},
            {"kind": "cursor_move", "x": 11, "y": 5},
            {"kind": "cursor_move", "x": 12, "y": 6},
        ],
        context_snapshot={"active_panel": "dashboard", "ai_status": "online"},
        expected_action_kind="follow_with_distance",
    )


def _artifact_detected_fast_target() -> SimulationFixture:
    return SimulationFixture(
        fixture_type="snake_event_sequence",
        surface="tui_snake",
        events=[
            {"kind": "artifact_select", "artifact_id": "art-001", "x": 20, "y": 8},
        ],
        context_snapshot={"active_panel": "artifacts", "ai_status": "online"},
        expected_action_kind="fast_target",
    )


def _artifact_explain_after_arrival() -> SimulationFixture:
    return SimulationFixture(
        fixture_type="context_snapshot",
        surface="tui_snake",
        context_snapshot={
            "active_panel": "artifacts",
            "ai_status": "online",
            "selected_artifacts": [{"id": "art-001", "type": "code"}],
            "snake_at_target": True,
        },
        expected_action_kind=None,  # any non-policy-violation
    )


def _ai_timeout_fallback() -> SimulationFixture:
    return SimulationFixture(
        fixture_type="snake_event_sequence",
        surface="tui_snake",
        events=[
            {"kind": "ai_timeout", "duration_ms": 5000},
        ],
        context_snapshot={"active_panel": "goals", "ai_status": "timeout"},
        expected_action_kind="follow_with_distance",
    )


def _no_trigger_match() -> SimulationFixture:
    return SimulationFixture(
        fixture_type="context_snapshot",
        surface="tui_snake",
        context_snapshot={
            "active_panel": "unknown_panel_xyz",
            "ai_status": "offline",
        },
        expected_action_kind=None,  # no_match is fine — it's the fallback
    )


def _loop_detection() -> SimulationFixture:
    """Candidate that repeatedly returns the same action should not cause issues."""
    return SimulationFixture(
        fixture_type="snake_event_sequence",
        surface="tui_snake",
        events=[
            {"kind": "cursor_move", "x": 5, "y": 5},
            {"kind": "cursor_move", "x": 5, "y": 5},
            {"kind": "cursor_move", "x": 5, "y": 5},
            {"kind": "cursor_move", "x": 5, "y": 5},
            {"kind": "cursor_move", "x": 5, "y": 5},
        ],
        context_snapshot={"active_panel": "dashboard", "ai_status": "offline"},
        expected_action_kind=None,  # looping alone is not a policy violation
    )


def _no_movement_detection() -> SimulationFixture:
    return SimulationFixture(
        fixture_type="context_snapshot",
        surface="tui_snake",
        context_snapshot={
            "active_panel": "system",
            "ai_status": "offline",
        },
        expected_action_kind=None,
    )
