"""Snake Audit Events — structured event-sourcing light (ASH-033).

All important actions in the snake heuristic lifecycle emit small, structured
events. Events contain NO raw data. They are in-memory, time-sortable, and
accessible via the TUI debug view.

Event types:
  snake_decision, candidate_created, candidate_duplicate_merged,
  candidate_validated, candidate_simulated, candidate_shadow_started,
  candidate_shadow_completed, candidate_shadow_watchdog_triggered,
  candidate_auto_promoted, candidate_rollout_stage_advanced,
  candidate_quarantined, heuristic_rollback
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any


_MAX_EVENTS = 200

# Module-level event store (append-only, bounded)
_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)


def emit(event_type: str, **kwargs: Any) -> str:
    """Emit a structured audit event. Returns the event_id."""
    event_id = str(uuid.uuid4())
    event: dict[str, Any] = {
        "event_id": event_id,
        "event_type": event_type,
        "ts": time.time(),
    }
    event.update(kwargs)
    _events.append(event)
    return event_id


def get_events(
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recent events, optionally filtered by type (newest last)."""
    result = list(_events)
    if event_type:
        result = [e for e in result if e.get("event_type") == event_type]
    return result[-limit:]


def clear_events() -> None:
    _events.clear()


# ── Typed emitters (no raw data) ──────────────────────────────────────────────

def snake_decision(*, heuristic_id: str, action_kind: str, fallback_reason: str) -> str:
    return emit("snake_decision",
                heuristic_id=heuristic_id,
                action_kind=action_kind,
                fallback_reason=fallback_reason or None)


def candidate_created(*, proposal_id: str, domain: str, fingerprint: str) -> str:
    return emit("candidate_created",
                proposal_id=proposal_id, domain=domain, fingerprint=fingerprint)


def candidate_duplicate_merged(*, fingerprint: str, existing_id: str) -> str:
    return emit("candidate_duplicate_merged",
                fingerprint=fingerprint, existing_id=existing_id)


def candidate_validated(*, proposal_id: str, passed: bool, reason_codes: list[str]) -> str:
    return emit("candidate_validated",
                proposal_id=proposal_id, passed=passed, reason_codes=reason_codes)


def candidate_simulated(*, proposal_id: str, can_activate: bool, policy_violations: int) -> str:
    return emit("candidate_simulated",
                proposal_id=proposal_id,
                can_activate=can_activate,
                policy_violations=policy_violations)


def candidate_shadow_started(*, candidate_id: str) -> str:
    return emit("candidate_shadow_started", candidate_id=candidate_id)


def candidate_shadow_completed(
    *,
    candidate_id: str,
    decision_count: int,
    duration_seconds: float,
    match_rate: float,
    passed: bool,
) -> str:
    return emit("candidate_shadow_completed",
                candidate_id=candidate_id,
                decision_count=decision_count,
                duration_seconds=round(duration_seconds, 2),
                match_rate=round(match_rate, 4),
                passed=passed)


def candidate_shadow_watchdog_triggered(
    *,
    candidate_id: str,
    trigger: str,
    value: float,
) -> str:
    return emit("candidate_shadow_watchdog_triggered",
                candidate_id=candidate_id,
                trigger=trigger,
                value=value)


def candidate_auto_promoted(*, candidate_id: str, reason_code: str) -> str:
    return emit("candidate_auto_promoted",
                candidate_id=candidate_id,
                reason_code=reason_code)


def candidate_rollout_stage_advanced(
    *,
    candidate_id: str,
    stage: str,
    quota: float,
    decisions_at_stage: int,
) -> str:
    return emit("candidate_rollout_stage_advanced",
                candidate_id=candidate_id,
                stage=stage,
                quota=quota,
                decisions_at_stage=decisions_at_stage)


def candidate_quarantined(*, candidate_id: str, reason_code: str) -> str:
    return emit("candidate_quarantined",
                candidate_id=candidate_id,
                reason_code=reason_code)


def heuristic_rollback(*, from_heuristic_id: str, to_heuristic_id: str, reason_code: str) -> str:
    return emit("heuristic_rollback",
                from_heuristic_id=from_heuristic_id,
                to_heuristic_id=to_heuristic_id,
                reason_code=reason_code)
