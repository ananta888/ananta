"""Validate the complete persisted scope of a Native command's Hub parent."""

from collections.abc import Mapping
from typing import Any

from agent.services.task_organization_scope import ORGANIZATION_SCOPE_ALL, inherited_organization_scope


def native_control_task_scope(task: Mapping[str, Any], *, command) -> dict[str, str]:
    scope = inherited_organization_scope(task)
    if (
        task.get("id") != command.control_task_id or task.get("tenant_id") != command.tenant_id
        or scope.get("tenant_id") != command.tenant_id or not scope.get("project_id")
        or scope["project_id"] != task.get("project_id")
        or any(task.get(key) is not None and task[key] != scope.get(key) for key in ORGANIZATION_SCOPE_ALL)
        or task.get("status") not in {"created", "assigned", "queued", "running", "in_progress"}
    ):
        raise ValueError("native_context_control_task_binding_mismatch")
    return scope
