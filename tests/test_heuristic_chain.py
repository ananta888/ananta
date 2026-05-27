"""Tests for Chain of Responsibility — chain.py, snake_rules.py, chat_selectors.py."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.chain import ChainResult, HeuristicRuleChainElement, RuleChain
from agent.services.heuristic_runtime.chat_selectors import (
    ActiveGoalSelector, ErrorHelpcenterSelector, NoMatchSelector,
    SelectedArtifactSelector, SymbolSelector, TodoSelector, build_chat_selector_chain,
)
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.snake_rules import (
    ArtifactHoverRule, DefaultFollowRule, DiffFocusRule, ErrorFocusRule,
    IdleLurkRule, build_snake_rule_chain,
)


def _ctx(surface="tui_snake", goal=None, panel=None, artifacts=None, events=None):
    return DecisionContext(
        source_surface=surface,
        ai_status="offline",
        active_goal_id=goal,
        active_panel=panel,
        selected_artifacts=artifacts or [],
        recent_events=events or [],
    )


# ── ChainResult factories ─────────────────────────────────────────────────────

def test_chain_result_handled():
    r = DecisionResult.heuristic_follow()
    cr = ChainResult.handled(r, rule_id="x")
    assert cr.status == "handled"
    assert cr.result is r
    assert cr.rule_id == "x"


def test_chain_result_abstain():
    cr = ChainResult.abstain(reason="skip")
    assert cr.status == "abstain"
    assert cr.result is None


def test_chain_result_continue():
    cr = ChainResult.continue_(rule_id="r1")
    assert cr.status == "continue"


# ── RuleChain execution ───────────────────────────────────────────────────────

class AlwaysAbstain(HeuristicRuleChainElement):
    priority = 1
    def handle(self, ctx, result): return ChainResult.abstain(rule_id="abstain")

class AlwaysHandle(HeuristicRuleChainElement):
    priority = 2
    def handle(self, ctx, result): return ChainResult.handled(DecisionResult.heuristic_lurk(), rule_id="handle")

class AlwaysContinue(HeuristicRuleChainElement):
    priority = 3
    def handle(self, ctx, result): return ChainResult.continue_(rule_id="cont")


def test_chain_short_circuits_on_handled():
    chain = RuleChain([AlwaysAbstain(), AlwaysHandle(), AlwaysContinue()])
    result = chain.run(_ctx())
    assert result.action_kind == "lurk"


def test_chain_returns_no_good_match_when_all_abstain():
    chain = RuleChain([AlwaysAbstain(), AlwaysAbstain()])
    result = chain.run(_ctx())
    assert result.is_no_good_match()


def test_chain_elements_sorted_by_priority():
    chain = RuleChain([AlwaysContinue(), AlwaysAbstain()])
    assert chain.elements[0].priority < chain.elements[1].priority


def test_chain_add_element_keeps_sorted():
    chain = RuleChain([AlwaysHandle()])  # prio 2
    chain.add(AlwaysAbstain())  # prio 1 → goes first
    assert chain.elements[0].priority < chain.elements[1].priority


# ── Snake Rules ───────────────────────────────────────────────────────────────

def test_artifact_hover_rule_triggers_on_event():
    ctx = _ctx(events=[{"kind": "artifact_select", "normalized_value": "ref1"}])
    cr = ArtifactHoverRule().handle(ctx, None)
    assert cr.status == "handled"
    assert cr.result.action_kind == "follow"


def test_artifact_hover_rule_abstains_without_event():
    cr = ArtifactHoverRule().handle(_ctx(), None)
    assert cr.status == "abstain"


def test_diff_focus_rule_triggers_on_editor_panel():
    cr = DiffFocusRule().handle(_ctx(panel="editor"), None)
    assert cr.status == "handled"
    assert cr.result.action_kind == "follow"


def test_diff_focus_rule_abstains_on_other_panel():
    cr = DiffFocusRule().handle(_ctx(panel="outline"), None)
    assert cr.status == "abstain"


def test_error_focus_rule_triggers():
    ctx = _ctx(events=[{"kind": "error_detected", "normalized_value": "NullPointerException"}])
    cr = ErrorFocusRule().handle(ctx, None)
    assert cr.status == "handled"


def test_idle_lurk_rule_triggers_without_goal():
    cr = IdleLurkRule().handle(_ctx(), None)
    assert cr.status == "handled"
    assert cr.result.action_kind == "lurk"


def test_idle_lurk_rule_abstains_with_goal():
    cr = IdleLurkRule().handle(_ctx(goal="g1"), None)
    assert cr.status == "abstain"


def test_default_follow_always_handled():
    cr = DefaultFollowRule().handle(_ctx(), None)
    assert cr.status == "handled"
    assert cr.result.action_kind == "follow"


def test_snake_chain_no_goal_returns_lurk():
    chain = build_snake_rule_chain()
    result = chain.run(_ctx())
    assert result.action_kind == "lurk"


def test_snake_chain_with_artifact_event_returns_follow():
    ctx = _ctx(events=[{"kind": "artifact_hover"}])
    result = build_snake_rule_chain().run(ctx)
    assert result.action_kind == "follow"


# ── Chat Selectors ────────────────────────────────────────────────────────────

def test_selected_artifact_selector_triggers():
    ctx = _ctx(artifacts=["ref1"])
    cr = SelectedArtifactSelector().handle(ctx, None)
    assert cr.status == "handled"
    assert cr.result.answer_kind == "context_summary"


def test_selected_artifact_selector_abstains_when_empty():
    cr = SelectedArtifactSelector().handle(_ctx(), None)
    assert cr.status == "abstain"


def test_active_goal_selector_includes_goal_ref():
    ctx = _ctx(goal="g42")
    cr = ActiveGoalSelector().handle(ctx, None)
    assert cr.status == "handled"
    assert any("goal:g42" in ref for ref in cr.result.selected_context_refs)


def test_error_helpcenter_selector_triggers_on_error_event():
    ctx = _ctx(events=[{"kind": "error_detected"}])
    cr = ErrorHelpcenterSelector().handle(ctx, None)
    assert cr.status == "handled"


def test_symbol_selector_triggers_on_keyword():
    ctx = _ctx(events=[{"kind": "chat_message", "normalized_value": "where is MyClass defined"}])
    cr = SymbolSelector().handle(ctx, None)
    assert cr.status == "handled"


def test_todo_selector_triggers_on_keyword():
    ctx = _ctx(events=[{"kind": "chat_message", "normalized_value": "what are the open tasks"}])
    cr = TodoSelector().handle(ctx, None)
    assert cr.status == "handled"


def test_no_match_selector_always_returns_no_good_match():
    cr = NoMatchSelector().handle(_ctx(), None)
    assert cr.result.is_no_good_match()


def test_chat_chain_artifact_beats_goal():
    ctx = _ctx(goal="g1", artifacts=["ref1"])
    result = build_chat_selector_chain().run(ctx)
    assert result.answer_kind == "context_summary"
    assert "ref1" in result.selected_context_refs


def test_chat_chain_no_match_fallback():
    result = build_chat_selector_chain().run(_ctx())
    assert result.is_no_good_match()
