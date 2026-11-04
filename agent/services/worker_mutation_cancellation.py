"""Withdrawal checks for in-flight worker mutation loops.

A mutation loop can outlive the execution it belongs to: the Hub's forward
gives up (transport deadline), the strategy attempt is declared exhausted, or
the task reaches a terminal state while the worker keeps iterating. Such an
orphan keeps consuming the shared model backend and starves the healthy
proposes dispatched behind it. The loop asks this seam before every iteration
and aborts once its task no longer wants the result.

Deliberately conservative:

- Without a task binding there is nothing to consult; ad-hoc runs continue.
- A missing row does not authorize killing the execution: sessions may bind
  synthetic ids (tests, sandboxed labels) that never enter this database, and
  a vanished task cannot accept the persist anyway. Only a readable row in a
  terminal state is positive evidence that the execution was withdrawn.
- A failed lookup (transient DB error) continues the loop rather than killing
  a healthy execution on infrastructure noise.
- Only terminal task states withdraw execution; the check stays a plain read
  of the task row so workers and hub share one source of truth.
"""

from __future__ import annotations

import logging

from agent.services.repository_registry import get_repository_registry

_WITHDRAWN_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "verification_failed",
        "skipped",
        "aborted",
        "timeout",
        "archived",
    }
)


def is_mutation_execution_withdrawn(task_id: str | None) -> bool:
    """True when the task behind ``task_id`` no longer accepts this execution."""
    tid = str(task_id or "").strip()
    if not tid:
        return False
    try:
        task = get_repository_registry().task_repo.get_by_id(tid)
    except Exception:
        logging.getLogger(__name__).debug(
            "mutation withdrawal check failed for %s; continuing loop", tid, exc_info=True
        )
        return False
    if task is None:
        return False
    status = str(getattr(task, "status", "") or "").strip().lower()
    return status in _WITHDRAWN_STATUSES
