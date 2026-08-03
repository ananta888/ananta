"""Shared HTTP authorization adapter for task-scoped source read models."""

from __future__ import annotations

from flask import current_app

from agent.auth import get_authenticated_source_control_principal
from agent.common.errors import api_response
from agent.services.organization_membership_service import (
    OrganizationMembershipService,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.source_control_access_policy import (
    SourceControlAccessPolicyError,
)
from agent.services.task_source_verification_access_service import (
    TaskSourceVerificationAccessError,
    get_task_source_verification_access_service,
)


def _get_task_payload(task_id: str) -> dict | None:
    task = get_repository_registry().task_repo.get_by_id(task_id)
    if task is None:
        return None
    return task.model_dump()


def authorized_task_source_payload(task_id: str):
    """Return a Task projection only after all source-read fences pass."""

    task = _get_task_payload(task_id)
    if task is None:
        return None, api_response(
            status="error",
            message="not_found",
            code=404,
        )
    try:
        principal = get_authenticated_source_control_principal()
        organization_membership = current_app.extensions.get(
            "organization_membership_service"
        ) or OrganizationMembershipService()
        get_task_source_verification_access_service().require(
            task=task,
            principal=principal,
            project_access=current_app.extensions.get(
                "project_access_authority"
            ),
            organization_membership=organization_membership,
        )
    except SourceControlAccessPolicyError as exc:
        return None, api_response(
            status="error",
            message="forbidden",
            data={"reason_code": exc.reason_code},
            code=403,
        )
    except TaskSourceVerificationAccessError as exc:
        message = (
            "not_found"
            if exc.status_code == 404
            else "service_unavailable"
        )
        return None, api_response(
            status="error",
            message=message,
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )
    return task, None


__all__ = ["authorized_task_source_payload"]
