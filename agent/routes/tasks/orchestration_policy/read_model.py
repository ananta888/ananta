from __future__ import annotations

import time

from .leasing import extract_active_lease
from agent.services.service_registry import get_core_services


def _services():
    return get_core_services()


def _fallback_queue_stats(tasks: list[dict]) -> dict:
    counts = {
        "todo": 0,
        "assigned": 0,
        "in_progress": 0,
        "blocked": 0,
        "completed": 0,
        "failed": 0,
    }
    by_agent: dict[str, int] = {}
    by_source: dict[str, int] = {}

    for task in tasks:
        status = str(task.get("status") or "").strip().lower()
        if status in counts:
            counts[status] += 1

        agent_url = str(task.get("assigned_agent_url") or "").strip()
        if agent_url:
            by_agent[agent_url] = by_agent.get(agent_url, 0) + 1

        source = str(task.get("source") or "").strip()
        if source:
            by_source[source] = by_source.get(source, 0) + 1

    return {
        "counts": counts,
        "depth": sum(counts.values()),
        "by_agent": by_agent,
        "by_source": by_source,
    }


def build_orchestration_read_model(tasks: list[dict]) -> dict:
    """
    Build a read model for orchestration status.

    Args:
        tasks: List of task dictionaries.

    Returns:
        Dictionary with queue stats, agent assignments, and leases.
    """
    try:
        tq_service = _services().task_queue_service
        stats = tq_service.get_queue_stats()
        dispatch_queue = tq_service.get_dispatch_queue()
    except RuntimeError:
        stats = _fallback_queue_stats(tasks)
        dispatch_queue = []

    leases: list[dict] = []
    for task in tasks:
        lease = extract_active_lease(task)
        if lease:
            leases.append(
                {
                    "task_id": task.get("id"),
                    "agent_url": lease.agent_url,
                    "lease_until": lease.lease_until,
                }
            )

    recent = sorted(tasks, key=lambda t: float(t.get("updated_at") or 0), reverse=True)[:40]

    return {
        "queue": stats["counts"],
        "queue_depth": stats["depth"],
        "by_agent": stats["by_agent"],
        "by_source": stats["by_source"],
        "active_leases": leases,
        "dispatch_queue": dispatch_queue[:40],
        "recent_tasks": [
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "status": t.get("status"),
                "priority": t.get("priority"),
                "assigned_agent_url": t.get("assigned_agent_url"),
                "updated_at": t.get("updated_at"),
                "queue_position": next(
                    (item["queue_position"] for item in dispatch_queue if item["task_id"] == t.get("id")),
                    None,
                ),
            }
            for t in recent
        ],
        "ts": time.time(),
    }
