from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.knowledge_index_forward_timeout import (
    resolve_knowledge_index_forward_budget_seconds,
)

KNOWLEDGE_INDEX_SUPERVISOR_GRACE_SECONDS = 30


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def resolve_effective_concurrency(
    *,
    requested_max_concurrency: int,
    security_policy: dict[str, Any],
    online_worker_capacity: int | None = None,
    runtime_capacity: int | None = None,
    ollama_capacity: int | None = None,
) -> int:
    requested = max(1, _safe_int(requested_max_concurrency, 1))
    security_cap = max(1, _safe_int((security_policy or {}).get("max_concurrency_cap"), 1))
    caps = [requested, security_cap]
    if online_worker_capacity is not None:
        caps.append(max(1, _safe_int(online_worker_capacity, 1)))
    if runtime_capacity is not None:
        caps.append(max(1, _safe_int(runtime_capacity, 1)))
    if ollama_capacity is not None:
        caps.append(max(1, _safe_int(ollama_capacity, 1)))
    return max(1, min(caps))


def resolve_dispatch_hard_timeout(
    *,
    tasks: list[Any],
    security_policy: dict[str, Any],
) -> int:
    """Resolve a Hub wait window from signed worker job budgets.

    The supervisor receives a small grace after the worker transport deadline
    so the transport owner can close the response and publish the terminal
    state before the generic Autopilot timeout path intervenes.
    """

    propose_timeout = max(
        1,
        _safe_int((security_policy or {}).get("propose_timeout"), 120),
    )
    execute_timeout = max(
        1,
        _safe_int((security_policy or {}).get("execute_timeout"), 60),
    )
    timeout = propose_timeout + execute_timeout + 30
    for task in tasks:
        if hasattr(task, "model_dump"):
            raw_task = task.model_dump()
        elif isinstance(task, Mapping):
            raw_task = dict(task)
        else:
            raw_task = dict(vars(task))
        if (
            str(raw_task.get("task_kind") or "").strip().lower()
            != "codecompass_index_build"
        ):
            continue
        try:
            transport_budget_seconds = (
                resolve_knowledge_index_forward_budget_seconds(
                    raw_task,
                    dispatch_phase="execute",
                )
            )
        except ValueError:
            # Invalid bindings fail in the governed dispatch gate.  They must
            # not inflate the supervisory wait window for unrelated tasks.
            continue
        if transport_budget_seconds is not None:
            timeout = max(
                timeout,
                propose_timeout
                + transport_budget_seconds
                + KNOWLEDGE_INDEX_SUPERVISOR_GRACE_SECONDS,
            )
    return timeout


def dispatch_queue_positions(dispatch_queue: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        str(item.get("task_id") or ""): item.get("queue_position")
        for item in dispatch_queue
        if item.get("task_id")
    }


def resolve_target_worker_for_task(
    *,
    task: Any,
    workers: list[Any],
    worker_cursor: int,
) -> tuple[Any, int, bool, str | None]:
    def _norm_url(value: Any) -> str:
        return str(value or "").strip().rstrip("/")

    hub_can_be_worker = bool(getattr(task, "_hub_can_be_worker", True))
    local_worker_url = _norm_url(getattr(task, "_local_worker_url", None))
    target_worker = None
    assigned_agent_url = _norm_url(getattr(task, "assigned_agent_url", None))
    if assigned_agent_url:
        if not hub_can_be_worker and local_worker_url and assigned_agent_url == local_worker_url:
            return None, worker_cursor, False, "assigned_worker_is_hub_forbidden"
        target_worker = next((w for w in workers if _norm_url(getattr(w, "url", None)) == assigned_agent_url), None)
    if target_worker is not None:
        return target_worker, worker_cursor, False, None
    if assigned_agent_url:
        return None, worker_cursor, False, "assigned_worker_offline"

    eligible_workers = list(workers or [])
    if not hub_can_be_worker and local_worker_url:
        filtered = [w for w in eligible_workers if _norm_url(getattr(w, "url", None)) != local_worker_url]
        if not filtered and eligible_workers:
            return None, worker_cursor, False, "hub_self_worker_filtered"
        eligible_workers = filtered
    if not eligible_workers:
        return None, worker_cursor, False, "no_workers_available"

    task_kind = str(getattr(task, "task_kind", None) or "").strip().lower()
    execution_context = getattr(task, "worker_execution_context", None)
    if task_kind == "codecompass_index_build" and isinstance(execution_context, dict):
        knowledge_index_job = execution_context.get("knowledge_index_job")
        if (
            isinstance(knowledge_index_job, dict)
            and knowledge_index_job.get("schema")
            == "ananta.knowledge_index_execution_job.v2"
        ):
            destination_selection = execution_context.get(
                "destination_selection"
            )
            if not isinstance(destination_selection, dict):
                return (
                    None,
                    worker_cursor,
                    False,
                    "destination_worker_selection_missing",
                )
            destination_worker_id = str(
                destination_selection.get("worker_id") or ""
            ).strip()
            matching_workers = [
                worker
                for worker in eligible_workers
                if str(getattr(worker, "name", None) or "").strip()
                == destination_worker_id
            ]
            if len(matching_workers) != 1:
                return (
                    None,
                    worker_cursor,
                    False,
                    "destination_worker_unavailable",
                )
            return (
                matching_workers[0],
                worker_cursor,
                True,
                "destination_worker_binding",
            )

    target_worker = eligible_workers[worker_cursor % len(eligible_workers)]
    return target_worker, worker_cursor + 1, True, None


def classify_no_candidate_reason(
    *,
    all_tasks: list[Any],
    workers_available_count: int,
) -> str:
    """APR-003: Classify why the dispatch queue has no candidates."""
    if not all_tasks:
        return "no_tasks"
    _TERMINAL = {
        "completed",
        "failed",
        "cancelled",
        "aborted",
        "timeout",
        "archived",
    }
    statuses = [str(getattr(t, "status", "") or "").strip().lower() for t in all_tasks]
    if all(s in _TERMINAL for s in statuses):
        return "all_terminal"
    if all(s in _TERMINAL | {"blocked_by_dependency"} for s in statuses):
        return "all_blocked_by_dependency"
    if workers_available_count == 0:
        return "no_workers_available"
    return "policy_or_state_blocked"


def build_tick_debug_payload(
    *,
    team_id_scope: str | None,
    total_tasks_unfiltered: int,
    total_tasks_scoped: int,
    candidate_count: int,
    workers_online_count: int,
    workers_available_count: int,
    no_candidate_reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "team_id_scope": team_id_scope,
        "total_tasks_unfiltered": total_tasks_unfiltered,
        "total_tasks_scoped": total_tasks_scoped,
        "candidate_count": candidate_count,
        "workers_online_count": workers_online_count,
        "workers_available_count": workers_available_count,
    }
    if no_candidate_reason is not None:
        payload["no_candidate_reason"] = no_candidate_reason
    return payload
