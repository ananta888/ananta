"""Shared vocabulary of Hub-owned recovery planning.

Split out of ``task_recovery_planning_service`` so that the service and its
extracted collaborators (approval saga, proposal, release, approval decision)
share one definition of the state/signal schemas, the recovery action set,
terminal statuses and the recovery-context budget. The service module
re-exports every name for compatibility.
"""

from __future__ import annotations

RECOVERY_STATE_SCHEMA = "ananta.task_recovery_state.v1"
RECOVERY_SIGNAL_SCHEMA = "model_recovery_signal.v1"
_RECOVERY_ACTIONS = {
    "compact_context",
    "segment_planning",
    "propose_task_plan",
    "require_approval",
    "stop",
}
_TERMINAL_GOAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "aborted",
    "timeout",
    "archived",
}
_TERMINAL_TASK_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "verification_failed",
    "skipped",
    "aborted",
    "timeout",
    "archived",
}


def _recovery_context_chars() -> int:
    """Recovery context: the "recovery_context" share of the effective window (a quarter; central policy)."""
    from agent.context_profile import context_budgets

    return context_budgets().chars("recovery_context")


def _record_recovery_cut(site: str, text: str, limit: int) -> str:
    """Record a hard character cut of recovery context (LCTX-002); returns ``text`` unchanged."""
    if len(text) > limit:
        from agent.context_window import estimate_tokens, record_truncation

        record_truncation(site, "char_cut", before_tokens=estimate_tokens(text),
                          after_tokens=estimate_tokens(text[:limit]), limit_chars=limit)
    return text
