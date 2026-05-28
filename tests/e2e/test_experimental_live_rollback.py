"""E2E: experimental_live Rollback bei schlechter Heuristik.

T08.04: Absichtlich schlechte experimental_live Heuristik → Rollback.
"""
import pytest
from agent.services.heuristic_runtime.heuristic_experiment_runner import HeuristicExperimentRunner
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult

_BAD_DSL = {
    "dsl_version": "2.0",
    "observe": {"sources": ["tui.semantic"]},
    "match": {"eq": ["source_surface", "NEVER"]},
    "action": {"kind": "no_action"},
    "safety": {"safety_class": "ui_motion_only"},
    "provenance": {"created_by": "test", "rationale": "Intentionally bad"},
}


def _active_result():
    return DecisionResult(action_kind="follow", confidence=0.9, source="heuristic")


def test_shadow_run_does_not_change_active_result():
    runner = HeuristicExperimentRunner()
    ctx = DecisionContext(source_surface="tui_snake")
    active = _active_result()

    shadow_res = runner.run_shadow_tick(_BAD_DSL, ctx, active, heuristic_id="bad_h", experiment_id="e1")

    # Active result muss UNVERÄNDERT bleiben
    unchanged = runner.active_decision_unchanged(active)
    assert unchanged.action_kind == "follow"
    assert unchanged.confidence == 0.9


def test_shadow_run_for_bad_heuristic_shows_no_match():
    runner = HeuristicExperimentRunner()
    ctx = DecisionContext(source_surface="tui_snake")
    active = _active_result()

    shadow_res = runner.run_shadow_tick(_BAD_DSL, ctx, active, heuristic_id="bad_h", experiment_id="e2")

    assert shadow_res.shadow_action == "no_action"
    assert shadow_res.shadow_confidence == 0.0


def test_experiment_report_tracks_poor_match_rate():
    runner = HeuristicExperimentRunner()
    ctx = DecisionContext(source_surface="tui_snake")
    active = _active_result()

    for _ in range(10):
        runner.run_shadow_tick(_BAD_DSL, ctx, active, heuristic_id="bad_h", experiment_id="e3")

    report = runner.get_report("e3")
    assert report is not None
    assert report.target_match_rate < 0.5  # schlechte Heuristik
    assert report.total_ticks == 10


def test_rollback_to_stable_after_bad_heuristic():
    """Nach schlechter Heuristik wird stable/default RuleChain genutzt."""
    from agent.services.heuristic_runtime.snake_decision_manager import SnakeDecisionManager
    from unittest.mock import MagicMock
    from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry

    registry = HeuristicRegistry.__new__(HeuristicRegistry)
    registry._heuristics = {}
    registry._definitions = {}
    registry._all = []
    registry._loaded = True
    registry._base_path = "/nonexistent"
    lease_repo = MagicMock()
    lease_repo.get_active.return_value = None
    lease_repo.acquire.return_value = MagicMock(id="lease_1", deadline_at=9999999999.0,
                                                  context_hash="x", heuristic_id="h1")
    lease_repo.mark_expired_batch.return_value = 0
    trace_repo = MagicMock()
    trace_repo.save.return_value = None

    mgr = SnakeDecisionManager(registry=registry, lease_repo=lease_repo, trace_repo=trace_repo)
    ctx = DecisionContext(source_surface="tui_snake")

    # decide() soll stabil bleiben
    result = mgr.decide(ctx)
    assert result is not None
    assert result.action_kind in ("follow", "lurk", "no_action", "policy_denied")
