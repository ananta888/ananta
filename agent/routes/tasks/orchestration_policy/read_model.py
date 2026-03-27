from __future__ import annotations

import time

from .leasing import extract_active_lease
from .routing import build_dispatch_queue


def build_orchestration_read_model(tasks: list[dict]) -> dict:
    """
    Build a read model for orchestration status.

    Args:
        tasks: List of task dictionaries.

    Returns:
        Dictionary with queue stats, agent assignments, and leases.
    """
    from agent.routes.tasks.status import normalize_task_status

    queue = {"todo": 0, "assigned": 0, "in_progress": 0, "blocked": 0, "completed": 0, "failed": 0}
    by_agent: dict[str, int] = {}
    by_source: dict[str, int] = {"ui": 0, "agent": 0, "system": 0, "unknown": 0}
    leases: list[dict] = []

    for task in tasks:
        status = normalize_task_status(task.get("status"), default="todo")
        queue[status] = queue.get(status, 0) + 1

        agent = task.get("assigned_agent_url")
        if agent:
            by_agent[agent] = by_agent.get(agent, 0) + 1

        history = task.get("history") or []
        if history:
            first_ingest = next(
                (h for h in history if isinstance(h, dict) and h.get("event_type") == "task_ingested"), None
            )
            source = str(((first_ingest or {}).get("details") or {}).get("source") or "unknown").lower()
            by_source[source if source in by_source else "unknown"] += 1

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
    dispatch_queue = build_dispatch_queue(tasks)

    return {
        "queue": queue,
        "queue_depth": len(dispatch_queue),
        "by_agent": by_agent,
        "by_source": by_source,
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
