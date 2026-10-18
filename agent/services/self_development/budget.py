"""Pure session/attempt budget evaluation for opt-in development runs.

A value of ``None`` or ``0`` in the operator policy means *unlimited*. The
finite bounds keep their previous clamps so existing policies stay bounded and
backward compatible; only an explicit opt-in lifts a limit.
"""
from __future__ import annotations

DEFAULT_SESSION_SECONDS = 1800
HARD_SESSION_SECONDS = 21600
HARD_ATTEMPT_CAP = 5


def unlimited(value) -> bool:
    return value is None or value == 0


def session_deadline(policy_value: dict, now: float) -> float | None:
    """Return the absolute session deadline, or ``None`` when unlimited."""
    max_seconds = policy_value.get('max_seconds', DEFAULT_SESSION_SECONDS)
    if unlimited(max_seconds):
        return None
    return now + min(HARD_SESSION_SECONDS, max_seconds or DEFAULT_SESSION_SECONDS)


def session_expired(session: dict, now: float) -> bool:
    expires_at = (session or {}).get('expires_at')
    return expires_at is not None and now >= expires_at


def session_active(policy_value: dict, session: dict, now: float) -> bool:
    """A session is usable unless stopped, disabled or past a finite deadline.

    With an unlimited ``max_seconds`` an already-issued session keeps running
    even though it still carries the finite deadline it was created with.
    """
    if not session or not session.get('enabled'):
        return False
    if unlimited(policy_value.get('max_seconds', DEFAULT_SESSION_SECONDS)):
        return True
    return not session_expired(session, now)


def task_budget_exhausted(policy_value: dict, session: dict) -> bool:
    max_tasks = policy_value.get('max_tasks', 1)
    if unlimited(max_tasks):
        return False
    return (session or {}).get('dispatched', 0) >= max_tasks


def attempt_budget_exhausted(policy_value: dict, attempts: list) -> bool:
    max_attempts = policy_value.get('max_attempts', 2)
    if unlimited(max_attempts):
        return False
    return len(attempts) + 1 >= min(HARD_ATTEMPT_CAP, max_attempts)
