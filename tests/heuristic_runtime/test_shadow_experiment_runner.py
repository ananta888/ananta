"""Tests für HeuristicExperimentRunner Shadow Mode (T07.03)."""
import pytest
from agent.services.heuristic_runtime.heuristic_experiment_runner import (
    HeuristicExperimentRunner,
    ShadowRunResult,
    ExperimentReport,
)
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult


_GOOD_DSL = {
    "dsl_version": "2.0",
    "observe": {"sources": ["tui.semantic"]},
    "match": {"eq": ["source_surface", "tui_snake"]},
    "action": {"kind": "follow_artifact", "confidence": 0.8},
    "safety": {"safety_class": "ui_motion_only"},
    "provenance": {"created_by": "test", "rationale": "Good heuristic"},
}

_NO_MATCH_DSL = {
    "dsl_version": "2.0",
    "observe": {"sources": ["tui.semantic"]},
    "match": {"eq": ["source_surface", "NEVER_MATCHES"]},
    "action": {"kind": "no_action", "confidence": 0.1},
    "safety": {"safety_class": "ui_motion_only"},
    "provenance": {"created_by": "test", "rationale": "Never matches"},
}


def _ctx() -> DecisionContext:
    return DecisionContext(source_surface="tui_snake")


def _active(action: str = "follow", confidence: float = 0.9) -> DecisionResult:
    return DecisionResult(action_kind=action, confidence=confidence, source="heuristic")


def test_shadow_run_returns_shadow_run_result():
    runner = HeuristicExperimentRunner()
    ctx = _ctx()
    active = _active()
    result = runner.run_shadow_tick(_GOOD_DSL, ctx, active, heuristic_id="h1", experiment_id="e1")
    assert isinstance(result, ShadowRunResult)
    assert result.heuristic_id == "h1"


def test_active_result_is_unchanged_by_shadow_run():
    """Sichtbarer DecisionResult bleibt UNVERÄNDERT."""
    runner = HeuristicExperimentRunner()
    ctx = _ctx()
    active = _active("follow", 0.95)

    runner.run_shadow_tick(_GOOD_DSL, ctx, active, heuristic_id="h1", experiment_id="e1")

    unchanged = runner.active_decision_unchanged(active)
    assert unchanged.action_kind == "follow"
    assert unchanged.confidence == 0.95
    assert unchanged is active  # Same object, not modified


def test_shadow_run_parallel_does_not_modify_active_result():
    """Multiple shadow ticks: active result always the same."""
    runner = HeuristicExperimentRunner()
    ctx = _ctx()
    active = _active("lurk", 0.7)

    for _ in range(5):
        runner.run_shadow_tick(_NO_MATCH_DSL, ctx, active, heuristic_id="h2", experiment_id="e2")

    # Active result must be unchanged
    assert active.action_kind == "lurk"
    assert active.confidence == 0.7


def test_reports_accumulate_correctly():
    """Reports werden korrekt akkumuliert."""
    runner = HeuristicExperimentRunner()
    ctx = _ctx()
    active = _active()

    for i in range(5):
        runner.run_shadow_tick(_GOOD_DSL, ctx, active, heuristic_id="h3", experiment_id="e3")

    report = runner.get_report("e3")
    assert report is not None
    assert isinstance(report, ExperimentReport)
    assert report.total_ticks == 5
    assert report.heuristic_id == "h3"
    assert report.experiment_id == "e3"


def test_separate_experiments_have_separate_reports():
    runner = HeuristicExperimentRunner()
    ctx = _ctx()
    active = _active()

    runner.run_shadow_tick(_GOOD_DSL, ctx, active, heuristic_id="h_a", experiment_id="exp_a")
    runner.run_shadow_tick(_GOOD_DSL, ctx, active, heuristic_id="h_b", experiment_id="exp_b")
    runner.run_shadow_tick(_GOOD_DSL, ctx, active, heuristic_id="h_b", experiment_id="exp_b")

    report_a = runner.get_report("exp_a")
    report_b = runner.get_report("exp_b")

    assert report_a is not None
    assert report_b is not None
    assert report_a.total_ticks == 1
    assert report_b.total_ticks == 2


def test_safety_violations_empty_for_valid_dsl():
    """safety violations sind leer für valide DSL."""
    runner = HeuristicExperimentRunner()
    ctx = _ctx()
    active = _active()

    result = runner.run_shadow_tick(_GOOD_DSL, ctx, active, heuristic_id="h1", experiment_id="e_safe")
    assert result.safety_violations == []


def test_no_match_dsl_gives_no_action():
    runner = HeuristicExperimentRunner()
    ctx = _ctx()
    active = _active("follow", 0.9)

    result = runner.run_shadow_tick(_NO_MATCH_DSL, ctx, active, heuristic_id="h_nm", experiment_id="e_nm")

    assert result.shadow_action == "no_action"
    assert result.shadow_confidence == 0.0
    assert result.target_match is False  # "no_action" != "follow"


def test_get_report_returns_none_for_unknown_experiment():
    runner = HeuristicExperimentRunner()
    assert runner.get_report("nonexistent") is None


def test_report_to_dict_has_expected_keys():
    runner = HeuristicExperimentRunner()
    ctx = _ctx()
    active = _active()
    runner.run_shadow_tick(_GOOD_DSL, ctx, active, heuristic_id="h1", experiment_id="e_dict")
    report = runner.get_report("e_dict")
    d = report.to_dict()
    assert "experiment_id" in d
    assert "total_ticks" in d
    assert "target_match_rate" in d
    assert d["total_ticks"] == 1
