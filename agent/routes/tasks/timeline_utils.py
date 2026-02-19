from __future__ import annotations

import json


def task_timeline_events(task: dict) -> list[dict]:
    tid = task.get("id")
    team_id = task.get("team_id")
    status = task.get("status")
    events: list[dict] = [
        {
            "event_type": "task_created",
            "task_id": tid,
            "team_id": team_id,
            "task_status": status,
            "timestamp": task.get("created_at"),
            "actor": task.get("assigned_agent_url") or "system",
            "details": {
                "title": task.get("title"),
                "description": task.get("description"),
                "parent_task_id": task.get("parent_task_id"),
            },
        }
    ]

    for item in task.get("history", []) or []:
        if not isinstance(item, dict):
            continue
        event_type = item.get("event_type") or "task_activity"
        events.append(
            {
                "event_type": event_type,
                "task_id": tid,
                "team_id": team_id,
                "task_status": status,
                "timestamp": item.get("timestamp") or task.get("updated_at"),
                "actor": item.get("delegated_to") or task.get("assigned_agent_url") or "system",
                "details": item,
            }
        )

    proposal = task.get("last_proposal") or {}
    if proposal:
        events.append(
            {
                "event_type": "proposal_snapshot",
                "task_id": tid,
                "team_id": team_id,
                "task_status": status,
                "timestamp": task.get("updated_at"),
                "actor": task.get("assigned_agent_url") or "system",
                "details": proposal,
            }
        )

    if task.get("last_output") or task.get("last_exit_code") is not None:
        events.append(
            {
                "event_type": "execution_result",
                "task_id": tid,
                "team_id": team_id,
                "task_status": status,
                "timestamp": task.get("updated_at"),
                "actor": task.get("assigned_agent_url") or "system",
                "details": {
                    "exit_code": task.get("last_exit_code"),
                    "output_preview": (task.get("last_output") or "")[:220],
                    "quality_gate_failed": "[quality_gate] failed:" in (task.get("last_output") or ""),
                },
            }
        )

    if task.get("parent_task_id"):
        events.append(
            {
                "event_type": "task_handoff",
                "task_id": tid,
                "team_id": team_id,
                "task_status": status,
                "timestamp": task.get("created_at"),
                "actor": "system",
                "details": {"parent_task_id": task.get("parent_task_id"), "reason": "followup_or_delegation"},
            }
        )

    return events


def is_error_timeline_event(event: dict) -> bool:
    event_type = str(event.get("event_type") or "").lower()
    details = event.get("details") or {}
    if event_type in {
        "tool_guardrail_blocked",
        "autopilot_security_policy_blocked",
        "autopilot_worker_failed",
        "quality_gate_failed",
    }:
        return True

    if isinstance(details, dict):
        if details.get("blocked_reasons"):
            return True
        exit_code = details.get("exit_code")
        if exit_code not in (None, 0):
            return True
        text = json.dumps(details, ensure_ascii=False).lower()
        if ("failed" in text) or ("error" in text):
            return True
    return False

