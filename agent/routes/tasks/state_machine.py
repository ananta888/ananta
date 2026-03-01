from __future__ import annotations

from agent.routes.tasks.status import normalize_task_status

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"todo", "created", "assigned", "in_progress", "blocked", "paused"}

_ACTION_TRANSITIONS = {
    "pause": {"from": ACTIVE_STATUSES - {"paused"}, "to": "paused"},
    "resume": {"from": {"paused"}, "to": "todo"},
    "cancel": {"from": ACTIVE_STATUSES, "to": "cancelled"},
    "retry": {"from": {"failed", "cancelled"}, "to": "todo"},
}


def can_transition(action: str, current_status: str | None) -> tuple[bool, str]:
    current = normalize_task_status(current_status, default="")
    rule = _ACTION_TRANSITIONS.get(action)
    if not rule:
        return False, "invalid_action"
    if current not in rule["from"]:
        return False, "invalid_transition"
    return True, ""


def resolve_next_status(action: str, current_status: str | None, assigned_agent_url: str | None = None) -> str:
    current = normalize_task_status(current_status, default="todo")
    ok, _ = can_transition(action, current)
    if not ok:
        return current
    if action in {"resume", "retry"} and assigned_agent_url:
        return "assigned"
    return str(_ACTION_TRANSITIONS[action]["to"])


def can_autopilot_dispatch(status: str | None, manual_override_active: bool = False) -> bool:
    normalized = normalize_task_status(status, default="")
    if manual_override_active:
        return False
    return normalized in {"todo", "created", "assigned"}
