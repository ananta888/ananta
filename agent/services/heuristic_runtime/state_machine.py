"""State Pattern for Snake and Chat heuristic lifecycles.

SnakeStateMachine states:
  following, lurking, waiting_ai, fallback_active, explaining, chatting, disabled

ChatStateMachine states:
  waiting_ai, heuristic_context_selection, heuristic_answer_ready,
  ai_answer_ready, stale_ai_answer, no_match, policy_denied

Transitions are explicit; invalid transitions raise InvalidTransitionError.
Timeout events drive waiting_ai → fallback_active deterministically.
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any

_AI_TIMEOUT_SECONDS = 2.5  # mirrors ai_snake_worker_client.py default


class InvalidTransitionError(RuntimeError):
    pass


# ── Base ──────────────────────────────────────────────────────────────────────

class HeuristicState(abc.ABC):
    name: str = "base"

    def on_enter(self, ctx: dict[str, Any] | None = None) -> None: ...
    def on_exit(self) -> None: ...

    @abc.abstractmethod
    def on_event(self, event: dict[str, Any]) -> "HeuristicState | None":
        """Return the next state or None to stay in the current state."""
        ...


# ── Snake States ──────────────────────────────────────────────────────────────

class FollowingState(HeuristicState):
    name = "following"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        kind = event.get("kind", "")
        if kind == "ai_request_sent":
            return WaitingAiState()
        if kind == "goal_cleared":
            return LurkingState()
        if kind == "disable":
            return DisabledState()
        return None


class LurkingState(HeuristicState):
    name = "lurking"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        kind = event.get("kind", "")
        if kind == "goal_set":
            return FollowingState()
        if kind == "ai_request_sent":
            return WaitingAiState()
        if kind == "disable":
            return DisabledState()
        return None


class WaitingAiState(HeuristicState):
    name = "waiting_ai"

    def __init__(self) -> None:
        self._entered_at: float = time.time()

    def on_enter(self, ctx: dict[str, Any] | None = None) -> None:
        self._entered_at = time.time()

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        kind = event.get("kind", "")
        now = float(event.get("timestamp", time.time()))
        if kind == "ai_response_received":
            return FollowingState()
        if kind == "ai_timeout" or (now - self._entered_at) > _AI_TIMEOUT_SECONDS:
            return FallbackActiveState()
        if kind == "ai_offline":
            return FallbackActiveState()
        if kind == "disable":
            return DisabledState()
        return None


class FallbackActiveState(HeuristicState):
    name = "fallback_active"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        kind = event.get("kind", "")
        if kind == "ai_response_received":
            return FollowingState()
        if kind == "goal_cleared":
            return LurkingState()
        if kind == "disable":
            return DisabledState()
        return None


class ExplainingState(HeuristicState):
    name = "explaining"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        kind = event.get("kind", "")
        if kind in ("explain_done", "explain_cancelled"):
            return FollowingState()
        if kind == "disable":
            return DisabledState()
        return None


class ChattingState(HeuristicState):
    name = "chatting"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        kind = event.get("kind", "")
        if kind == "chat_done":
            return FollowingState()
        if kind == "disable":
            return DisabledState()
        return None


class DisabledState(HeuristicState):
    name = "disabled"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        # disabled → following only via explicit enable; never automatic
        if event.get("kind") == "enable":
            return LurkingState()
        return None


_SNAKE_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "following":       frozenset({"lurking", "waiting_ai", "disabled"}),
    "lurking":         frozenset({"following", "waiting_ai", "disabled"}),
    "waiting_ai":      frozenset({"following", "fallback_active", "disabled"}),
    "fallback_active": frozenset({"following", "lurking", "disabled"}),
    "explaining":      frozenset({"following", "disabled"}),
    "chatting":        frozenset({"following", "disabled"}),
    "disabled":        frozenset({"lurking"}),
}


class SnakeStateMachine:
    """State machine for the Snake heuristic lifecycle."""

    def __init__(self, initial: HeuristicState | None = None) -> None:
        self._state: HeuristicState = initial or LurkingState()
        self._state.on_enter()
        self._history: list[str] = [self._state.name]

    @property
    def state(self) -> HeuristicState:
        return self._state

    @property
    def state_name(self) -> str:
        return self._state.name

    def send(self, event: dict[str, Any]) -> bool:
        """Process event. Returns True if a transition occurred."""
        next_state = self._state.on_event(event)
        if next_state is None:
            return False
        self._transition(next_state)
        return True

    def _transition(self, next_state: HeuristicState) -> None:
        allowed = _SNAKE_ALLOWED_TRANSITIONS.get(self._state.name, frozenset())
        if next_state.name not in allowed:
            raise InvalidTransitionError(
                f"Snake: {self._state.name} → {next_state.name} is not allowed"
            )
        self._state.on_exit()
        self._state = next_state
        self._state.on_enter()
        self._history.append(next_state.name)

    @property
    def history(self) -> list[str]:
        return list(self._history)


# ── Chat States ───────────────────────────────────────────────────────────────

class ChatWaitingAiState(HeuristicState):
    name = "waiting_ai"

    def __init__(self) -> None:
        self._entered_at: float = time.time()

    def on_enter(self, ctx: dict[str, Any] | None = None) -> None:
        self._entered_at = time.time()

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        kind = event.get("kind", "")
        now = float(event.get("timestamp", time.time()))
        if kind == "ai_response_received":
            return ChatAiAnswerReadyState()
        if kind == "ai_timeout" or (now - self._entered_at) > _AI_TIMEOUT_SECONDS:
            return ChatHeuristicContextSelectionState()
        if kind == "ai_offline":
            return ChatHeuristicContextSelectionState()
        if kind == "policy_denied":
            return ChatPolicyDeniedState()
        return None


class ChatHeuristicContextSelectionState(HeuristicState):
    name = "heuristic_context_selection"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        kind = event.get("kind", "")
        if kind == "heuristic_answer_ready":
            return ChatHeuristicAnswerReadyState()
        if kind == "no_match":
            return ChatNoMatchState()
        if kind == "ai_response_received":
            return ChatStaleAiAnswerState()
        return None


class ChatHeuristicAnswerReadyState(HeuristicState):
    name = "heuristic_answer_ready"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        if event.get("kind") == "ai_response_received":
            return ChatStaleAiAnswerState()
        if event.get("kind") == "reset":
            return ChatWaitingAiState()
        return None


class ChatAiAnswerReadyState(HeuristicState):
    name = "ai_answer_ready"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        if event.get("kind") == "reset":
            return ChatWaitingAiState()
        return None


class ChatStaleAiAnswerState(HeuristicState):
    """Late AI response arrived after heuristic already answered — discard it."""
    name = "stale_ai_answer"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        if event.get("kind") == "reset":
            return ChatWaitingAiState()
        return None


class ChatNoMatchState(HeuristicState):
    name = "no_match"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        if event.get("kind") == "reset":
            return ChatWaitingAiState()
        return None


class ChatPolicyDeniedState(HeuristicState):
    name = "policy_denied"

    def on_event(self, event: dict[str, Any]) -> HeuristicState | None:
        if event.get("kind") == "reset":
            return ChatWaitingAiState()
        return None


_CHAT_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "waiting_ai":                  frozenset({"ai_answer_ready", "heuristic_context_selection", "policy_denied"}),
    "heuristic_context_selection": frozenset({"heuristic_answer_ready", "no_match", "stale_ai_answer"}),
    "heuristic_answer_ready":      frozenset({"stale_ai_answer", "waiting_ai"}),
    "ai_answer_ready":             frozenset({"waiting_ai"}),
    "stale_ai_answer":             frozenset({"waiting_ai"}),
    "no_match":                    frozenset({"waiting_ai"}),
    "policy_denied":               frozenset({"waiting_ai"}),
}


class ChatStateMachine:
    """State machine for the Chat heuristic lifecycle."""

    def __init__(self, initial: HeuristicState | None = None) -> None:
        self._state: HeuristicState = initial or ChatWaitingAiState()
        self._state.on_enter()
        self._history: list[str] = [self._state.name]

    @property
    def state(self) -> HeuristicState:
        return self._state

    @property
    def state_name(self) -> str:
        return self._state.name

    def send(self, event: dict[str, Any]) -> bool:
        next_state = self._state.on_event(event)
        if next_state is None:
            return False
        self._transition(next_state)
        return True

    def _transition(self, next_state: HeuristicState) -> None:
        allowed = _CHAT_ALLOWED_TRANSITIONS.get(self._state.name, frozenset())
        if next_state.name not in allowed:
            raise InvalidTransitionError(
                f"Chat: {self._state.name} → {next_state.name} is not allowed"
            )
        self._state.on_exit()
        self._state = next_state
        self._state.on_enter()
        self._history.append(next_state.name)

    @property
    def history(self) -> list[str]:
        return list(self._history)
