"""Thin read/presentation API for Organization hierarchy and graph views."""

from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.organization_route_support import (
    OrganizationRouteError,
    organization_boundary,
    organization_catalog,
    organization_membership_service,
    request_payload,
    require_organization_scope,
)
from agent.services.organization_read_service import OrganizationReadService
from agent.services.project_access_authority import ProjectCapability

organization_topology_bp = Blueprint("organization_topology", __name__)

_NODE_KINDS = {
    "organization",
    "coordination_unit",
    "value_stream",
    "team",
    "role_slot",
    "assignment",
}
_EDGE_NAMESPACES = {"hierarchy", "organization", "runtime"}


@organization_topology_bp.get("/api/organizations/<organization_id>/topology")
@check_auth
@organization_boundary
def get_organization_topology(organization_id: str):
    _require_query_fields(
        {
            "cursor",
            "page_size",
            "depth",
            "subgraph_root_id",
            "kinds",
            "edge_namespaces",
            "search",
            "include_runtime",
        }
    )
    scope = require_organization_scope(organization_id)
    page_size = _optional_int(request.args.get("page_size"), minimum=1, maximum=100)
    depth = _optional_int(request.args.get("depth"), minimum=0, maximum=10_000)
    kinds = _csv_values(request.args.get("kinds"), allowed=_NODE_KINDS)
    namespaces = _csv_values(
        request.args.get("edge_namespaces"),
        allowed=_EDGE_NAMESPACES,
    )
    search = str(request.args.get("search") or "").strip()
    subgraph_root = str(request.args.get("subgraph_root_id") or "").strip()
    if len(search) > 200 or len(subgraph_root) > 191:
        raise OrganizationRouteError("organization_topology_filter_invalid", status_code=400)
    return api_response(
        data=_read_service().topology(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            organization=scope.organization,
            include_runtime=_boolean_query(request.args.get("include_runtime"), default=True),
            cursor=request.args.get("cursor"),
            page_size=page_size,
            depth=depth,
            filters={
                **({"subgraph_root_id": subgraph_root} if subgraph_root else {}),
                **({"kinds": kinds} if kinds else {}),
                **({"edge_namespaces": namespaces} if namespaces else {}),
                **({"search": search} if search else {}),
            },
        )
    )


@organization_topology_bp.get("/api/organizations/<organization_id>/role-slots")
@check_auth
@organization_boundary
def get_organization_role_slots(organization_id: str):
    _require_query_fields(set())
    scope = require_organization_scope(organization_id)
    return api_response(
        data=_read_service().role_slots(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            organization_id=scope.organization_id,
        )
    )


@organization_topology_bp.get("/api/organizations/<organization_id>/role-slots/<role_slot_id>/assignment-candidates")
@check_auth
@organization_boundary
def get_organization_assignment_candidates(organization_id: str, role_slot_id: str):
    _require_query_fields(set())
    scope = require_organization_scope(organization_id, ProjectCapability.MANAGE)
    if not organization_membership_service().can_mutate(
        principal=scope.principal.membership_principal(),
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        organization_id=scope.organization_id,
        grant_kind="role_assignment",
    ):
        raise OrganizationRouteError("organization_not_found", status_code=404)
    if not role_slot_id or len(role_slot_id) > 191:
        raise OrganizationRouteError("organization_role_slot_not_found", status_code=404)
    return api_response(
        data=_read_service().assignment_candidates(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            organization_id=scope.organization_id,
            role_slot_id=role_slot_id,
        )
    )


@organization_topology_bp.get("/api/organizations/<organization_id>/layout-preferences")
@check_auth
@organization_boundary
def get_organization_layout_preferences(organization_id: str):
    _require_query_fields({"projection_mode"})
    scope = require_organization_scope(organization_id)
    return api_response(
        data={
            "preferences": _read_service().layout_preferences(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                organization_id=scope.organization_id,
                principal_id=scope.principal.subject_id,
                projection_mode=request.args.get("projection_mode", "graph"),
            ),
            "projection_mode": request.args.get("projection_mode", "graph"),
            "definition_revision": scope.organization.definition_revision,
        }
    )


@organization_topology_bp.put("/api/organizations/<organization_id>/layout-preferences")
@check_auth
@organization_boundary
def save_organization_layout_preferences(organization_id: str):
    scope = require_organization_scope(organization_id, ProjectCapability.WRITE)
    payload = request_payload(allowed_fields={"preferences", "projection_mode"})
    preferences = payload.get("preferences")
    if not isinstance(preferences, list):
        raise OrganizationRouteError("organization_layout_preferences_required", status_code=400)
    # This PUT stores only the authenticated principal's presentation state.
    # It is naturally idempotent and cannot alter topology, assignments,
    # lifecycle or policy; aggregate Admin-Grant/If-Match rules do not apply.
    return api_response(
        data=_read_service().save_layout_preferences(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            organization=scope.organization,
            principal_id=scope.principal.subject_id,
            projection_mode=str(payload.get("projection_mode") or "graph"),
            preferences=preferences,
        )
    )


def _read_service() -> OrganizationReadService:
    return current_app.extensions.get("organization_read_service") or OrganizationReadService(
        catalog=organization_catalog(),
        cursor_secret=current_app.config.get("SECRET_KEY"),
    )


def _require_query_fields(allowed: set[str]) -> None:
    unknown = sorted(set(request.args) - allowed)
    if unknown:
        raise OrganizationRouteError(
            "organization_query_fields_invalid",
            status_code=400,
            details={"unknown_fields": unknown},
        )


def _optional_int(raw, *, minimum: int, maximum: int) -> int | None:
    if raw in {None, ""}:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise OrganizationRouteError("organization_integer_parameter_invalid", status_code=400) from exc
    if not minimum <= value <= maximum:
        raise OrganizationRouteError("organization_integer_parameter_invalid", status_code=400)
    return value


def _boolean_query(raw, *, default: bool) -> bool:
    if raw in {None, ""}:
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise OrganizationRouteError("organization_boolean_parameter_invalid", status_code=400)


def _csv_values(raw, *, allowed: set[str]) -> list[str]:
    if not raw:
        return []
    values = [value.strip() for value in str(raw).split(",") if value.strip()]
    if len(values) > len(allowed) or any(value not in allowed for value in values):
        raise OrganizationRouteError("organization_topology_filter_invalid", status_code=400)
    return sorted(set(values))


__all__ = ["organization_topology_bp"]
