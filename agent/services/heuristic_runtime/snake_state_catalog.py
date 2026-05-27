"""AI-Snake state catalog (ASH-003).

SnakeState enumerates all valid operational states of the TUI AI snake.
Invalid transitions are prevented via VALID_TRANSITIONS.
"""
from __future__ import annotations

from enum import Enum


class SnakeState(str, Enum):
    """Operational state of the TUI AI snake."""

    DISABLED = "disabled"
    """Snake is off. No movement, no heuristics, no learning."""

    PAUSED = "paused"
    """Snake halts in place. Triggered by Space key. No state change until resumed."""

    OBSERVE_ONLY = "observe_only"
    """Snake observes TUI movement without steering or learning."""

    FOLLOW_USER = "follow_user"
    """Snake follows cursor with configurable distance."""

    INSPECT_ARTIFACT = "inspect_artifact"
    """Snake is inspecting a selected artifact at rest."""

    MOVE_TO_ARTIFACT = "move_to_artifact"
    """Snake is navigating toward a target artifact (fast_target movement)."""

    EXPLAIN_ARTIFACT = "explain_artifact"
    """Snake has arrived; explanation of artifact is shown."""

    CHAT_WITH_USER = "chat_with_user"
    """Interactive chat panel is open between user and snake AI."""

    WAITING_FOR_AI = "waiting_for_ai"
    """Snake is waiting for an async AI response. Falls back to HEURISTIC_FALLBACK on timeout."""

    HEURISTIC_FALLBACK = "heuristic_fallback"
    """Active heuristic is driving movement; no live AI response available."""

    CANDIDATE_SHADOW_TEST = "candidate_shadow_test"
    """A candidate heuristic is running in shadow mode (not controlling movement)."""

    CANDIDATE_ROLLOUT = "candidate_rollout"
    """Intermediate state during progressive rollout (quota < 100%)."""

    CANDIDATE_AUTO_ACTIVE = "candidate_auto_active"
    """An auto-promoted candidate heuristic is fully controlling movement."""

    QUARANTINED = "quarantined"
    """Snake is in safe mode; current heuristic was quarantined. Fallback to default."""

    ERROR = "error"
    """Unhandled exception occurred. Recovery via restart."""

    @classmethod
    def from_str(cls, value: str) -> "SnakeState":
        try:
            return cls(value)
        except ValueError:
            valid = ", ".join(s.value for s in cls)
            raise ValueError(
                f"Unknown snake_state {value!r}. Valid values: {valid}"
            ) from None


# Allowed transitions: source → set of reachable states
# ERROR is reachable from any state; DISABLED is always reachable.
_T = SnakeState

VALID_TRANSITIONS: dict[SnakeState, frozenset[SnakeState]] = {
    _T.DISABLED:              frozenset({_T.FOLLOW_USER, _T.OBSERVE_ONLY, _T.PAUSED}),
    _T.PAUSED:                frozenset({_T.FOLLOW_USER, _T.OBSERVE_ONLY, _T.HEURISTIC_FALLBACK,
                                          _T.CANDIDATE_SHADOW_TEST, _T.CANDIDATE_AUTO_ACTIVE,
                                          _T.DISABLED}),
    _T.OBSERVE_ONLY:          frozenset({_T.FOLLOW_USER, _T.PAUSED, _T.DISABLED}),
    _T.FOLLOW_USER:           frozenset({_T.PAUSED, _T.MOVE_TO_ARTIFACT, _T.HEURISTIC_FALLBACK,
                                          _T.WAITING_FOR_AI, _T.CANDIDATE_SHADOW_TEST,
                                          _T.CANDIDATE_AUTO_ACTIVE, _T.DISABLED, _T.QUARANTINED}),
    _T.INSPECT_ARTIFACT:      frozenset({_T.FOLLOW_USER, _T.EXPLAIN_ARTIFACT, _T.CHAT_WITH_USER,
                                          _T.PAUSED, _T.DISABLED}),
    _T.MOVE_TO_ARTIFACT:      frozenset({_T.EXPLAIN_ARTIFACT, _T.FOLLOW_USER, _T.PAUSED,
                                          _T.DISABLED, _T.QUARANTINED}),
    _T.EXPLAIN_ARTIFACT:      frozenset({_T.CHAT_WITH_USER, _T.FOLLOW_USER, _T.PAUSED, _T.DISABLED}),
    _T.CHAT_WITH_USER:        frozenset({_T.FOLLOW_USER, _T.HEURISTIC_FALLBACK, _T.PAUSED,
                                          _T.DISABLED}),
    _T.WAITING_FOR_AI:        frozenset({_T.FOLLOW_USER, _T.HEURISTIC_FALLBACK, _T.PAUSED,
                                          _T.DISABLED, _T.ERROR}),
    _T.HEURISTIC_FALLBACK:    frozenset({_T.FOLLOW_USER, _T.WAITING_FOR_AI, _T.PAUSED,
                                          _T.CANDIDATE_SHADOW_TEST, _T.CANDIDATE_AUTO_ACTIVE,
                                          _T.QUARANTINED, _T.DISABLED}),
    _T.CANDIDATE_SHADOW_TEST: frozenset({_T.FOLLOW_USER, _T.HEURISTIC_FALLBACK,
                                          _T.CANDIDATE_ROLLOUT, _T.QUARANTINED,
                                          _T.PAUSED, _T.DISABLED}),
    _T.CANDIDATE_ROLLOUT:     frozenset({_T.CANDIDATE_AUTO_ACTIVE, _T.HEURISTIC_FALLBACK,
                                          _T.QUARANTINED, _T.PAUSED, _T.DISABLED}),
    _T.CANDIDATE_AUTO_ACTIVE: frozenset({_T.FOLLOW_USER, _T.HEURISTIC_FALLBACK,
                                          _T.QUARANTINED, _T.PAUSED, _T.DISABLED}),
    _T.QUARANTINED:           frozenset({_T.HEURISTIC_FALLBACK, _T.FOLLOW_USER,
                                          _T.DISABLED, _T.PAUSED}),
    _T.ERROR:                 frozenset({_T.DISABLED, _T.PAUSED}),
}

# Every state can also transition to ERROR
for _src in list(VALID_TRANSITIONS):
    VALID_TRANSITIONS[_src] = VALID_TRANSITIONS[_src] | frozenset({_T.ERROR})


def is_valid_transition(from_state: SnakeState, to_state: SnakeState) -> bool:
    return to_state in VALID_TRANSITIONS.get(from_state, frozenset())
