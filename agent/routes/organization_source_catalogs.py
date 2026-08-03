"""Organization-scoped Hub Source Catalog publication API."""

from __future__ import annotations

from flask import Blueprint, current_app, g

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.models.organization_source_catalog_models import (
    OrganizationSourceCatalogPublishCommand,
)
from agent.routes.organization_route_support import (
    OrganizationRouteError,
    organization_boundary,
    request_payload,
    require_idempotency_key,
    require_organization_scope,
)
from agent.services.organization_source_catalog_publisher_service import (
    OrganizationSourceCatalogPublisherError,
    OrganizationSourceCatalogPublisherPrincipal,
    OrganizationSourceCatalogPublisherService,
)
from agent.services.organization_source_catalog_query_adapter import (
    ProductionOrganizationSourceCatalogQueryAdapter,
)
from agent.services.project_access_authority import ProjectCapability

organization_source_catalogs_bp = Blueprint(
    "organization_source_catalogs", __name__
)


@organization_source_catalogs_bp.post(
    "/api/organizations/<organization_id>/source-catalogs"
)
@check_auth
@organization_boundary
def publish_organization_source_catalog(organization_id: str):
    payload = request_payload(
        allowed_fields={"connection_id", "queries", "limit"}
    )
    scope = require_organization_scope(
        organization_id,
        ProjectCapability.MANAGE,
        include_archived=False,
    )
    principal = OrganizationSourceCatalogPublisherPrincipal(
        subject_id=scope.principal.subject_id,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        roles=frozenset(scope.principal.roles),
        project_role=str(scope.project.role),
        credential_type=_credential_type(),
    )
    try:
        result = _service().publish(
            principal=principal,
            organization_id=scope.organization_id,
            command=OrganizationSourceCatalogPublishCommand.model_validate(payload),
            idempotency_key=require_idempotency_key(),
        )
    except OrganizationSourceCatalogPublisherError as exc:
        raise OrganizationRouteError(
            exc.reason_code,
            status_code=exc.public_status,
        ) from exc
    return api_response(
        data=result.model_dump(mode="json"),
        code=200 if result.replayed else 201,
    )


def _service() -> OrganizationSourceCatalogPublisherService:
    configured = current_app.extensions.get(
        "organization_source_catalog_publisher_service"
    )
    if configured is not None:
        return configured
    return OrganizationSourceCatalogPublisherService(
        query_port=ProductionOrganizationSourceCatalogQueryAdapter(
            current_app.extensions.get("source_control_v1_core_runtime")
        )
    )


def _credential_type() -> str:
    """Classify server-authenticated context; request bodies are never identity."""

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


__all__ = ["organization_source_catalogs_bp"]
