from __future__ import annotations

import re
from typing import Any

from flask import current_app, has_app_context


def safe_scope_segment(value: str | None, *, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return fallback
    normalized = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-.")
    return normalized or fallback


def resolve_workspace_reuse_mode() -> str:
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) if has_app_context() else {}
    worker_runtime = (agent_cfg or {}).get("worker_runtime")
    worker_runtime = worker_runtime if isinstance(worker_runtime, dict) else {}
    mode = str(worker_runtime.get("workspace_reuse_mode") or "goal_worker").strip().lower()
    return mode if mode in {"task", "goal_worker"} else "goal_worker"


def derive_workspace_scope(
    *,
    parent_task: dict[str, Any],
    subtask_id: str,
    worker_job_id: str,
    agent_url: str | None,
) -> dict[str, str]:
    mode = resolve_workspace_reuse_mode()
    worker_key = safe_scope_segment(agent_url, fallback="worker")
    if mode == "task":
        scope_key = f"{subtask_id}:{worker_job_id}"
        return {
            "mode": "task",
            "scope_key": scope_key,
            "session_scope_kind": "task",
            "session_scope_key": f"task:{subtask_id}",
        }
    goal_key = safe_scope_segment(str(parent_task.get("goal_id") or "").strip(), fallback="")
    team_key = safe_scope_segment(str(parent_task.get("team_id") or "").strip(), fallback="")
    task_key = safe_scope_segment(str(parent_task.get("id") or "").strip(), fallback="task")
    continuity_key = goal_key or team_key or task_key
    scope_key = f"{worker_key}:{continuity_key}"
    return {
        "mode": "goal_worker",
        "scope_key": scope_key,
        "session_scope_kind": "workspace",
        "session_scope_key": f"workspace:{scope_key}",
    }


def build_worker_workspace(
    *,
    scope: dict[str, str],
    parent_task_id: str,
    subtask_id: str,
    worker_job_id: str,
    agent_url: str | None,
) -> dict[str, Any]:
    return {
        "mode": "task_scoped_workspace",
        "scope_mode": scope["mode"],
        "task_id": subtask_id,
        "parent_task_id": parent_task_id,
        "worker_job_id": worker_job_id,
        "agent_url": agent_url,
        "agent_name": str(agent_url or "worker").rstrip("/").split("/")[-1] or "worker",
        "scope_key": scope["scope_key"],
        "session_scope_kind": scope["session_scope_kind"],
        "session_scope_key": scope["session_scope_key"],
    }
