"""Tests for HeuristicStrategy implementations — T03.01."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition
from agent.services.heuristic_runtime.strategy import (
    DefaultChatCodeCompassStrategy,
    DefaultEclipseSnakeStrategy,
    DefaultTuiSnakeStrategy,
    decide_for_context,
    get_strategy,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _hdef(hid: str, domain: str, strategy_kind: str = "follow", deterministic: bool = True) -> HeuristicDefinition:
    return HeuristicDefinition(
        heuristic_id=hid,
        version="1.0.0",
        domain=domain,
        strategy_kind=strategy_kind,
        description="test",
        deterministic=deterministic,
        safety_class="bounded",
        capabilities=("motion_suggest",),
        inputs=(),
        outputs=(),
        parameters={},
    )


def _ctx(surface: str, goal: str | None = None, panel: str | None = None, artifacts: list[str] | None = None) -> DecisionContext:
    return DecisionContext(
        source_surface=surface,
        ai_status="offline",
        active_goal_id=goal,
        active_panel=panel,
        selected_artifacts=artifacts or [],
    )


# ── DefaultTuiSnakeStrategy ───────────────────────────────────────────────────

class TestTuiSnakeStrategy:
    def setup_method(self):
        self.strategy = DefaultTuiSnakeStrategy()

    def test_domain(self):
        assert self.strategy.domain == "tui_snake"

    def test_follow_when_goal_present(self):
        candidates = [_hdef("h1", "tui_snake", "follow")]
        result = self.strategy.decide(_ctx("tui_snake", goal="g1"), candidates)
        assert result.action_kind == "follow"
        assert result.source == "heuristic"
        assert result.strategy_id == "h1"

    def test_lurk_when_no_goal(self):
        candidates = [_hdef("h1", "tui_snake", "lurk")]
        result = self.strategy.decide(_ctx("tui_snake"), candidates)
        assert result.action_kind == "lurk"
        assert result.strategy_id == "h1"

    def test_follow_preferred_over_lurk_when_goal_present(self):
        follow = _hdef("follow-h", "tui_snake", "follow")
        lurk = _hdef("lurk-h", "tui_snake", "lurk")
        result = self.strategy.decide(_ctx("tui_snake", goal="g1"), [lurk, follow])
        assert result.action_kind == "follow"
        assert result.strategy_id == "follow-h"

    def test_fallback_when_no_matching_strategy_kind(self):
        candidates = [_hdef("h1", "tui_snake", "custom_exotic")]
        result = self.strategy.decide(_ctx("tui_snake"), candidates)
        # No lurk candidates → fallback
        assert result.action_kind == "follow"
        assert result.fallback_reason == "no_matching_strategy_kind"

    def test_no_candidates_returns_no_good_match(self):
        result = self.strategy.decide(_ctx("tui_snake"), [])
        assert result.is_no_good_match()

    def test_prefers_deterministic_candidate(self):
        det = _hdef("det-h", "tui_snake", "follow", deterministic=True)
        non_det = _hdef("non-det-h", "tui_snake", "follow", deterministic=False)
        result = self.strategy.decide(_ctx("tui_snake", goal="g1"), [non_det, det])
        assert result.strategy_id == "det-h"


# ── DefaultEclipseSnakeStrategy ───────────────────────────────────────────────

class TestEclipseSnakeStrategy:
    def setup_method(self):
        self.strategy = DefaultEclipseSnakeStrategy()

    def test_domain(self):
        assert self.strategy.domain == "eclipse_snake"

    def test_follow_with_editor_panel(self):
        candidates = [_hdef("h1", "eclipse_snake", "zone")]
        result = self.strategy.decide(_ctx("eclipse_snake", panel="editor"), candidates)
        assert result.action_kind == "follow"
        assert result.suggested_motion is not None
        assert result.suggested_motion.dx == 1
        assert result.suggested_motion.dy == 0

    def test_follow_with_terminal_panel(self):
        candidates = [_hdef("h1", "eclipse_snake", "zone")]
        result = self.strategy.decide(_ctx("eclipse_snake", panel="terminal"), candidates)
        assert result.suggested_motion.dx == 0
        assert result.suggested_motion.dy == 1

    def test_lurk_when_no_panel(self):
        candidates = [_hdef("h1", "eclipse_snake", "zone")]
        result = self.strategy.decide(_ctx("eclipse_snake"), candidates)
        assert result.action_kind == "lurk"

    def test_unknown_panel_uses_zero_motion(self):
        candidates = [_hdef("h1", "eclipse_snake", "zone")]
        result = self.strategy.decide(_ctx("eclipse_snake", panel="unknown_panel"), candidates)
        assert result.action_kind == "follow"
        assert result.suggested_motion.dx == 0
        assert result.suggested_motion.dy == 0

    def test_no_candidates_returns_no_good_match(self):
        result = self.strategy.decide(_ctx("eclipse_snake"), [])
        assert result.is_no_good_match()


# ── DefaultChatCodeCompassStrategy ────────────────────────────────────────────

class TestChatCodeCompassStrategy:
    def setup_method(self):
        self.strategy = DefaultChatCodeCompassStrategy()

    def test_domain(self):
        assert self.strategy.domain == "chat_codecompass"

    def test_selects_context_refs_from_artifacts(self):
        candidates = [_hdef("c1", "chat_codecompass", "context_select")]
        ctx = _ctx("chat_codecompass", artifacts=["ref1", "ref2", "ref3"])
        result = self.strategy.decide(ctx, candidates)
        assert result.action_kind == "chat"
        assert result.answer_kind == "context_summary"
        assert result.selected_context_refs == ["ref1", "ref2", "ref3"]
        assert result.strategy_id == "c1"

    def test_no_artifacts_returns_no_good_match(self):
        candidates = [_hdef("c1", "chat_codecompass", "context_select")]
        result = self.strategy.decide(_ctx("chat_codecompass"), candidates)
        assert result.is_no_good_match()

    def test_caps_context_refs_at_five(self):
        candidates = [_hdef("c1", "chat_codecompass")]
        ctx = _ctx("chat_codecompass", artifacts=["a", "b", "c", "d", "e", "f", "g"])
        result = self.strategy.decide(ctx, candidates)
        assert len(result.selected_context_refs) == 5

    def test_no_candidates_returns_no_good_match(self):
        result = self.strategy.decide(_ctx("chat_codecompass", artifacts=["r1"]), [])
        assert result.is_no_good_match()


# ── get_strategy / decide_for_context ─────────────────────────────────────────

def test_get_strategy_returns_correct_type():
    assert isinstance(get_strategy("tui_snake"), DefaultTuiSnakeStrategy)
    assert isinstance(get_strategy("eclipse_snake"), DefaultEclipseSnakeStrategy)
    assert isinstance(get_strategy("chat_codecompass"), DefaultChatCodeCompassStrategy)


def test_get_strategy_returns_none_for_unknown():
    assert get_strategy("unknown_surface") is None


def test_decide_for_context_delegates():
    candidates = [_hdef("h1", "tui_snake", "lurk")]
    result = decide_for_context(_ctx("tui_snake"), candidates)
    assert result.action_kind == "lurk"


def test_decide_for_context_unknown_domain_fallback():
    ctx = DecisionContext(source_surface="unknown_domain", ai_status="offline")
    result = decide_for_context(ctx, [])
    assert result.action_kind == "follow"
    assert result.fallback_reason is not None
