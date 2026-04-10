from __future__ import annotations

import time

from .leasing import extract_active_lease
from agent.services.repository_registry import get_repository_registry
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


def _context_bundle_summary(task: dict) -> dict | None:
    bundle_id = str(task.get("context_bundle_id") or "").strip()
    if not bundle_id:
        return None
    bundle = get_repository_registry().context_bundle_repo.get_by_id(bundle_id)
    if bundle is None:
        return None
    metadata = dict(bundle.bundle_metadata or {})
    explainability = dict(metadata.get("explainability") or {})
    why = dict(metadata.get("why_this_context") or {})
    budget = dict(metadata.get("budget") or {})
    context_policy = dict(metadata.get("context_policy") or {})
    selection_trace = dict(metadata.get("selection_trace") or {})
    sources = list(explainability.get("sources") or [])
    why_top_sources = list(why.get("top_sources") or [])
    return {
        "context_bundle_id": bundle.id,
        "chunk_count": len(bundle.chunks or []),
        "token_estimate": int(bundle.token_estimate or 0),
        "engines": list(explainability.get("engines") or []),
        "top_sources": sources[:3],
        "why_top_sources": why_top_sources[:3],
        "why_summary": why.get("summary"),
        "retrieval_utilization": budget.get("retrieval_utilization"),
        "context_policy": {
            "mode": context_policy.get("mode"),
            "window_profile": context_policy.get("window_profile"),
            "bundle_strategy": context_policy.get("bundle_strategy"),
            "explainability_level": context_policy.get("explainability_level"),
            "chunk_text_style": context_policy.get("chunk_text_style"),
        },
        "selection_trace": {
            "knowledge_index_reason": selection_trace.get("knowledge_index_reason"),
            "result_memory_reason": selection_trace.get("result_memory_reason"),
        },
    }


def _task_neighborhood_summary(task: dict, *, tasks: list[dict]) -> dict:
    task_id = str(task.get("id") or "").strip()
    parent_id = str(task.get("parent_task_id") or "").strip()
    goal_id = str(task.get("goal_id") or "").strip()
    depends_on = [str(item).strip() for item in list(task.get("depends_on") or []) if str(item).strip()]

    downstream_ids: list[str] = []
    sibling_ids: list[str] = []
    goal_neighbor_ids: list[str] = []
    for item in tasks:
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id == task_id:
            continue
        item_depends_on = [str(dep).strip() for dep in list(item.get("depends_on") or []) if str(dep).strip()]
        if task_id and task_id in item_depends_on and item_id not in downstream_ids:
            downstream_ids.append(item_id)
        if parent_id and str(item.get("parent_task_id") or "").strip() == parent_id and item_id not in sibling_ids:
            sibling_ids.append(item_id)
        if goal_id and str(item.get("goal_id") or "").strip() == goal_id and item_id not in goal_neighbor_ids:
            goal_neighbor_ids.append(item_id)

    related: list[str] = []
    for value in [*depends_on, *downstream_ids, *sibling_ids, *goal_neighbor_ids]:
        if value and value != task_id and value not in related:
            related.append(value)
        if len(related) >= 16:
            break
    return {
        "depends_on": depends_on,
        "downstream": downstream_ids[:12],
        "siblings": sibling_ids[:12],
        "goal_neighbors": goal_neighbor_ids[:12],
        "related": related,
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
                "task_kind": t.get("task_kind"),
                "depends_on": list(t.get("depends_on") or []),
                "parent_task_id": t.get("parent_task_id"),
                "updated_at": t.get("updated_at"),
                "queue_position": next(
                    (item["queue_position"] for item in dispatch_queue if item["task_id"] == t.get("id")),
                    None,
                ),
                "context_bundle_summary": _context_bundle_summary(t),
                "task_neighborhood": _task_neighborhood_summary(t, tasks=tasks),
            }
            for t in recent
        ],
        "ts": time.time(),
    }
