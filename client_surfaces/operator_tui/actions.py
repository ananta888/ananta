from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from client_surfaces.operator_tui.models import ActionDispatchResult, ActionRisk, OperatorAction


READ_ONLY_ACTIONS = {"inspect", "refresh", "open_browser", "copy_reference"}
MUTATION_ACTIONS = {"goal_create", "task_assign", "task_execute", "artifact_index"}


def build_audit_context(action: OperatorAction, *, source: str = "operator_tui") -> dict[str, Any]:
    return {
        "source": source,
        "action": action.name,
        "target": action.target,
        "risk": action.risk.value,
        "intent": "view" if action.risk is ActionRisk.READ_ONLY else "mutation_request",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def dispatch_action(action: OperatorAction, *, confirmed: bool = False) -> ActionDispatchResult:
    audit_context = build_audit_context(action)
    if action.name in READ_ONLY_ACTIONS and action.risk is ActionRisk.READ_ONLY:
        return ActionDispatchResult(True, f"read-only action accepted: {action.name}", audit_context)
    if action.name not in MUTATION_ACTIONS:
        return ActionDispatchResult(False, f"unknown action: {action.name}", audit_context)
    if action.requires_confirmation and not confirmed:
        return ActionDispatchResult(False, f"confirmation required: {action.name}", audit_context, pending_action=action)
    if action.risk in {ActionRisk.HIGH, ActionRisk.DESTRUCTIVE} and not confirmed:
        return ActionDispatchResult(False, f"confirmation required: {action.name}", audit_context, pending_action=action)
    return ActionDispatchResult(True, f"mutation action prepared for hub dispatch: {action.name}", audit_context)


def parse_action(name: str, target: str = "", risk: str = "read_only") -> OperatorAction:
    risk_value = ActionRisk(risk) if risk in {item.value for item in ActionRisk} else ActionRisk.READ_ONLY
    return OperatorAction(
        name=str(name or "").strip(),
        target=str(target or "").strip() or "current_selection",
        risk=risk_value,
        payload={},
        requires_confirmation=risk_value in {ActionRisk.HIGH, ActionRisk.DESTRUCTIVE},
    )
