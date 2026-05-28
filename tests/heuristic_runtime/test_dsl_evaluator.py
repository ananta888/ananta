"""Tests für DSL Evaluation Engine (T05.02)."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.dsl.evaluator import DslEvaluator, EvalResult


def _ctx(**kwargs):
    return DecisionContext(source_surface="tui_snake", **kwargs)


def _valid_dsl(*, action_kind="follow_artifact", match=None, score_base=0.8):
    dsl = {
        "dsl_version": "2.0",
        "observe": {"sources": ["tui.snapshot"]},
        "action": {"kind": action_kind, "confidence": score_base},
        "safety": {"safety_class": "ui_motion_only"},
        "provenance": {"created_by": "test", "rationale": "test"},
    }
    if match is not None:
        dsl["match"] = match
    dsl["score"] = {"base": score_base}
    return dsl


class TestDslEvaluator:
    def setup_method(self):
        self.evaluator = DslEvaluator()

    def test_matched_no_match_expr(self):
        dsl = _valid_dsl()
        ctx = _ctx()
        result = self.evaluator.evaluate(dsl, ctx)
        assert result.matched is True
        assert result.score > 0

    def test_no_match_when_match_expr_false(self):
        # match expression: eq where path resolves to None (key doesn't exist in ctx)
        # Using "not" to force False result
        dsl = _valid_dsl(match={"not": {"eq": ["source_surface", "source_surface"]}})
        ctx = _ctx()  # source_surface == source_surface → eq=True → not=False
        result = self.evaluator.evaluate(dsl, ctx)
        assert result.matched is False

    def test_matched_when_match_expr_true(self):
        # eq comparing a path value to itself → always True
        dsl = _valid_dsl(match={"eq": ["source_surface", "source_surface"]})
        ctx = _ctx()
        result = self.evaluator.evaluate(dsl, ctx)
        assert result.matched is True

    def test_score_uses_base(self):
        dsl = _valid_dsl(score_base=0.6)
        ctx = _ctx()
        result = self.evaluator.evaluate(dsl, ctx)
        assert result.matched
        assert abs(result.score - 0.6) < 1e-6

    def test_score_clamped_max(self):
        dsl = _valid_dsl()
        dsl["score"] = {"base": 2.0}  # > 1.0, clamped to 1.0
        ctx = _ctx()
        result = self.evaluator.evaluate(dsl, ctx)
        assert result.score <= 1.0

    def test_score_clamped_min(self):
        dsl = _valid_dsl()
        dsl["score"] = {"base": -5.0}  # < 0.0, clamped to 0.0
        ctx = _ctx()
        result = self.evaluator.evaluate(dsl, ctx)
        assert result.score >= 0.0

    def test_action_has_confidence(self):
        dsl = _valid_dsl(score_base=0.7)
        ctx = _ctx()
        result = self.evaluator.evaluate(dsl, ctx)
        assert "confidence" in result.action

    def test_deterministic_same_context(self):
        dsl = _valid_dsl(score_base=0.75)
        ctx = _ctx(active_goal_id="goal-1")
        r1 = self.evaluator.evaluate(dsl, ctx)
        r2 = self.evaluator.evaluate(dsl, ctx)
        assert r1.matched == r2.matched
        assert r1.score == r2.score

    def test_broken_dsl_returns_rejected(self):
        # Pass a completely broken DSL that will fail internally
        dsl = {"match": {"eq": None}}  # invalid eq
        ctx = _ctx()
        result = self.evaluator.evaluate(dsl, ctx)
        # Should either be rejected or matched=False, never raise
        assert isinstance(result, EvalResult)

    def test_exception_in_match_does_not_crash(self):
        # If expression raises internally, evaluator should catch it
        dsl = _valid_dsl()
        dsl["match"] = "not_a_dict"  # will cause TypeError in eval_expr
        ctx = _ctx()
        result = self.evaluator.evaluate(dsl, ctx)
        assert isinstance(result, EvalResult)

    def test_to_decision_result_matched(self):
        dsl = _valid_dsl(action_kind="follow_artifact", score_base=0.9)
        ctx = _ctx()
        eval_result = self.evaluator.evaluate(dsl, ctx)
        assert eval_result.matched
        decision = self.evaluator.to_decision_result(eval_result, strategy_id="test-strat")
        assert decision.action_kind in ("follow", "lurk", "no_action")

    def test_to_decision_result_not_matched(self):
        eval_result = EvalResult(matched=False, score=0.0, action={"kind": "no_action"})
        decision = self.evaluator.to_decision_result(eval_result)
        assert decision.is_no_good_match()

    def test_to_decision_result_rejected(self):
        eval_result = EvalResult(matched=True, score=0.5, action={"kind": "follow_artifact"},
                                 rejected=True, reject_reason="crash")
        decision = self.evaluator.to_decision_result(eval_result)
        assert decision.is_no_good_match()

    def test_lurk_near_maps_to_lurk(self):
        dsl = _valid_dsl(action_kind="lurk_near")
        ctx = _ctx()
        eval_result = self.evaluator.evaluate(dsl, ctx)
        decision = self.evaluator.to_decision_result(eval_result)
        assert decision.action_kind == "lurk"

    def test_no_action_returns_no_good_match(self):
        dsl = _valid_dsl(action_kind="no_action")
        ctx = _ctx()
        eval_result = self.evaluator.evaluate(dsl, ctx)
        decision = self.evaluator.to_decision_result(eval_result)
        assert decision.is_no_good_match()
