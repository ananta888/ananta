"""Shared vocabulary and pure rules of Hub-owned recovery planning.

Split out of ``task_recovery_planning_service`` so that the service and its
extracted saga steps (approval saga, proposal, release, approval decision)
share one definition of the state/signal schemas, the recovery action set,
terminal statuses, the recovery-context budget and the pure recovery rules
(terminal/team-binding checks, plan lookups, plan rejection, audit sink).
The steps import the rules directly instead of reaching through the service
(ISP/DIP). The service module re-exports the vocabulary and keeps its
historic private staticmethod names as aliases of the rules.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agent.services.recovery_plan_contract import calculate_recovery_plan_digest
from agent.services.task_recovery_planning_values import mapping as _mapping

_log = logging.getLogger("agent.services.task_recovery_planning_service")

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


def is_terminal(record: Any, terminal_statuses: set[str]) -> bool:
    return str(getattr(record, "status", "") or "").strip().lower() in terminal_statuses


def team_binding_matches(
    *,
    source_task: Any,
    goal: Any,
    expected_team_id: str,
) -> bool:
    normalized_expected = str(expected_team_id or "").strip()
    return (
        str(getattr(source_task, "team_id", "") or "").strip()
        == normalized_expected
        and str(getattr(goal, "team_id", "") or "").strip()
        == normalized_expected
    )


def stored_team_binding_matches(
    stored: dict[str, Any],
    expected_team_id: str,
) -> bool:
    """Require an explicit persisted binding, including unscoped goals."""
    return (
        "team_id" in stored
        and str(stored.get("team_id") or "").strip()
        == str(expected_team_id or "").strip()
    )


def plan_digest(plan: Any, nodes: list[Any]) -> str:
    return calculate_recovery_plan_digest(plan, nodes)


def plan_action_configured(actions: list[str]) -> bool:
    return bool(
        {"segment_planning", "propose_task_plan"}.intersection(
            actions
        )
    )


def task_recovery_depth(task: Any) -> int:
    data = _mapping(task)
    reason = str(data.get("derivation_reason") or "").strip().lower()
    if reason == "goal_task_recovery":
        return 1
    details = _mapping(data.get("status_reason_details"))
    state = _mapping(details.get("model_recovery"))
    return max(0, int(state.get("recovery_depth") or 0))


def existing_plan(repos: Any, *, goal_id: str, recovery_key: str):
    for plan in list(repos.plan_repo.get_by_goal_id(goal_id) or []):
        if str(_mapping(getattr(plan, "rationale", None)).get("recovery_key") or "") == recovery_key:
            return plan
    return None


def active_plan_for_source(
    repos: Any,
    *,
    goal_id: str,
    source_task_id: str,
    exclude_plan_id: str | None = None,
):
    active_statuses = {
        "draft",
        "pending_approval",
        "approved",
        "materialized",
    }
    for plan in list(repos.plan_repo.get_by_goal_id(goal_id) or []):
        if exclude_plan_id and str(getattr(plan, "id", "") or "") == str(exclude_plan_id):
            continue
        rationale = _mapping(getattr(plan, "rationale", None))
        if (
            str(rationale.get("source_task_id") or "") == source_task_id
            and str(getattr(plan, "status", "") or "").strip().lower() in active_statuses
        ):
            return plan
    return None


def reject_plan(
    repos: Any,
    plan: Any,
    *,
    reason_code: str,
) -> None:
    plan.status = "rejected"
    plan.rationale = {
        **_mapping(getattr(plan, "rationale", None)),
        "approval_state": "stopped",
        "recovery_stop_reason": str(reason_code)[:160],
    }
    plan.updated_at = time.time()
    repos.plan_repo.save(plan)


def record_recovery_audit(action: str, details: dict[str, Any]) -> None:
    """Default audit sink of the recovery saga; an audit failure never fails the saga."""
    try:
        from agent.common.audit import log_audit

        log_audit(action, details)
    except Exception:
        _log.debug("task recovery audit failed", exc_info=True)
