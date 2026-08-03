"""Scoped HTTP boundary for the Hub-owned role-activation read model."""

from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.organization_route_support import (
    OrganizationRouteError,
    organization_boundary,
    organization_catalog,
    require_organization_scope,
)
from agent.services.organization_role_activation_read_service import (
    OrganizationRoleActivationReadError,
    OrganizationRoleActivationReadService,
)

organization_role_activation_bp = Blueprint(
    "organization_role_activation",
    __name__,
)


@organization_role_activation_bp.get("/api/organizations/<organization_id>/role-activation-map")
@check_auth
@organization_boundary
def get_organization_role_activation_map(organization_id: str):
    unknown = sorted(set(request.args))
    if unknown:
        raise OrganizationRouteError(
            "organization_query_fields_invalid",
            status_code=400,
            details={"unknown_fields": unknown},
        )
    scope = require_organization_scope(organization_id)
    try:
        projection = _read_service().read(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            organization=scope.organization,
        )
    except OrganizationRoleActivationReadError as exc:
        raise OrganizationRouteError(
            exc.reason_code,
            status_code=409,
            details=exc.details,
        ) from exc
    return api_response(data=projection)


def _read_service() -> OrganizationRoleActivationReadService:
    return current_app.extensions.get(
        "organization_role_activation_read_service"
    ) or OrganizationRoleActivationReadService(catalog=organization_catalog())


__all__ = ["organization_role_activation_bp"]
