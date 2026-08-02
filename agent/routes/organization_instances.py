"""Thin scoped HTTP adapter for Organization instance lifecycle."""

from __future__ import annotations

from collections.abc import Mapping

from flask import Blueprint, current_app, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.organization_route_support import (
    OrganizationRouteError,
    organization_boundary,
    organization_catalog,
    organization_compile_service,
    request_payload,
    require_admin_grant_header,
    require_idempotency_key,
    require_if_match,
    require_if_match_header,
    require_organization_scope,
    require_project_scope,
)
from agent.services.organization_instance_application_service import (
    OrganizationInstanceApplicationService,
)
from agent.services.organization_read_service import OrganizationReadService
from agent.services.organization_runtime_application_service import (
    OrganizationRuntimeApplicationService,
)
from agent.services.project_access_authority import ProjectCapability

organization_instances_bp = Blueprint("organization_instances", __name__)


@organization_instances_bp.get("/api/organizations")
@check_auth
@organization_boundary
def list_organizations():
    principal, project = require_project_scope(ProjectCapability.READ)
    return api_response(
        data=_read_service().list_organizations(
            tenant_id=project.tenant_id,
            project_id=project.project_id,
            principal_id=principal.subject_id,
            cursor=request.args.get("cursor"),
            page_size=_page_size(request.args.get("page_size")),
        )
    )


@organization_instances_bp.post("/api/organizations")
@check_auth
@organization_boundary
def instantiate_organization():
    payload = request_payload(
        allowed_fields={
            "compile_plan",
            "title",
            "admin_grant",
            "project_id",
            "projectId",
        }
    )
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    client_plan = payload.get("compile_plan")
    if not isinstance(client_plan, Mapping):
        raise OrganizationRouteError("organization_compile_plan_required", status_code=400)
    idempotency_key = require_idempotency_key()
    grant_id = require_admin_grant_header(body_value=payload.get("admin_grant"))

    # Security boundary: the client plan is never materialized directly.  Its
    # signed binding is decoded for authenticated scope, then the current
    # server catalog is compiled again immediately before the guarded write.
    plan, bound = organization_compile_service().recompile_bound_plan(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        client_plan=client_plan,
    )
    require_if_match(plan.definition_revision)
    supplied_digest = str(request.headers.get("X-Plan-Digest") or "").strip()
    if supplied_digest and supplied_digest != plan.plan_digest:
        raise OrganizationRouteError("organization_plan_digest_stale", status_code=412)
    title = str(payload.get("title") or "").strip()
    if not title or len(title) > 255 or title != str(bound.get("title") or ""):
        raise OrganizationRouteError("organization_title_binding_invalid", status_code=422)

    result = _instance_service().instantiate(
        plan=plan,
        name=title,
        idempotency_key=idempotency_key,
        definition_revision=plan.definition_revision,
        plan_digest=plan.plan_digest,
        principal_id=principal.subject_id,
        grant_id=grant_id,
        admin_policy_hash=str(bound.get("admin_policy_hash") or ""),
        admission_exception_ref=(str(bound.get("admission_exception_ref") or "").strip() or None),
        custom_composition=(
            {str(key): int(value) for key, value in dict(bound.get("custom_composition") or {}).items()}
            if isinstance(bound.get("custom_composition"), Mapping)
            else None
        ),
    )
    summary = _read_service().organization_summary(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        organization_id=result.organization_id,
    )
    OrganizationRuntimeApplicationService(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        organization_id=result.organization_id,
    ).emit_event(
        event_type="organization_instantiated",
        correlation_id=result.organization_id,
        idempotency_key=f"organization-instantiation:{idempotency_key}",
        definition_revision=plan.definition_revision,
        snapshot_hash=result.topology_snapshot_hash,
        payload={
            "status": "draft",
            "unit_count": len(result.unit_ids),
            "team_count": len(result.team_ids),
            "role_slot_count": len(result.role_slot_ids),
        },
    )
    return api_response(
        data={
            "organization": summary,
            "unit_ids": list(result.unit_ids),
            "team_ids": list(result.team_ids),
            "role_slot_ids": list(result.role_slot_ids),
            "relation_ids": list(result.relation_ids),
            "organization_admin_grant_id": result.organization_admin_grant_id,
            "topology_snapshot_hash": result.topology_snapshot_hash,
            "replayed": result.idempotent_replay,
        },
        code=200 if result.idempotent_replay else 201,
    )


@organization_instances_bp.get("/api/organizations/<organization_id>")
@check_auth
@organization_boundary
def get_organization(organization_id: str):
    scope = require_organization_scope(organization_id)
    return api_response(
        data=_read_service().organization_summary(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            organization_id=scope.organization_id,
        )
    )


@organization_instances_bp.post("/api/organizations/<organization_id>/lifecycle")
@check_auth
@organization_boundary
def transition_organization_lifecycle(organization_id: str):
    scope = require_organization_scope(
        organization_id,
        ProjectCapability.MANAGE,
        include_archived=True,
    )
    payload = request_payload(
        allowed_fields={"target_state", "active_work_strategy", "migration_target", "admin_grant"}
    )
    target_state = str(payload.get("target_state") or "").strip().lower()
    if target_state not in {"draft", "validated", "active", "paused", "completed", "archived"}:
        raise OrganizationRouteError("organization_lifecycle_state_invalid", status_code=422)
    strategy = str(payload.get("active_work_strategy") or "").strip().lower() or None
    if strategy not in {None, "drain", "migrate", "cancel"}:
        raise OrganizationRouteError("organization_active_work_strategy_invalid", status_code=422)
    migration_target = payload.get("migration_target")
    if migration_target is not None and not isinstance(migration_target, dict):
        raise OrganizationRouteError("organization_migration_target_invalid", status_code=422)
    if strategy != "migrate" and migration_target is not None:
        raise OrganizationRouteError("organization_migration_target_unexpected", status_code=422)
    raw_revision = require_if_match_header()
    if not raw_revision.isdigit() or int(raw_revision) < 1:
        raise OrganizationRouteError("organization_if_match_invalid", status_code=400)
    expected_lock_version = int(raw_revision)
    idempotency_key = require_idempotency_key()
    grant_id = require_admin_grant_header(body_value=payload.get("admin_grant"))
    activity = _read_service().activity_snapshot(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        organization_id=scope.organization_id,
    )
    result = _instance_service().transition_lifecycle(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        organization_id=scope.organization_id,
        principal_id=scope.principal.subject_id,
        grant_id=grant_id,
        expected_lock_version=expected_lock_version,
        idempotency_key=idempotency_key,
        target_state=target_state,
        active_work_strategy=strategy,
        activity=activity,
        migration_target=migration_target,
    )
    return api_response(data=result)


def _instance_service() -> OrganizationInstanceApplicationService:
    return current_app.extensions.get(
        "organization_instance_application_service"
    ) or OrganizationInstanceApplicationService(catalog=organization_catalog())


def _read_service() -> OrganizationReadService:
    return current_app.extensions.get("organization_read_service") or OrganizationReadService(
        catalog=organization_catalog(),
        cursor_secret=current_app.config.get("SECRET_KEY"),
    )


def _page_size(raw) -> int:
    try:
        value = 50 if raw in {None, ""} else int(raw)
    except (TypeError, ValueError) as exc:
        raise OrganizationRouteError("organization_page_size_invalid", status_code=400) from exc
    if isinstance(value, bool) or not 1 <= value <= 100:
        raise OrganizationRouteError("organization_page_size_invalid", status_code=400)
    return value


__all__ = ["organization_instances_bp"]
