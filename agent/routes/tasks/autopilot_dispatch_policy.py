from __future__ import annotations

from typing import Any


def resolve_effective_concurrency(*, requested_max_concurrency: int, security_policy: dict[str, Any]) -> int:
    return max(1, min(int(requested_max_concurrency), int(security_policy.get("max_concurrency_cap") or 1)))


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


def build_tick_debug_payload(
    *,
    team_id_scope: str | None,
    total_tasks_unfiltered: int,
    total_tasks_scoped: int,
    candidate_count: int,
    workers_online_count: int,
    workers_available_count: int,
) -> dict[str, Any]:
    return {
        "team_id_scope": team_id_scope,
        "total_tasks_unfiltered": total_tasks_unfiltered,
        "total_tasks_scoped": total_tasks_scoped,
        "candidate_count": candidate_count,
        "workers_online_count": workers_online_count,
        "workers_available_count": workers_available_count,
    }
