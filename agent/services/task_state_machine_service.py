from __future__ import annotations

from agent.models import TaskStateMachineContract, TaskStateTransitionRule, TaskStatusContract
from agent.services.task_status_service import _STATUS_ALIASES, normalize_task_status

TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_TASK_STATUSES = {"todo", "created", "assigned", "in_progress", "blocked", "paused"}
AUTOPILOT_DISPATCH_TASK_STATUSES = {"todo", "created", "assigned"}
KNOWN_TASK_STATUSES = {
    "todo",
    "created",
    "assigned",
    "in_progress",
    "blocked",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "archived",
    "proposing",
    "updated",
}

_ACTION_TRANSITIONS = {
    "pause": {"from": ACTIVE_TASK_STATUSES - {"paused"}, "to": "paused"},
    "resume": {"from": {"paused"}, "to": "todo"},
    "cancel": {"from": ACTIVE_TASK_STATUSES, "to": "cancelled"},
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
    return normalized in AUTOPILOT_DISPATCH_TASK_STATUSES


def build_task_status_contract() -> TaskStatusContract:
    return TaskStatusContract(
        canonical_values=sorted(KNOWN_TASK_STATUSES),
        terminal_values=sorted(TERMINAL_TASK_STATUSES),
        active_values=sorted(ACTIVE_TASK_STATUSES),
        autopilot_dispatch_values=sorted(AUTOPILOT_DISPATCH_TASK_STATUSES),
        aliases=dict(sorted(_STATUS_ALIASES.items())),
    )


def build_task_state_machine_contract() -> TaskStateMachineContract:
    return TaskStateMachineContract(
        transitions=[
            TaskStateTransitionRule(
                action=action,
                from_statuses=sorted(rule["from"]),
                to_status=str(rule["to"]),
            )
            for action, rule in sorted(_ACTION_TRANSITIONS.items())
        ],
        notes={
            "resume_or_retry_with_assignment_promotes_to": "assigned",
            "manual_override_blocks_autopilot_dispatch": True,
        },
    )
