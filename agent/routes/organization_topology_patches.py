"""Preview-first HTTP boundary for typed Organization topology patches."""

from __future__ import annotations

import json
from collections.abc import Mapping

from flask import Blueprint, request
from pydantic import ValidationError
from sqlmodel import Session
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.database import engine
from agent.repositories.organizations.adapters import SqlOrganizationLimitProfileAdapter
from agent.repositories.organizations.definitions import SqlOrganizationDefinitionRepository
from agent.repositories.organizations.topology import SqlOrganizationTopologyReadRepository
from agent.routes.organization_route_support import (
    OrganizationRouteError,
    organization_boundary,
    organization_catalog,
    organization_uow_factory,
    require_admin_grant_header,
    require_idempotency_key,
    require_if_match,
    require_organization_scope,
)
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
)
from agent.services.organization_projection_service import OrganizationProjectionService
from agent.services.organization_topology_apply_service import (
    OrganizationTopologyApplyService,
    OrganizationTopologyPatchDocument,
    OrganizationTopologyPatchError,
    OrganizationTopologyPatchPreview,
    SqlOrganizationPatchReadAdapter,
)
from agent.services.project_access_authority import ProjectCapability

organization_topology_patches_bp = Blueprint("organization_topology_patches", __name__, url_prefix="/api/organizations")

_PATCH_BODY_LIMIT = 512 * 1024


@organization_topology_patches_bp.post("/<organization_id>/patches/preview")
@check_auth
@organization_boundary
def preview_organization_topology_patch(organization_id: str):
    payload = _bounded_json(_PATCH_BODY_LIMIT)
    scope = require_organization_scope(organization_id, ProjectCapability.WRITE)
    try:
        document = OrganizationTopologyPatchDocument.model_validate(payload)
    except ValidationError:
        raise
    require_if_match(document.expected_revision)

    with Session(engine) as session:
        service = _service(session)
        try:
            preview = service.preview(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                organization_id=scope.organization_id,
                principal_id=scope.principal.subject_id,
                document=document,
            )
        except OrganizationTopologyPatchError as exc:
            raise OrganizationRouteError(exc.reason_code, status_code=exc.public_status) from exc
    return api_response(data=preview.model_dump(mode="json"))


@organization_topology_patches_bp.post("/<organization_id>/patches/apply")
@check_auth
@organization_boundary
def apply_organization_topology_patch(organization_id: str):
    payload = _bounded_json(_PATCH_BODY_LIMIT)
    scope = require_organization_scope(organization_id, ProjectCapability.MANAGE)
    preview = OrganizationTopologyPatchPreview.model_validate(payload)
    expected_revision = require_if_match(preview.expected_revision)
    expected_digest = _require_preview_digest_headers(preview)
    idempotency_key = require_idempotency_key()
    topology_patch_grant_id = str(request.headers.get("X-Topology-Patch-Grant") or "").strip()
    if not topology_patch_grant_id:
        raise OrganizationRouteError(
            "organization_topology_patch_grant_required",
            status_code=403,
        )

    with Session(engine) as session:
        service = _service(session)
        try:
            service.apply(
                preview=preview,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                organization_id=scope.organization_id,
                principal_id=scope.principal.subject_id,
                expected_revision=expected_revision,
                expected_patch_digest=expected_digest,
                idempotency_key=idempotency_key,
                topology_patch_grant_id=topology_patch_grant_id,
            )
        except OrganizationTopologyPatchError as exc:
            raise OrganizationRouteError(exc.reason_code, status_code=exc.public_status) from exc

        catalog = organization_catalog()
        definitions = FileCatalogDefinitionRepositoryAdapter(
            SqlOrganizationDefinitionRepository(session),
            catalog,
            session,
        )
        limit_profiles = SqlOrganizationLimitProfileAdapter(definitions)
        organization = scope.organization
        limit_ref = str(organization.effective_limit_profile_ref)
        if "@" not in limit_ref:
            limit_ref = f"{limit_ref}@{organization.effective_limit_profile_revision}"
        limits = limit_profiles.resolve_limit_profile(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            policy_ref=limit_ref,
        )
        page = OrganizationProjectionService(topology_reader=SqlOrganizationTopologyReadRepository(session)).project(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            organization_id=scope.organization_id,
            limits=limits,
            include_runtime_overlay=True,
        )
    return api_response(data=page)


@organization_topology_patches_bp.post("/<organization_id>/patches/grants")
@check_auth
@organization_boundary
def issue_organization_topology_patch_grant(organization_id: str):
    payload = _bounded_json(_PATCH_BODY_LIMIT)
    scope = require_organization_scope(organization_id, ProjectCapability.MANAGE)
    preview = OrganizationTopologyPatchPreview.model_validate(payload)
    expected_revision = require_if_match(preview.expected_revision)
    expected_digest = _require_preview_digest_headers(preview)
    issue_idempotency_key = require_idempotency_key()
    parent_admin_grant_id = require_admin_grant_header()

    with Session(engine) as session:
        service = _service(session)
        try:
            grant = service.issue_grant(
                preview=preview,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                organization_id=scope.organization_id,
                principal_id=scope.principal.subject_id,
                expected_revision=expected_revision,
                expected_patch_digest=expected_digest,
                issue_idempotency_key=issue_idempotency_key,
                parent_admin_grant_id=parent_admin_grant_id,
            )
        except OrganizationTopologyPatchError as exc:
            raise OrganizationRouteError(
                exc.reason_code,
                status_code=exc.public_status,
            ) from exc
    return api_response(
        data=grant.model_dump(mode="json"),
        code=200 if grant.replayed else 201,
    )


def _require_preview_digest_headers(
    preview: OrganizationTopologyPatchPreview,
) -> str:
    expected_digest = str(request.headers.get("X-Patch-Digest") or "").strip()
    if not expected_digest:
        raise OrganizationRouteError(
            "organization_patch_digest_required",
            status_code=428,
        )
    limit_digest = str(request.headers.get("X-Limit-Digest") or "").strip()
    if not limit_digest:
        raise OrganizationRouteError(
            "organization_patch_limit_digest_required",
            status_code=428,
        )
    if limit_digest != preview.effective_limit_profile_hash:
        raise OrganizationRouteError(
            "organization_patch_limit_digest_mismatch",
            status_code=412,
        )
    policy_digest = str(request.headers.get("X-Policy-Digest") or "").strip()
    if not policy_digest:
        raise OrganizationRouteError(
            "organization_patch_policy_digest_required",
            status_code=428,
        )
    if policy_digest != preview.effective_policy_hash:
        raise OrganizationRouteError(
            "organization_patch_policy_digest_mismatch",
            status_code=412,
        )
    return expected_digest


def _service(session: Session) -> OrganizationTopologyApplyService:
    catalog = organization_catalog()
    definitions = FileCatalogDefinitionRepositoryAdapter(
        SqlOrganizationDefinitionRepository(session),
        catalog,
        session,
    )
    return OrganizationTopologyApplyService(
        reader=SqlOrganizationPatchReadAdapter(catalog=catalog),
        limit_profiles=SqlOrganizationLimitProfileAdapter(definitions),
        uow_factory=organization_uow_factory(),
        catalog=catalog,
    )


def _bounded_json(maximum_bytes: int) -> dict:
    content_length = request.content_length
    if content_length is not None and content_length > maximum_bytes:
        raise OrganizationRouteError("organization_patch_payload_too_large", status_code=413)
    request.max_content_length = maximum_bytes
    try:
        raw = request.get_data(cache=True, as_text=False)
    except RequestEntityTooLarge as exc:
        raise OrganizationRouteError("organization_patch_payload_too_large", status_code=413) from exc
    if len(raw) > maximum_bytes:
        raise OrganizationRouteError("organization_patch_payload_too_large", status_code=413)
    if not request.is_json:
        raise OrganizationRouteError("organization_patch_json_required", status_code=415)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrganizationRouteError("organization_patch_json_invalid", status_code=400) from exc
    if not isinstance(value, Mapping):
        raise OrganizationRouteError("organization_patch_payload_invalid", status_code=400)
    return dict(value)


__all__ = ["organization_topology_patches_bp"]
