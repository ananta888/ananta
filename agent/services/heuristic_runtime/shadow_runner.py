"""Shadow Runner + Live Watchdog (ASH-020, ASH-023).

A candidate heuristic runs in shadow mode alongside the active heuristic:
  - Active heuristic still controls real snake movement.
  - Candidate computes a hypothetical decision in parallel (never applied).
  - Decisions are compared; match rate and decision count are accumulated.
  - The run requires BOTH min_decisions AND min_duration_seconds (AND gate).

Live Watchdog (ASH-023) monitors the shadow run and aborts if:
  - shadow_no_movement_frames > 10 consecutive
  - shadow_decision_loop_detected (same position 5x in a row)
  - shadow_exception_rate > 0.1
  - shadow_invalid_action_rate > 0.2

On watchdog trigger:
  - Shadow is aborted immediately.
  - Candidate is quarantined with reason_code shadow_watchdog_triggered.
  - Audit event is emitted (ASH-033).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


# Shadow thresholds (ASH-020): BOTH must be satisfied
SHADOW_MIN_DECISIONS = 50
SHADOW_MIN_DURATION_S = 30.0

# Watchdog triggers (ASH-023)
WATCHDOG_MAX_NO_MOVEMENT_FRAMES = 10
WATCHDOG_LOOP_REPEAT_COUNT = 5
WATCHDOG_MAX_EXCEPTION_RATE = 0.1
WATCHDOG_MAX_INVALID_ACTION_RATE = 0.2

_VALID_ACTION_KINDS = frozenset({
    "follow_with_distance", "lurk_near", "fast_target",
})


@dataclass
class ShadowDecision:
    action_kind: str
    active_action_kind: str
    position_hint: tuple[int, int] | None = None  # (col, row) or None
    exception: bool = False
    invalid_action: bool = False


@dataclass
class ShadowRunState:
    candidate_id: str
    started_at: float = field(default_factory=time.time)
    decisions: list[ShadowDecision] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""
    completed: bool = False
    # Watchdog counters
    consecutive_no_movement: int = 0
    recent_positions: list[tuple[int, int]] = field(default_factory=list)

    @property
    def decision_count(self) -> int:
        return len(self.decisions)

    @property
    def duration_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def match_rate(self) -> float:
        if not self.decisions:
            return 0.0
        matches = sum(
            1 for d in self.decisions
            if d.action_kind == d.active_action_kind and not d.exception
        )
        return matches / len(self.decisions)

    @property
    def exception_rate(self) -> float:
        if not self.decisions:
            return 0.0
        return sum(1 for d in self.decisions if d.exception) / len(self.decisions)

    @property
    def invalid_action_rate(self) -> float:
        if not self.decisions:
            return 0.0
        return sum(1 for d in self.decisions if d.invalid_action) / len(self.decisions)

    def is_ready_to_promote(self) -> bool:
        """Both thresholds must be met (AND gate)."""
        if self.aborted or self.decision_count < SHADOW_MIN_DECISIONS:
            return False
        return self.duration_seconds >= SHADOW_MIN_DURATION_S

    def to_summary(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "decision_count": self.decision_count,
            "duration_seconds": round(self.duration_seconds, 2),
            "match_rate": round(self.match_rate, 4),
            "exception_rate": round(self.exception_rate, 4),
            "invalid_action_rate": round(self.invalid_action_rate, 4),
            "aborted": self.aborted,
            "abort_reason": self.abort_reason or None,
            "completed": self.completed,
        }


class ShadowRunner:
    """Manages one shadow run for a single candidate."""

    def __init__(
        self,
        candidate: dict[str, Any],
        *,
        on_watchdog_trigger: Callable[[str, str, float], None] | None = None,
    ) -> None:
        self._candidate = candidate
        self._state = ShadowRunState(
            candidate_id=str(candidate.get("proposal_id") or candidate.get("heuristic_id") or "unknown")
        )
        self._on_watchdog_trigger = on_watchdog_trigger

    @property
    def state(self) -> ShadowRunState:
        return self._state

    @property
    def is_active(self) -> bool:
        return not self._state.aborted and not self._state.completed

    def record_decision(
        self,
        *,
        shadow_action_kind: str,
        active_action_kind: str,
        position_hint: tuple[int, int] | None = None,
        exception: bool = False,
    ) -> None:
        """Record one shadow decision and run watchdog checks."""
        if not self.is_active:
            return

        invalid = shadow_action_kind not in _VALID_ACTION_KINDS and not exception
        decision = ShadowDecision(
            action_kind=shadow_action_kind,
            active_action_kind=active_action_kind,
            position_hint=position_hint,
            exception=exception,
            invalid_action=invalid,
        )
        self._state.decisions.append(decision)

        # Watchdog: no-movement check (same position repeated)
        if position_hint is not None:
            positions = self._state.recent_positions
            positions.append(position_hint)
            if len(positions) > WATCHDOG_LOOP_REPEAT_COUNT + 1:
                positions.pop(0)
            # Detect loop: all recent positions the same
            if (len(positions) >= WATCHDOG_LOOP_REPEAT_COUNT
                    and len(set(positions)) == 1):
                self._trigger_watchdog("shadow_decision_loop_detected", WATCHDOG_LOOP_REPEAT_COUNT)
                return

        # Watchdog: no-movement frames
        if shadow_action_kind == "no_movement" or (
            shadow_action_kind == active_action_kind == "follow_with_distance"
            and position_hint is not None
        ):
            self._state.consecutive_no_movement += 1
            if self._state.consecutive_no_movement > WATCHDOG_MAX_NO_MOVEMENT_FRAMES:
                self._trigger_watchdog(
                    "shadow_no_movement_frames",
                    self._state.consecutive_no_movement,
                )
                return
        else:
            self._state.consecutive_no_movement = 0

        # Watchdog: exception rate (check after minimum 10 decisions)
        if len(self._state.decisions) >= 10:
            exc_rate = self._state.exception_rate
            if exc_rate > WATCHDOG_MAX_EXCEPTION_RATE:
                self._trigger_watchdog("shadow_exception_rate", exc_rate)
                return
            inv_rate = self._state.invalid_action_rate
            if inv_rate > WATCHDOG_MAX_INVALID_ACTION_RATE:
                self._trigger_watchdog("shadow_invalid_action_rate", inv_rate)
                return

        # Check completion
        if self._state.is_ready_to_promote():
            self._state.completed = True
            self._emit_completed()

    def _trigger_watchdog(self, trigger: str, value: float) -> None:
        self._state.aborted = True
        self._state.abort_reason = f"shadow_watchdog_triggered:{trigger}"
        try:
            from agent.services.heuristic_runtime import snake_audit_events as audit
            audit.candidate_shadow_watchdog_triggered(
                candidate_id=self._state.candidate_id,
                trigger=trigger,
                value=value,
            )
        except Exception:
            pass
        if self._on_watchdog_trigger:
            self._on_watchdog_trigger(self._state.candidate_id, trigger, value)

    def _emit_completed(self) -> None:
        try:
            from agent.services.heuristic_runtime import snake_audit_events as audit
            audit.candidate_shadow_completed(
                candidate_id=self._state.candidate_id,
                decision_count=self._state.decision_count,
                duration_seconds=self._state.duration_seconds,
                match_rate=self._state.match_rate,
                passed=True,
            )
        except Exception:
            pass

    def compute_candidate_action(self, heuristic: dict[str, Any], *, section: str, ai_status: str, artifact_present: bool) -> str:
        """Evaluate the candidate heuristic and return its hypothetical action_kind.

        Returns "exception" if evaluation fails.
        """
        try:
            triggers = (heuristic.get("runtime") or {}).get("triggers") or heuristic.get("triggers") or []
            if not triggers:
                matched = True
            else:
                matched = any(
                    self._trigger_matches(t, section=section, ai_status=ai_status, artifact_present=artifact_present)
                    for t in triggers if isinstance(t, dict)
                )
            if not matched:
                return "no_trigger_match"
            action = (heuristic.get("runtime") or {}).get("action") or heuristic.get("action") or {}
            return str(action.get("kind") or "follow_with_distance")
        except Exception:
            return "exception"

    @staticmethod
    def _trigger_matches(
        trigger: dict[str, Any],
        *,
        section: str,
        ai_status: str,
        artifact_present: bool,
    ) -> bool:
        for key, value in trigger.items():
            if key == "active_panel_is":
                if str(value) != "any" and str(value) != section:
                    return False
            elif key == "ai_status_is":
                if str(value) != ai_status:
                    return False
            elif key == "selected_artifact_present":
                if bool(value) != artifact_present:
                    return False
            else:
                return False
        return True
