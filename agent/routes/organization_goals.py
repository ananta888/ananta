"""Scoped HTTP intake for passive Organization root Goals."""

from __future__ import annotations

from flask import Blueprint, current_app, g

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.models.organization_goal_models import OrganizationGoalCreateCommand
from agent.routes.organization_route_support import (
    OrganizationRouteError,
    organization_boundary,
    request_payload,
    require_idempotency_key,
    require_organization_scope,
)
from agent.services.organization_goal_application_service import (
    OrganizationGoalApplicationError,
    OrganizationGoalApplicationService,
)
from agent.services.organization_membership_service import OrganizationAccessPrincipal
from agent.services.project_access_authority import ProjectCapability

organization_goals_bp = Blueprint("organization_goals", __name__)


@organization_goals_bp.post("/api/organizations/<organization_id>/goals")
@check_auth
@organization_boundary
def create_organization_goal(organization_id: str):
    payload = request_payload(
        allowed_fields={
            "goal",
            "summary",
            "constraints",
            "acceptance_criteria",
        }
    )
    scope = require_organization_scope(organization_id, ProjectCapability.MANAGE)
    principal = OrganizationAccessPrincipal(
        principal_id=scope.principal.subject_id,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        credential_type=_credential_type(),
    )
    try:
        result = _service().create(
            principal=principal,
            organization_id=scope.organization_id,
            command=OrganizationGoalCreateCommand.model_validate(payload),
            idempotency_key=require_idempotency_key(),
        )
    except OrganizationGoalApplicationError as exc:
        raise OrganizationRouteError(
            exc.reason_code,
            status_code=exc.public_status,
        ) from exc
    return api_response(
        data=result.model_dump(mode="json"),
        code=200 if result.replayed else 201,
    )


def _service() -> OrganizationGoalApplicationService:
    return current_app.extensions.get(
        "organization_goal_application_service"
    ) or OrganizationGoalApplicationService()


def _credential_type() -> str:
    """Classify only authenticated server context; body claims are never authority."""

    service_identity = dict(getattr(g, "service_identity", {}) or {})
    identity = dict(getattr(g, "auth_payload", {}) or {})
    token_use = str(identity.get("token_use") or "").strip().lower()
    auth_mode = str(identity.get("auth_mode") or "").strip().lower()
    if (
        service_identity.get("worker_id")
        or token_use == "workflow_worker_service"
        or auth_mode == "registered_worker_service_token"
    ):
        return "worker"
    if (
        service_identity.get("service_id")
        or token_use == "workflow_runtime_service"
        or auth_mode == "preconfigured_runtime_service_token"
    ):
        return "service"
    if getattr(g, "user", {}) or {}:
        return "user"
    if auth_mode in {"agent_jwt", "agent_static_token"}:
        return "hub_service"
    return "unknown"


__all__ = ["organization_goals_bp"]
