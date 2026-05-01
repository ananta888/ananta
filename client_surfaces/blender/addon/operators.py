from __future__ import annotations

from typing import Any


def submit_blender_goal(goal: str, context: dict, capability: str, *, client: Any | None = None) -> dict:
    goal_text = str(goal or "").strip()
    if not goal_text:
        return {"status": "degraded", "reason": "goal_missing"}
    if client is not None and hasattr(client, "submit_goal"):
        return client.submit_goal(goal=goal_text, context=dict(context or {}), capability_id=str(capability or "").strip())
    return {
        "status": "accepted",
        "goal": goal_text,
        "capability": capability,
        "context": context,
        "task_id": "blend-task-preview",
    }


def submit_approval_decision(*, approval_id: str, decision: str, client: Any) -> dict:
    return client.submit_approval_decision(approval_id=approval_id, decision=decision)


def request_export_plan(*, fmt: str, target_path: str, selection_only: bool, client: Any) -> dict:
    return client.request_export_plan(fmt=fmt, target_path=target_path, selection_only=selection_only)
