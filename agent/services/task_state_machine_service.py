from __future__ import annotations

from agent.models import TaskStateMachineContract, TaskStateTransitionRule, TaskStatus, TaskStatusContract
from agent.services.task_status_service import _STATUS_ALIASES, normalize_task_status

TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.VERIFICATION_FAILED.value,
}

ACTIVE_TASK_STATUSES = {
    TaskStatus.TODO.value,
    TaskStatus.CREATED.value,
    TaskStatus.ASSIGNED.value,
    TaskStatus.IN_PROGRESS.value,
    TaskStatus.DELEGATED.value,
    TaskStatus.WAITING_FOR_REVIEW.value,
    TaskStatus.BLOCKED_BY_DEPENDENCY.value,
    TaskStatus.PAUSED.value,
    TaskStatus.PROPOSING.value,
    TaskStatus.UPDATED.value,
    "blocked",  # Legacy support
}

AUTOPILOT_DISPATCH_TASK_STATUSES = {
    TaskStatus.TODO.value,
    TaskStatus.CREATED.value,
    TaskStatus.ASSIGNED.value,
}

KNOWN_TASK_STATUSES = {s.value for s in TaskStatus} | {"blocked"}

_ACTION_TRANSITIONS = {
    "pause": {"from": ACTIVE_TASK_STATUSES - {TaskStatus.PAUSED.value}, "to": TaskStatus.PAUSED.value},
    "resume": {"from": {TaskStatus.PAUSED.value}, "to": TaskStatus.TODO.value},
    "cancel": {"from": ACTIVE_TASK_STATUSES, "to": TaskStatus.CANCELLED.value},
    "unassign": {"from": {TaskStatus.ASSIGNED.value, TaskStatus.CREATED.value}, "to": TaskStatus.TODO.value},
    "retry": {
        "from": {
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.VERIFICATION_FAILED.value,
        },
        "to": TaskStatus.TODO.value,
    },
    "approve": {"from": {TaskStatus.WAITING_FOR_REVIEW.value}, "to": TaskStatus.TODO.value},
    "request_review": {"from": {TaskStatus.IN_PROGRESS.value, TaskStatus.DELEGATED.value}, "to": TaskStatus.WAITING_FOR_REVIEW.value},
    "fail_verification": {"from": {TaskStatus.IN_PROGRESS.value, TaskStatus.DELEGATED.value}, "to": TaskStatus.VERIFICATION_FAILED.value},
    "block_on_dependency": {"from": ACTIVE_TASK_STATUSES - {TaskStatus.BLOCKED_BY_DEPENDENCY.value}, "to": TaskStatus.BLOCKED_BY_DEPENDENCY.value},
    "resolve_dependency": {"from": {TaskStatus.BLOCKED_BY_DEPENDENCY.value}, "to": TaskStatus.TODO.value},
}


def can_transition(action: str, current_status: str | None) -> tuple[bool, str]:
    current = normalize_task_status(current_status, default="")
    rule = _ACTION_TRANSITIONS.get(action)
    if not rule:
        return False, "invalid_action"
    if current not in rule["from"]:
        return False, "invalid_transition"
    return True, ""


def can_transition_to(current_status: str | None, next_status: str | None) -> tuple[bool, str]:
    current = normalize_task_status(current_status, default=TaskStatus.TODO.value)
    target = normalize_task_status(next_status, default=TaskStatus.TODO.value)

    if current == target:
        return True, ""

    # Immer erlaubt: Übergang von beliebig nach CANCELLED oder PAUSED (wenn in ACTIVE)
    if target == TaskStatus.CANCELLED.value:
        if current in ACTIVE_TASK_STATUSES:
            return True, ""
    if target == TaskStatus.PAUSED.value:
        if current in ACTIVE_TASK_STATUSES:
            return True, ""

    # Spezialfall: Von beliebigem aktivem Zustand nach ASSIGNED, IN_PROGRESS oder DELEGATED
    if target in {TaskStatus.ASSIGNED.value, TaskStatus.IN_PROGRESS.value, TaskStatus.DELEGATED.value}:
        if current in {TaskStatus.TODO.value, TaskStatus.CREATED.value, TaskStatus.ASSIGNED.value, TaskStatus.PROPOSING.value, TaskStatus.UPDATED.value}:
            return True, ""

    # Spezialfall: Abschluss/Fehler (Großzügiger für Legacy/Tests)
    if target in {TaskStatus.COMPLETED.value, TaskStatus.VERIFICATION_FAILED.value}:
        if current in {
            TaskStatus.TODO.value,
            TaskStatus.CREATED.value,
            TaskStatus.ASSIGNED.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.DELEGATED.value,
            TaskStatus.WAITING_FOR_REVIEW.value,
            TaskStatus.PROPOSING.value,
        }:
            return True, ""
    if target == TaskStatus.FAILED.value:
        if current in {
            TaskStatus.TODO.value,
            TaskStatus.CREATED.value,
            TaskStatus.ASSIGNED.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.DELEGATED.value,
            TaskStatus.WAITING_FOR_REVIEW.value,
            TaskStatus.PROPOSING.value,
            TaskStatus.BLOCKED_BY_DEPENDENCY.value,
        }:
            return True, ""

    # Prüfen, ob eine Action diesen Übergang abdeckt
    for action, rule in _ACTION_TRANSITIONS.items():
        if target == rule["to"] and current in rule["from"]:
            return True, ""

    # Erlaubte Standard-Kette: todo -> created -> assigned -> proposing -> in_progress
    progression = [
        TaskStatus.TODO.value,
        TaskStatus.CREATED.value,
        TaskStatus.ASSIGNED.value,
        TaskStatus.PROPOSING.value,
        TaskStatus.IN_PROGRESS.value,
    ]
    if current in progression and target in progression:
        if progression.index(target) > progression.index(current):
            return True, ""

    return False, f"illegal_transition_from_{current}_to_{target}"


def resolve_next_status(action: str, current_status: str | None, assigned_agent_url: str | None = None) -> str:
    current = normalize_task_status(current_status, default=TaskStatus.TODO.value)
    ok, _ = can_transition(action, current)
    if not ok:
        return current
    if action in {"resume", "retry", "approve", "resolve_dependency"} and assigned_agent_url:
        return TaskStatus.ASSIGNED.value
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
