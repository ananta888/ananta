"""Admin diagnostics for the self-development pipeline (read-only).

One operator surface for the questions that otherwise cost an afternoon of
SQL and log spelunking: which backlog items are where, where the dispatch
queue stands and why candidates are skipped, which workers are admitted, and
which model/provider the effective configuration resolves to. Read-only by
construction: nothing here mutates state (SRP).
"""

from __future__ import annotations

import time
from typing import Any

_MAX_ROWS = 200
_ITEM_STATUSES = (
    "ready",
    "running",
    "pending_review",
    "review_passed",
    "publication_failed",
    "failed",
    "blocked",
)


def build_development_diagnostics(*, coordinator: Any, max_rows: int = _MAX_ROWS) -> dict:
    from agent.services.orchestration_policy.routing import build_dispatch_queue
    from agent.services.repository_registry import get_repository_registry

    state = coordinator.journal.read()
    items = list(state["items"].values())
    now = time.time()
    repos = get_repository_registry()
    team_id = str(coordinator.policy.value.get("team_id") or "")

    queue_tasks = [task for task in repos.task_repo.get_all() if str(getattr(task, "team_id", "") or "") == team_id]
    queue_rows = []
    for entry in build_dispatch_queue([task.model_dump() for task in queue_tasks]):
        task = next((t for t in queue_tasks if str(getattr(t, "id", "")) == str(entry.get("task_id"))), None)
        if task is None:
            continue
        created_at = float(getattr(task, "created_at", 0.0) or 0.0)
        queue_rows.append(
            {
                "position": entry.get("queue_position"),
                "task_id": str(getattr(task, "id", "") or ""),
                "task_kind": str(getattr(task, "task_kind", "") or ""),
                "priority": entry.get("priority"),
                "status": entry.get("status"),
                "assigned_agent_url": entry.get("assigned_agent_url"),
                "age_seconds": round(now - created_at, 1) if created_at else None,
            }
        )
    queue_rows = queue_rows[:max_rows]

    item_rows = []
    for item in items:
        status = str(item.get("status") or "")
        if status not in _ITEM_STATUSES:
            continue
        item_rows.append(
            {
                "todo_id": item.get("todo_id"),
                "status": status,
                "reason": str(item.get("reason") or "")[:120],
                "task_id": item.get("task_id"),
                "review_task_id": item.get("review_task_id"),
                "attempts": len(item.get("attempts") or []),
                "has_contract": bool(item.get("contract")),
                "review_feedback": bool(item.get("review_feedback")),
                "verdict": _verdict_summary(item),
            }
        )
    item_rows = item_rows[:max_rows]

    workers = []
    for agent in getattr(repos, "agent_repo", None).get_all() if getattr(repos, "agent_repo", None) else []:
        workers.append(
            {
                "name": getattr(agent, "name", ""),
                "status": getattr(agent, "status", ""),
                "url": getattr(agent, "url", ""),
                "registration_validated": bool(getattr(agent, "registration_validated", False)),
                "worker_roles": list(getattr(agent, "worker_roles", None) or []),
                "capabilities": len(getattr(agent, "capabilities", None) or []),
            }
        )

    skipped = {}
    queue_service = getattr(coordinator, "queue", None)
    if queue_service is not None and hasattr(queue_service, "last_skipped_candidates"):
        skipped = queue_service.last_skipped_candidates()

    return {
        "generated_at": int(now),
        "session": state.get("session"),
        "items": item_rows,
        "item_counts": {
            status: sum(1 for item in items if str(item.get("status") or "") == status)
            for status in sorted({str(item.get("status") or "") for item in items})
        },
        "dispatch_queue": queue_rows,
        "dispatch_candidates_skipped": dict(list(skipped.items())[:max_rows]),
        "workers": workers,
        "effective_models": _effective_model_summary(),
    }


def _verdict_summary(item: dict) -> dict | None:
    evidence = item.get("acceptance_review_evidence")
    if not isinstance(evidence, dict):
        return None
    summary: dict = {}
    for key in ("reason", "verdict_source", "criteria_count", "verdict_reason", "decided_by", "disagreed"):
        value = evidence.get(key)
        if value is not None:
            summary[key] = value
    decision = evidence.get("verdict_decision")
    if isinstance(decision, dict):
        summary["decision"] = {
            "mode": decision.get("mode"),
            "usable": decision.get("usable"),
            "deferred": decision.get("deferred"),
            "disagreed": decision.get("disagreed"),
        }
    return summary or None


def _effective_model_summary() -> dict:
    """The model/provider knobs an operator checks first; secrets never included."""
    from agent.cli_backends.helpers import _get_agent_config, _get_runtime_default_provider

    agent_cfg = _get_agent_config()
    opencode_runtime = agent_cfg.get("opencode_runtime") if isinstance(agent_cfg.get("opencode_runtime"), dict) else {}
    return {
        "default_provider": _get_runtime_default_provider() or None,
        "default_model": str(agent_cfg.get("default_model") or agent_cfg.get("model") or "") or None,
        "opencode_default_model": str(agent_cfg.get("opencode_default_model") or "") or None,
        "opencode_target_provider": str(opencode_runtime.get("target_provider") or "") or None,
        "opencode_target_model": str(opencode_runtime.get("target_model") or "") or None,
        "local_openai_backends": [
            {"provider": entry.get("provider") or entry.get("id"), "base_url": entry.get("base_url")}
            for entry in (agent_cfg.get("local_openai_backends") or [])
            if isinstance(entry, dict)
        ],
    }
