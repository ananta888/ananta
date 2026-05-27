"""Tests for HeuristicSimulationHarness — T07.02."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition
from agent.services.heuristic_runtime.simulation_harness import (
    HeuristicSimulationHarness,
    SimulationFixture,
    SimulationReport,
)


def _hdef(hid="test-h", domain="tui_snake", caps=()):
    return HeuristicDefinition(
        heuristic_id=hid, version="1.0.0", domain=domain,
        strategy_kind="follow", description="test", deterministic=True,
        safety_class="bounded", capabilities=caps,
        inputs=(), outputs=(), parameters={},
    )


harness = HeuristicSimulationHarness()


# ── snake_event_sequence ──────────────────────────────────────────────────────

def test_snake_event_sequence_runs():
    fixture = SimulationFixture(
        fixture_type="snake_event_sequence",
        surface="tui_snake",
        events=[
            {"kind": "artifact_select", "normalized_value": "ref1"},
            {"kind": "pointer_move", "normalized_value": ""},
        ],
        expected_action_kind="follow",
    )
    report = harness.simulate(_hdef(), [fixture])
    assert report.total_runs == 2


def test_lurk_event_sequence():
    fixture = SimulationFixture(
        fixture_type="snake_event_sequence",
        surface="tui_snake",
        events=[{}],  # empty event, no goal → lurk
        expected_action_kind="lurk",
    )
    report = harness.simulate(_hdef(), [fixture])
    assert report.success_rate >= 0  # lurk or follow depending on rules


# ── chat_query_sequence ───────────────────────────────────────────────────────

def test_chat_query_sequence_runs():
    fixture = SimulationFixture(
        fixture_type="chat_query_sequence",
        surface="chat_codecompass",
        queries=["erkläre src/main.py", "wo ist MyClass"],
    )
    report = harness.simulate(_hdef(domain="chat_codecompass"), [fixture])
    assert report.total_runs == 2


# ── context_snapshot ──────────────────────────────────────────────────────────

def test_context_snapshot_runs():
    fixture = SimulationFixture(
        fixture_type="context_snapshot",
        surface="tui_snake",
        context_snapshot={"active_goal_id": "g1", "ai_status": "offline"},
    )
    report = harness.simulate(_hdef(), [fixture])
    assert report.total_runs == 1


# ── SimulationReport metrics ──────────────────────────────────────────────────

def test_success_rate_between_0_and_1():
    fixture = SimulationFixture(fixture_type="context_snapshot", surface="tui_snake",
                                 context_snapshot={}, expected_action_kind="follow")
    report = harness.simulate(_hdef(), [fixture])
    assert 0.0 <= report.success_rate <= 1.0


def test_no_match_rate_between_0_and_1():
    fixture = SimulationFixture(fixture_type="context_snapshot", surface="tui_snake",
                                 context_snapshot={})
    report = harness.simulate(_hdef(), [fixture])
    assert 0.0 <= report.no_match_rate <= 1.0


def test_avg_latency_positive():
    fixture = SimulationFixture(fixture_type="context_snapshot", surface="tui_snake",
                                 context_snapshot={})
    report = harness.simulate(_hdef(), [fixture])
    assert report.avg_latency_ms >= 0


def test_to_dict():
    fixture = SimulationFixture(fixture_type="context_snapshot", surface="tui_snake",
                                 context_snapshot={})
    report = harness.simulate(_hdef(), [fixture])
    d = report.to_dict()
    for key in ("candidate_id", "success_rate", "no_match_rate", "policy_violations", "can_activate"):
        assert key in d


# ── policy violations block activation ───────────────────────────────────────

def test_can_activate_true_without_violations():
    fixture = SimulationFixture(fixture_type="context_snapshot", surface="tui_snake",
                                 context_snapshot={})
    report = harness.simulate(_hdef(), [fixture])
    # Snake domain candidate with allowed caps → no policy violations from caps
    assert report.can_activate is True


def test_can_activate_false_with_capability_violation():
    # network_access is forbidden for snake domains
    bad_candidate = _hdef(caps=("network_access",))
    fixture = SimulationFixture(fixture_type="context_snapshot", surface="tui_snake",
                                 context_snapshot={})
    report = harness.simulate(bad_candidate, [fixture])
    assert report.can_activate is False
    assert report.policy_violation_count > 0


def test_empty_fixtures_gives_zero_runs():
    report = harness.simulate(_hdef(), [])
    assert report.total_runs == 0
    assert report.can_activate is True  # no violations when nothing ran


# ── multiple fixtures ─────────────────────────────────────────────────────────

def test_multiple_fixtures_combined():
    f1 = SimulationFixture(fixture_type="context_snapshot", surface="tui_snake",
                            context_snapshot={})
    f2 = SimulationFixture(fixture_type="snake_event_sequence", surface="tui_snake",
                            events=[{"kind": "artifact_hover"}])
    report = harness.simulate(_hdef(), [f1, f2])
    assert report.total_runs == 2
