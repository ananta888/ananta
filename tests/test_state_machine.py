"""Tests for SnakeStateMachine and ChatStateMachine — state_machine.py."""
from __future__ import annotations

import time

import pytest

from agent.services.heuristic_runtime.state_machine import (
    ChatStateMachine,
    FallbackActiveState,
    FollowingState,
    InvalidTransitionError,
    LurkingState,
    SnakeStateMachine,
    WaitingAiState,
)


def _ev(kind: str, ts: float | None = None) -> dict:
    return {"kind": kind, "timestamp": ts or time.time()}


# ── SnakeStateMachine ─────────────────────────────────────────────────────────

class TestSnakeStateMachine:
    def test_initial_state_is_lurking(self):
        sm = SnakeStateMachine()
        assert sm.state_name == "lurking"

    def test_lurking_to_following_on_goal_set(self):
        sm = SnakeStateMachine()
        sm.send(_ev("goal_set"))
        assert sm.state_name == "following"

    def test_following_to_lurking_on_goal_cleared(self):
        sm = SnakeStateMachine()
        sm.send(_ev("goal_set"))
        sm.send(_ev("goal_cleared"))
        assert sm.state_name == "lurking"

    def test_following_to_waiting_ai(self):
        sm = SnakeStateMachine()
        sm.send(_ev("goal_set"))
        sm.send(_ev("ai_request_sent"))
        assert sm.state_name == "waiting_ai"

    def test_waiting_ai_to_following_on_response(self):
        sm = SnakeStateMachine()
        sm.send(_ev("goal_set"))
        sm.send(_ev("ai_request_sent"))
        sm.send(_ev("ai_response_received"))
        assert sm.state_name == "following"

    def test_waiting_ai_to_fallback_on_timeout(self):
        sm = SnakeStateMachine()
        sm.send(_ev("goal_set"))
        sm.send(_ev("ai_request_sent"))
        sm.send(_ev("ai_timeout"))
        assert sm.state_name == "fallback_active"

    def test_waiting_ai_auto_timeout_on_stale_timestamp(self):
        state = WaitingAiState()
        state._entered_at = time.time() - 10  # past timeout
        next_s = state.on_event(_ev("unknown_event"))
        assert next_s is not None
        assert next_s.name == "fallback_active"

    def test_fallback_to_following_on_ai_response(self):
        sm = SnakeStateMachine()
        sm.send(_ev("goal_set"))
        sm.send(_ev("ai_request_sent"))
        sm.send(_ev("ai_timeout"))
        sm.send(_ev("ai_response_received"))
        assert sm.state_name == "following"

    def test_disabled_blocks_direct_to_following(self):
        sm = SnakeStateMachine()
        sm.send(_ev("disable"))
        with pytest.raises(InvalidTransitionError):
            sm._transition(FollowingState())

    def test_disabled_to_lurking_via_enable(self):
        sm = SnakeStateMachine()
        sm.send(_ev("disable"))
        sm.send(_ev("enable"))
        assert sm.state_name == "lurking"

    def test_history_tracks_transitions(self):
        sm = SnakeStateMachine()
        sm.send(_ev("goal_set"))
        sm.send(_ev("ai_request_sent"))
        assert sm.history == ["lurking", "following", "waiting_ai"]

    def test_unknown_event_no_transition(self):
        sm = SnakeStateMachine()
        transitioned = sm.send(_ev("noop_event"))
        assert not transitioned
        assert sm.state_name == "lurking"

    def test_disable_from_any_state(self):
        for initial_event in (None, "goal_set"):
            sm = SnakeStateMachine()
            if initial_event:
                sm.send(_ev(initial_event))
            sm.send(_ev("disable"))
            assert sm.state_name == "disabled"


# ── ChatStateMachine ──────────────────────────────────────────────────────────

class TestChatStateMachine:
    def test_initial_state_is_waiting_ai(self):
        sm = ChatStateMachine()
        assert sm.state_name == "waiting_ai"

    def test_waiting_ai_to_ai_answer_ready(self):
        sm = ChatStateMachine()
        sm.send(_ev("ai_response_received"))
        assert sm.state_name == "ai_answer_ready"

    def test_waiting_ai_timeout_to_heuristic_selection(self):
        sm = ChatStateMachine()
        sm.send(_ev("ai_timeout"))
        assert sm.state_name == "heuristic_context_selection"

    def test_waiting_ai_offline_to_heuristic_selection(self):
        sm = ChatStateMachine()
        sm.send(_ev("ai_offline"))
        assert sm.state_name == "heuristic_context_selection"

    def test_heuristic_selection_to_answer_ready(self):
        sm = ChatStateMachine()
        sm.send(_ev("ai_timeout"))
        sm.send(_ev("heuristic_answer_ready"))
        assert sm.state_name == "heuristic_answer_ready"

    def test_heuristic_selection_to_no_match(self):
        sm = ChatStateMachine()
        sm.send(_ev("ai_timeout"))
        sm.send(_ev("no_match"))
        assert sm.state_name == "no_match"

    def test_late_ai_response_becomes_stale(self):
        sm = ChatStateMachine()
        sm.send(_ev("ai_timeout"))
        sm.send(_ev("heuristic_answer_ready"))
        # Late AI arrives
        sm.send(_ev("ai_response_received"))
        assert sm.state_name == "stale_ai_answer"

    def test_stale_resets_to_waiting(self):
        sm = ChatStateMachine()
        sm.send(_ev("ai_timeout"))
        sm.send(_ev("heuristic_answer_ready"))
        sm.send(_ev("ai_response_received"))
        sm.send(_ev("reset"))
        assert sm.state_name == "waiting_ai"

    def test_waiting_ai_auto_timeout(self):
        from agent.services.heuristic_runtime.state_machine import ChatWaitingAiState
        state = ChatWaitingAiState()
        state._entered_at = time.time() - 10
        next_s = state.on_event(_ev("unknown"))
        assert next_s is not None
        assert next_s.name == "heuristic_context_selection"

    def test_policy_denied_transition(self):
        sm = ChatStateMachine()
        sm.send(_ev("policy_denied"))
        assert sm.state_name == "policy_denied"

    def test_policy_denied_resets(self):
        sm = ChatStateMachine()
        sm.send(_ev("policy_denied"))
        sm.send(_ev("reset"))
        assert sm.state_name == "waiting_ai"

    def test_history_tracked(self):
        sm = ChatStateMachine()
        sm.send(_ev("ai_timeout"))
        assert "waiting_ai" in sm.history
        assert "heuristic_context_selection" in sm.history
