"""Flask adapter for the generic Task read authorization boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import current_app

from agent.auth import get_authenticated_source_control_principal
from agent.common.errors import api_response
from agent.services.organization_membership_service import (
    OrganizationMembershipService,
)
from agent.services.task_read_access_service import (
    TaskReadAccessContext,
    TaskReadAccessError,
    get_task_read_access_service,
)


def task_read_access_context() -> TaskReadAccessContext:
    membership = current_app.extensions.get(
        "organization_membership_service"
    ) or OrganizationMembershipService()
    return TaskReadAccessContext(
        principal=get_authenticated_source_control_principal(),
        project_access=current_app.extensions.get("project_access_authority"),
        organization_membership=membership,
        service=current_app.extensions.get("task_read_access_service")
        or get_task_read_access_service(),
    )


def require_task_read(
    task: Mapping[str, Any] | Any,
    *,
    access: TaskReadAccessContext | None = None,
):
    payload = (
        dict(task)
        if isinstance(task, Mapping)
        else dict(task.model_dump())
        if hasattr(task, "model_dump")
        else {}
    )
    try:
        (access or task_read_access_context()).require(payload)
    except TaskReadAccessError as exc:
        return None, task_read_error_response(exc)
    return payload, None


def task_read_error_response(exc: TaskReadAccessError):
    message = "not_found" if exc.status_code == 404 else "service_unavailable"
    return api_response(
        status="error",
        message=message,
        data={"reason_code": exc.reason_code},
        code=exc.status_code,
    )


__all__ = [
    "require_task_read",
    "task_read_access_context",
    "task_read_error_response",
]
