"""Thin HTTP adapter for scoped Organization definitions and compilation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Mapping

from flask import Blueprint, current_app, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.models.organization_models import (
    VersionedDefinitionRef,
    canonical_definition_sha256,
)
from agent.routes.organization_route_support import (
    OrganizationRouteError,
    organization_admission_exception_service,
    organization_boundary,
    organization_compile_service,
    organization_definition_service,
    project_plan_grant_service,
    request_payload,
    require_admin_grant_header,
    require_idempotency_key,
    require_if_match,
    require_if_match_header,
    require_project_scope,
)
from agent.services.organization_compile_application_service import (
    OrganizationCompileApplicationService,
)
from agent.services.project_access_authority import ProjectCapability

organization_blueprints_bp = Blueprint("organization_blueprints", __name__)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@organization_blueprints_bp.get("/api/organization-blueprints")
@check_auth
@organization_boundary
def list_organization_blueprints():
    principal, project = require_project_scope(ProjectCapability.READ)
    page_size = _bounded_int(request.args.get("page_size"), default=50, minimum=1, maximum=100)
    items = organization_compile_service().list_blueprint_summaries(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
    )
    cursor_scope = {
        "tenant_id": project.tenant_id,
        "project_id": project.project_id,
        "principal_id": principal.subject_id,
        "catalog_revision": canonical_definition_sha256(items),
    }
    offset = _decode_cursor(request.args.get("cursor"), cursor_scope)
    page = items[offset : offset + page_size]
    next_offset = offset + len(page)
    return api_response(
        data={
            "items": page,
            "next_cursor": (_encode_cursor(next_offset, cursor_scope) if next_offset < len(items) else None),
            "tenant_id": principal.tenant_id,
            "project_id": project.project_id,
        }
    )


@organization_blueprints_bp.get("/api/organization-blueprints/<path:blueprint_key>")
@check_auth
@organization_boundary
def get_organization_blueprint(blueprint_key: str):
    _principal, project = require_project_scope(ProjectCapability.READ)
    key, _team_count = OrganizationCompileApplicationService.parse_selector(blueprint_key)
    version = _bounded_int(
        request.args.get("version"),
        default=0,
        minimum=0,
        maximum=2**31 - 1,
    )
    resolved = organization_compile_service().get_blueprint(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        key=key,
        version=version or None,
        include_inactive=bool(version),
    )
    if resolved is None:
        raise OrganizationRouteError("organization_blueprint_not_found", status_code=404)
    definition, lifecycle = resolved
    return api_response(
        data={
            **definition.model_dump(mode="json"),
            "revision": canonical_definition_sha256(definition),
            "lifecycle": lifecycle,
            "project_id": project.project_id,
            "test_only": False,
        }
    )


@organization_blueprints_bp.post("/api/organization-blueprints/validate")
@organization_blueprints_bp.post("/api/organization-blueprints/<path:blueprint_key>/validate")
@check_auth
@organization_boundary
def validate_organization_blueprint(blueprint_key: str | None = None):
    payload = request_payload(
        allowed_fields={
            "definition",
            "lifecycle",
            "expected_parent_revision",
            "project_id",
            "projectId",
        }
    )
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    result = organization_definition_service().validate(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        definition_payload=_definition_payload(payload, path_key=blueprint_key),
        lifecycle=str(payload.get("lifecycle") or "draft"),
        expected_parent_revision=_optional_revision(payload.get("expected_parent_revision")),
    )
    grant = _issue_preview_grant(
        result=result,
        principal_id=principal.subject_id,
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        idempotency_key=require_idempotency_key(),
    )
    return api_response(data={**result, "admin_grant": grant})


@organization_blueprints_bp.post("/api/organization-blueprints")
@check_auth
@organization_boundary
def create_organization_blueprint():
    payload = request_payload(
        allowed_fields={
            "definition",
            "lifecycle",
            "mutation_digest",
            "admin_grant",
            "project_id",
            "projectId",
        }
    )
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    if require_if_match_header().lower() not in {"none", "null"}:
        raise OrganizationRouteError(
            "organization_definition_parent_revision_stale",
            status_code=412,
        )
    result = organization_definition_service().create_revision(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        definition_payload=_definition_payload(payload),
        lifecycle=str(payload.get("lifecycle") or "draft"),
        expected_parent_revision=None,
        mutation_digest=_required_digest(payload.get("mutation_digest")),
        grant_id=require_admin_grant_header(body_value=_admin_grant_body_value(payload.get("admin_grant"))),
        idempotency_key=require_idempotency_key(),
    )
    return api_response(data=result, code=200 if result["replayed"] else 201)


@organization_blueprints_bp.patch("/api/organization-blueprints/<path:blueprint_key>")
@organization_blueprints_bp.post("/api/organization-blueprints/<path:blueprint_key>/revisions")
@check_auth
@organization_boundary
def create_organization_blueprint_revision(blueprint_key: str):
    payload = request_payload(
        allowed_fields={
            "definition",
            "lifecycle",
            "mutation_digest",
            "admin_grant",
            "project_id",
            "projectId",
        }
    )
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    result = organization_definition_service().create_revision(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        definition_payload=_definition_payload(payload, path_key=blueprint_key),
        lifecycle=str(payload.get("lifecycle") or "draft"),
        expected_parent_revision=_required_revision_header(),
        mutation_digest=_required_digest(payload.get("mutation_digest")),
        grant_id=require_admin_grant_header(body_value=_admin_grant_body_value(payload.get("admin_grant"))),
        idempotency_key=require_idempotency_key(),
    )
    return api_response(data=result, code=200 if result["replayed"] else 201)


@organization_blueprints_bp.post("/api/organization-blueprints/<path:blueprint_key>/archive-preview")
@check_auth
@organization_boundary
def preview_organization_blueprint_archive(blueprint_key: str):
    payload = request_payload(allowed_fields={"version", "project_id", "projectId"})
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    result = organization_definition_service().preview_archive(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        key=_plain_blueprint_key(blueprint_key),
        version=_bounded_int(payload.get("version"), default=0, minimum=1, maximum=2**31 - 1),
    )
    grant = _issue_preview_grant(
        result=result,
        principal_id=principal.subject_id,
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        idempotency_key=require_idempotency_key(),
    )
    return api_response(data={**result, "admin_grant": grant})


@organization_blueprints_bp.post("/api/organization-blueprints/<path:blueprint_key>/archive")
@check_auth
@organization_boundary
def archive_organization_blueprint(blueprint_key: str):
    payload = request_payload(
        allowed_fields={
            "version",
            "mutation_digest",
            "admin_grant",
            "project_id",
            "projectId",
        }
    )
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    result = organization_definition_service().archive_revision(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        key=_plain_blueprint_key(blueprint_key),
        version=_bounded_int(payload.get("version"), default=0, minimum=1, maximum=2**31 - 1),
        expected_revision=_required_revision_header(),
        mutation_digest=_required_digest(payload.get("mutation_digest")),
        grant_id=require_admin_grant_header(body_value=_admin_grant_body_value(payload.get("admin_grant"))),
        idempotency_key=require_idempotency_key(),
    )
    return api_response(data=result)


@organization_blueprints_bp.post("/api/organization-blueprints/<path:blueprint_key>/reconcile-preview")
@check_auth
@organization_boundary
def preview_organization_blueprint_reconcile(blueprint_key: str):
    payload = request_payload(
        allowed_fields={
            "current_version",
            "source",
            "desired_definition",
            "local_override_paths",
            "project_id",
            "projectId",
        }
    )
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    key = _plain_blueprint_key(blueprint_key)
    current_version = _bounded_int(
        payload.get("current_version"),
        default=0,
        minimum=1,
        maximum=2**31 - 1,
    )
    overrides = _override_paths(payload.get("local_override_paths"))
    source = str(payload.get("source") or "").strip().lower()
    if not source:
        source = "payload" if payload.get("desired_definition") is not None else "seed"
    if source == "seed":
        if payload.get("desired_definition") is not None:
            raise OrganizationRouteError("organization_reconcile_source_conflict", status_code=400)
        result = organization_definition_service().preview_seed_reconcile(
            tenant_id=project.tenant_id,
            project_id=project.project_id,
            key=key,
            current_version=current_version,
            local_override_paths=overrides,
        )
    elif source == "payload":
        result = organization_definition_service().preview_reconcile(
            tenant_id=project.tenant_id,
            project_id=project.project_id,
            key=key,
            current_version=current_version,
            desired_definition=_definition_payload(
                payload,
                path_key=key,
                field="desired_definition",
            ),
            local_override_paths=overrides,
        )
        result["reconcile_source"] = "payload"
    else:
        raise OrganizationRouteError("organization_reconcile_source_invalid", status_code=400)
    grant = _issue_preview_grant(
        result=result,
        principal_id=principal.subject_id,
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        idempotency_key=require_idempotency_key(),
        requires_apply=bool(result.get("requires_apply")),
    )
    return api_response(data={**result, "admin_grant": grant})


@organization_blueprints_bp.post("/api/organization-blueprints/<path:blueprint_key>/reconcile-apply")
@check_auth
@organization_boundary
def apply_organization_blueprint_reconcile(blueprint_key: str):
    payload = request_payload(allowed_fields={"preview", "admin_grant", "project_id", "projectId"})
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    key = _plain_blueprint_key(blueprint_key)
    preview = payload.get("preview")
    if not isinstance(preview, Mapping) or str(preview.get("definition_key") or "") != key:
        raise OrganizationRouteError("organization_reconcile_preview_invalid", status_code=422)
    result = organization_definition_service().apply_reconcile(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        preview=dict(preview),
        expected_revision=_required_revision_header(),
        grant_id=require_admin_grant_header(body_value=_admin_grant_body_value(payload.get("admin_grant"))),
        idempotency_key=require_idempotency_key(),
    )
    return api_response(data=result, code=200 if result["replayed"] else 201)


@organization_blueprints_bp.post("/api/organization-blueprints/<path:blueprint_key>/admission-exceptions")
@check_auth
@organization_boundary
def issue_organization_admission_exception(blueprint_key: str):
    payload = request_payload(
        allowed_fields={
            "blueprint_version",
            "team_blueprint_counts",
            "reason",
            "ttl_seconds",
            "project_id",
            "projectId",
        }
    )
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    definition_key, _selector_count = OrganizationCompileApplicationService.parse_selector(blueprint_key)
    raw_counts = payload.get("team_blueprint_counts")
    if not isinstance(raw_counts, Mapping):
        raise OrganizationRouteError(
            "organization_custom_composition_invalid",
            status_code=422,
        )
    counts: dict[str, int] = {}
    for raw_key, raw_value in raw_counts.items():
        key = str(raw_key or "").strip()
        if not key or isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise OrganizationRouteError(
                "organization_custom_composition_invalid",
                status_code=422,
            )
        counts[key] = raw_value
    version = _bounded_int(
        payload.get("blueprint_version"),
        default=0,
        minimum=0,
        maximum=2**31 - 1,
    )
    ttl_seconds = _bounded_int(
        payload.get("ttl_seconds"),
        default=900,
        minimum=60,
        maximum=3600,
    )
    result = organization_admission_exception_service().issue(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        definition_key=definition_key,
        definition_version=version or None,
        composition=counts,
        reason=str(payload.get("reason") or ""),
        idempotency_key=require_idempotency_key(),
        ttl_seconds=ttl_seconds,
    )
    return api_response(data=result, code=200 if result["replayed"] else 201)


@organization_blueprints_bp.post("/api/organization-blueprints/<path:blueprint_key>/precreation-admin-grants")
@check_auth
@organization_boundary
def issue_organization_precreation_admin_grant(blueprint_key: str):
    """Issue a short-lived grant bound to a server-recompiled plan."""

    payload = request_payload(
        allowed_fields={
            "compile_plan",
            "ttl_seconds",
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
        raise OrganizationRouteError(
            "organization_compile_plan_required",
            status_code=400,
        )
    plan, bound = organization_compile_service().recompile_bound_plan(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        client_plan=client_plan,
    )
    path_key, _path_count = OrganizationCompileApplicationService.parse_selector(blueprint_key)
    if VersionedDefinitionRef.parse(plan.definition_ref).key != path_key:
        raise OrganizationRouteError(
            "organization_blueprint_selector_mismatch",
            status_code=400,
        )
    require_if_match(plan.definition_revision)
    ttl_seconds = _bounded_int(
        payload.get("ttl_seconds"),
        default=900,
        minimum=60,
        maximum=3600,
    )
    result = project_plan_grant_service().issue(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        plan_digest=plan.plan_digest,
        policy_hash=str(bound.get("admin_policy_hash") or ""),
        grant_kind="instantiate",
        granted_by=f"hub:{principal.subject_id}",
        idempotency_key=require_idempotency_key(),
        ttl_seconds=ttl_seconds,
    )
    return api_response(data=result, code=200 if result["replayed"] else 201)


@organization_blueprints_bp.post("/api/organization-blueprints/<path:blueprint_key>/compile")
@check_auth
@organization_boundary
def compile_organization_blueprint(blueprint_key: str):
    payload = request_payload(
        allowed_fields={
            "blueprint_key",
            "blueprint_version",
            "title",
            "team_count",
            "custom_team_blueprint_keys",
            "admission_exception_ref",
            "parameters",
            "project_id",
            "projectId",
        }
    )
    principal, project = require_project_scope(
        ProjectCapability.READ,
        payload_project_id=payload.get("project_id") or payload.get("projectId"),
    )
    _plan, response = organization_compile_service().compile(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        payload=payload,
        path_blueprint_key=blueprint_key,
    )
    return api_response(data=response)


def _issue_preview_grant(
    *,
    result: Mapping[str, object],
    principal_id: str,
    tenant_id: str,
    project_id: str,
    idempotency_key: str,
    requires_apply: bool = True,
) -> dict | None:
    if not bool(result.get("applicable", True)) or not requires_apply:
        return None
    return project_plan_grant_service().issue(
        tenant_id=tenant_id,
        project_id=project_id,
        principal_id=principal_id,
        plan_digest=_required_digest(result.get("mutation_digest") or result.get("plan_digest")),
        policy_hash=_required_digest(result.get("policy_hash")),
        grant_kind=str(result.get("grant_kind") or ""),
        granted_by=f"hub:{principal_id}",
        idempotency_key=idempotency_key,
        ttl_seconds=900,
    )


def _definition_payload(
    payload: Mapping[str, object],
    *,
    path_key: str | None = None,
    field: str = "definition",
) -> dict:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise OrganizationRouteError("organization_definition_payload_invalid", status_code=422)
    definition = dict(value)
    if path_key is not None and str(definition.get("key") or "") != _plain_blueprint_key(path_key):
        raise OrganizationRouteError("organization_definition_key_mismatch", status_code=422)
    return definition


def _admin_grant_body_value(raw: object) -> object | None:
    if isinstance(raw, Mapping):
        return raw.get("grant_id")
    return raw


def _plain_blueprint_key(value: str) -> str:
    key, count = OrganizationCompileApplicationService.parse_selector(value)
    if count is not None:
        raise OrganizationRouteError("organization_blueprint_selector_invalid", status_code=400)
    return key


def _required_digest(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if _SHA256.fullmatch(value) is None:
        raise OrganizationRouteError("organization_definition_digest_invalid", status_code=422)
    return value


def _optional_revision(raw: object) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if _SHA256.fullmatch(value) is None:
        raise OrganizationRouteError("organization_definition_revision_invalid", status_code=422)
    return value


def _required_revision_header() -> str:
    value = require_if_match_header().lower()
    if _SHA256.fullmatch(value) is None:
        raise OrganizationRouteError("organization_definition_if_match_invalid", status_code=400)
    return value


def _override_paths(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > 128:
        raise OrganizationRouteError("organization_local_override_paths_invalid", status_code=422)
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise OrganizationRouteError("organization_local_override_paths_invalid", status_code=422)
        values.append(item)
    return tuple(values)


def _bounded_int(raw, *, default: int, minimum: int, maximum: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise OrganizationRouteError("organization_integer_parameter_invalid", status_code=400) from exc
    if not minimum <= value <= maximum:
        raise OrganizationRouteError("organization_integer_parameter_invalid", status_code=400)
    return value


def _cursor_secret() -> bytes:
    secret = str(current_app.config.get("SECRET_KEY") or "").encode("utf-8")
    if not secret:
        raise OrganizationRouteError("organization_cursor_secret_missing", status_code=500)
    return secret


def _encode_cursor(offset: int, scope: Mapping[str, str]) -> str:
    payload = json.dumps(
        {"v": 1, "scope": dict(scope), "offset": offset},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(_cursor_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")


def _decode_cursor(cursor: str | None, scope: Mapping[str, str]) -> int:
    if not cursor:
        return 0
    try:
        raw = str(cursor)
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(decoded) <= hashlib.sha256().digest_size:
            raise ValueError
        payload, signature = decoded[:-32], decoded[-32:]
        expected = hmac.new(_cursor_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        value = json.loads(payload)
        if (
            not isinstance(value, dict)
            or value.get("v") != 1
            or value.get("scope") != dict(scope)
            or isinstance(value.get("offset"), bool)
            or not isinstance(value.get("offset"), int)
            or value["offset"] < 0
        ):
            raise ValueError
        return value["offset"]
    except (
        ValueError,
        TypeError,
        UnicodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise OrganizationRouteError("organization_cursor_invalid", status_code=400) from exc


__all__ = ["organization_blueprints_bp"]
