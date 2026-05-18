from __future__ import annotations

from typing import Any


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


def dispatch_queue_positions(dispatch_queue: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(item.get("task_id") or ""): item.get("queue_position") for item in dispatch_queue if item.get("task_id")}


def resolve_target_worker_for_task(
    *,
    task: Any,
    workers: list[Any],
    worker_cursor: int,
) -> tuple[Any, int, bool]:
    target_worker = None
    if getattr(task, "assigned_agent_url", None):
        target_worker = next((w for w in workers if w.url == task.assigned_agent_url), None)
    if target_worker is not None:
        return target_worker, worker_cursor, False

    target_worker = workers[worker_cursor % len(workers)]
    return target_worker, worker_cursor + 1, True


def classify_no_candidate_reason(
    *,
    all_tasks: list[Any],
    workers_available_count: int,
) -> str:
    """APR-003: Classify why the dispatch queue has no candidates."""
    if not all_tasks:
        return "no_tasks"
    _TERMINAL = {"completed", "failed", "cancelled"}
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
