"""Scoped export and preview-first import routes for Organization Bundle v2."""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Mapping
from typing import Any, Literal

from flask import Blueprint, current_app, request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models.organizations import OrganizationInstanceDB
from agent.models.organization_models import OrganizationBundleImportPlan
from agent.models.team_models import OrganizationBlueprintBundleV2, TeamBlueprintBundle
from agent.repositories.organizations.adapters import SqlOrganizationLimitProfileAdapter
from agent.repositories.organizations.definitions import SqlOrganizationDefinitionRepository
from agent.routes.organization_route_support import (
    OrganizationRouteError,
    organization_boundary,
    organization_catalog,
    organization_uow_factory,
    project_plan_grant_service,
    require_admin_grant_header,
    require_idempotency_key,
    require_if_match,
    require_organization_scope,
    require_project_scope,
)
from agent.services.organization_admission_exception_service import (
    SqlOrganizationAdmissionPolicy,
)
from agent.services.organization_bundle_apply_service import (
    OrganizationBundleApplyError,
    OrganizationBundleApplyService,
    organization_bundle_target_revision,
)
from agent.services.organization_bundle_export_service import (
    OrganizationBundleExportError,
    OrganizationBundleExportService,
)
from agent.services.organization_bundle_migration_service import (
    OrganizationBundleMigrationError,
    OrganizationBundleMigrationService,
)
from agent.services.organization_bundle_service import OrganizationBundlePlanner
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
)
from agent.services.organization_template_security_service import (
    installed_template_appendix_refs,
)
from agent.services.project_access_authority import ProjectCapability

organization_bundles_bp = Blueprint("organization_bundles", __name__, url_prefix="/api/organization-bundles")

_BUNDLE_BODY_LIMIT = 12 * 1024 * 1024


class _BundleTargetRebindContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available: bool = False
    scope_binding: Literal["authenticated_target_project"] = "authenticated_target_project"
    root_definition_ref: str | None = None
    compile_endpoint_template: Literal["/api/organization-blueprints/{blueprint_key}/compile"] = (
        "/api/organization-blueprints/{blueprint_key}/compile"
    )
    instantiate_endpoint: Literal["/api/organizations"] = "/api/organizations"
    id_allocation: Literal["target_hub"] = "target_hub"
    assignment_binding: Literal["explicit_target_local_rebind"] = "explicit_target_local_rebind"


class _BundlePreviewEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_digest: str
    expires_at: str
    source_version: str
    target_version: str = "2.0"
    project_id: str
    conflict_strategy: Literal["fail", "skip", "overwrite"]
    redacted_fields: list[str]
    omitted_fields: list[str] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]]
    changes: dict[str, list[dict[str, Any]]]
    applicable: bool
    instance_import_mode: Literal["optional_target_recompile"] = "optional_target_recompile"
    target_rebind_contract: _BundleTargetRebindContract = Field(default_factory=_BundleTargetRebindContract)
    migration_warnings: list[str] = Field(default_factory=list)
    bundle: OrganizationBlueprintBundleV2
    import_plan: OrganizationBundleImportPlan


@organization_bundles_bp.get("/export")
@check_auth
@organization_boundary
def export_organization_bundle():
    organization_id = str(request.args.get("organization_id") or "").strip()
    if not organization_id:
        raise OrganizationRouteError("organization_id_required", status_code=400)
    include_instances = _query_bool("include_instances", default=False)
    include_assignments = _query_bool("include_assignments", default=False)
    scope = require_organization_scope(organization_id, ProjectCapability.READ)
    with Session(engine) as session:
        try:
            bundle = OrganizationBundleExportService(catalog=organization_catalog()).export(
                session=session,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                organization_id=scope.organization_id,
                include_instances=include_instances,
                include_assignments=include_assignments,
            )
        except OrganizationBundleExportError as exc:
            raise OrganizationRouteError(exc.reason_code, status_code=exc.public_status) from exc
    return api_response(data=bundle.model_dump(mode="json"))


@organization_bundles_bp.post("/import-preview")
@check_auth
@organization_boundary
def preview_organization_bundle_import():
    payload = _bounded_json(_BUNDLE_BODY_LIMIT)
    unknown = sorted(
        set(payload)
        - {
            "bundle",
            "conflict_strategy",
            "project_id",
            "migrate_v1",
            "assignment_rebindings",
            "instance_admission_exception_refs",
        }
    )
    if unknown:
        raise OrganizationRouteError(
            "organization_bundle_preview_fields_invalid",
            status_code=400,
            details={"unknown_fields": unknown},
        )
    raw_bundle = payload.get("bundle")
    if not isinstance(raw_bundle, Mapping):
        raise OrganizationRouteError("organization_bundle_payload_invalid", status_code=400)
    source_version = str(raw_bundle.get("schema_version") or "")
    migration_warnings: list[str] = []
    if source_version == "1.0":
        if payload.get("migrate_v1") is not True:
            raise OrganizationRouteError("organization_bundle_v1_migration_must_be_explicit", status_code=422)
        try:
            migrated = OrganizationBundleMigrationService().migrate_v1_team_slice(
                TeamBlueprintBundle.model_validate(raw_bundle)
            )
        except OrganizationBundleMigrationError as exc:
            raise OrganizationRouteError(str(exc), status_code=422) from exc
        bundle = migrated.bundle
        migration_warnings = migrated.warnings
    else:
        bundle = OrganizationBlueprintBundleV2.model_validate(raw_bundle)
        source_version = "2.0"

    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=payload.get("project_id"),
    )
    conflict_strategy = str(payload.get("conflict_strategy") or "fail")
    if conflict_strategy not in {"fail", "skip", "overwrite"}:
        raise OrganizationRouteError(
            "organization_bundle_conflict_strategy_invalid",
            status_code=400,
        )
    with Session(engine) as session:
        assignment_rebindings = payload.get("assignment_rebindings", {})
        admission_exception_refs = payload.get("instance_admission_exception_refs", {})
        if not isinstance(assignment_rebindings, Mapping) or not isinstance(
            admission_exception_refs,
            Mapping,
        ):
            raise OrganizationRouteError(
                "organization_bundle_rebindings_invalid",
                status_code=400,
            )
        plan = _build_import_plan(
            session=session,
            bundle=bundle,
            conflict_strategy=conflict_strategy,
            tenant_id=project.tenant_id,
            project_id=project.project_id,
            principal_id=principal.subject_id,
            assignment_rebindings={str(key): str(value) for key, value in assignment_rebindings.items()},
            instance_admission_exception_refs={str(key): str(value) for key, value in admission_exception_refs.items()},
        )
    envelope = _preview_envelope(
        bundle=bundle,
        plan=plan,
        source_version=source_version,
        migration_warnings=migration_warnings,
    )
    return api_response(data=envelope.model_dump(mode="json"))


@organization_bundles_bp.post("/import-grants")
@check_auth
@organization_boundary
def issue_organization_bundle_import_grant():
    preview = _BundlePreviewEnvelope.model_validate(_bounded_json(_BUNDLE_BODY_LIMIT))
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=preview.project_id,
    )
    supplied_digest = str(request.headers.get("X-Import-Plan-Digest") or "").strip()
    if not _preview_binding_is_plausible(
        preview=preview,
        supplied_digest=supplied_digest,
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
    ):
        raise OrganizationRouteError(
            "organization_bundle_grant_binding_invalid",
            status_code=412,
        )
    require_if_match(preview.import_plan.expected_target_revision)
    with Session(engine) as session:
        recomputed = _build_import_plan(
            session,
            bundle=preview.bundle,
            conflict_strategy=preview.conflict_strategy,
            tenant_id=project.tenant_id,
            project_id=project.project_id,
            principal_id=principal.subject_id,
            assignment_rebindings=preview.import_plan.assignment_rebindings,
            instance_admission_exception_refs=(preview.import_plan.instance_admission_exception_refs),
            clock=lambda: preview.import_plan.expires_at_epoch - 300.0,
        )
    if recomputed != preview.import_plan or recomputed.errors:
        raise OrganizationRouteError(
            "organization_bundle_preview_not_authoritative",
            status_code=412,
        )
    result = project_plan_grant_service().issue(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        principal_id=principal.subject_id,
        plan_digest=preview.plan_digest,
        policy_hash=preview.import_plan.effective_limit_profile_hash,
        grant_kind="bundle_import",
        granted_by=f"hub:{principal.subject_id}",
        idempotency_key=require_idempotency_key(),
        ttl_seconds=900,
    )
    return api_response(data=result, code=200 if result["replayed"] else 201)


@organization_bundles_bp.post("/import-apply")
@check_auth
@organization_boundary
def apply_organization_bundle_import():
    payload = _bounded_json(_BUNDLE_BODY_LIMIT)
    preview = _BundlePreviewEnvelope.model_validate(payload)
    principal, project = require_project_scope(
        ProjectCapability.MANAGE,
        payload_project_id=preview.project_id,
    )
    expected_digest = str(request.headers.get("X-Import-Plan-Digest") or "").strip()
    if not expected_digest:
        raise OrganizationRouteError("organization_bundle_plan_digest_required", status_code=428)
    if expected_digest != preview.plan_digest or expected_digest != preview.import_plan.plan_digest:
        raise OrganizationRouteError("organization_bundle_plan_digest_mismatch", status_code=412)
    if preview.expires_at != preview.import_plan.expires_at:
        raise OrganizationRouteError("organization_bundle_plan_expiry_mismatch", status_code=412)
    limit_digest = str(request.headers.get("X-Limit-Digest") or "").strip()
    if limit_digest and limit_digest != preview.import_plan.effective_limit_profile_hash:
        raise OrganizationRouteError("organization_bundle_limit_digest_mismatch", status_code=412)
    require_if_match(preview.import_plan.expected_target_revision)
    idempotency_key = require_idempotency_key()
    grant_id = require_admin_grant_header()

    with Session(engine) as session:
        catalog = organization_catalog()
        definitions = FileCatalogDefinitionRepositoryAdapter(
            SqlOrganizationDefinitionRepository(session),
            catalog,
            session,
        )
        apply_service = OrganizationBundleApplyService(
            limit_profiles=SqlOrganizationLimitProfileAdapter(definitions),
            uow_factory=organization_uow_factory(),
            catalog=catalog,
        )
        try:
            result = apply_service.apply(
                bundle=preview.bundle,
                plan=preview.import_plan,
                idempotency_key=idempotency_key,
                current_target_revision=preview.import_plan.expected_target_revision,
                tenant_id=project.tenant_id,
                project_id=project.project_id,
                principal_id=principal.subject_id,
                admin_grant_id=grant_id,
            )
        except OrganizationBundleApplyError as exc:
            raise OrganizationRouteError(exc.reason_code, status_code=_bundle_apply_status(exc.reason_code)) from exc
    imported = Counter(item.section for item in preview.import_plan.items if item.action in {"create", "update"})
    return api_response(
        data={
            "imported": dict(sorted(imported.items())),
            "replayed": bool(result.get("idempotent_replay")),
        }
    )


def _preview_envelope(*, bundle, plan, source_version, migration_warnings):
    changes: dict[str, list[dict[str, Any]]] = {}
    for item in plan.items:
        changes.setdefault(item.section, []).append(
            {
                "key": f"{item.key}@{item.version}",
                "action": item.action,
                **({"detail": ", ".join(item.changes)} if item.changes else {}),
            }
        )
    for values in changes.values():
        values.sort(key=lambda value: value["key"])
    diagnostics = [
        {
            "severity": item.severity,
            "reason_code": item.reason_code,
            "message": item.human_message,
        }
        for item in plan.errors
    ]
    applicable = not plan.errors and not any(item.action == "conflict" for item in plan.items)
    return _BundlePreviewEnvelope(
        plan_digest=plan.plan_digest,
        expires_at=plan.expires_at,
        source_version=source_version,
        project_id=plan.project_id,
        conflict_strategy=plan.conflict_strategy,
        redacted_fields=(
            ["assignments[].agent_url -> assignments[].principal_ref"] if bundle.include_assignments else []
        ),
        omitted_fields=[
            "source_tenant_id",
            "source_project_id",
            "source_organization_id",
            "local_database_ids",
            "compiled_plan",
            "agent_urls",
            "credentials",
        ],
        diagnostics=diagnostics,
        changes=dict(sorted(changes.items())),
        applicable=applicable,
        target_rebind_contract=_target_rebind_contract(bundle),
        migration_warnings=migration_warnings,
        bundle=bundle,
        import_plan=plan,
    )


def _target_rebind_contract(bundle: OrganizationBlueprintBundleV2) -> _BundleTargetRebindContract:
    root_ref = str((bundle.bundle_metadata or {}).get("root_definition_ref") or "").strip()
    available_refs = {f"{item.key}@{item.version}" for item in bundle.organization_blueprints}
    return _BundleTargetRebindContract(
        available=bool(bundle.organization_instances) or root_ref in available_refs,
        root_definition_ref=root_ref if root_ref in available_refs else None,
    )


def _build_import_plan(
    session: Session,
    *,
    bundle: OrganizationBlueprintBundleV2,
    conflict_strategy: str,
    tenant_id: str,
    project_id: str,
    principal_id: str,
    assignment_rebindings: Mapping[str, str],
    instance_admission_exception_refs: Mapping[str, str],
    clock=None,
) -> OrganizationBundleImportPlan:
    catalog = organization_catalog()
    definitions = FileCatalogDefinitionRepositoryAdapter(
        SqlOrganizationDefinitionRepository(session),
        catalog,
        session,
    )
    limits = SqlOrganizationLimitProfileAdapter(definitions).resolve_limit_profile(
        tenant_id=tenant_id,
        project_id=project_id,
        policy_ref=_preview_limit_ref(),
    )
    target_revision = organization_bundle_target_revision(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        catalog=catalog,
    )
    planner_arguments: dict[str, Any] = {}
    if clock is not None:
        planner_arguments["clock"] = clock
    return OrganizationBundlePlanner(
        definitions=definitions,
        admission_policy=SqlOrganizationAdmissionPolicy(),
        allowed_template_appendix_refs=installed_template_appendix_refs(catalog),
        instance_exists=lambda candidate_tenant, candidate_project, organization_id: (
            session.exec(
                select(OrganizationInstanceDB)
                .where(OrganizationInstanceDB.tenant_id == candidate_tenant)
                .where(OrganizationInstanceDB.project_id == candidate_project)
                .where(OrganizationInstanceDB.organization_id == organization_id)
            ).first()
            is not None
        ),
        **planner_arguments,
    ).plan(
        bundle=bundle,
        conflict_strategy=conflict_strategy,
        tenant_id=tenant_id,
        project_id=project_id,
        principal_id=principal_id,
        expected_target_revision=target_revision,
        effective_limits=limits,
        allowed_source_refs=[],
        allowed_run_refs=[],
        assignment_rebindings=assignment_rebindings,
        instance_admission_exception_refs=instance_admission_exception_refs,
    )


def _preview_binding_is_plausible(
    *,
    preview: _BundlePreviewEnvelope,
    supplied_digest: str,
    tenant_id: str,
    project_id: str,
    principal_id: str,
) -> bool:
    plan = preview.import_plan
    return bool(
        preview.plan_digest == plan.plan_digest
        and supplied_digest == preview.plan_digest
        and preview.project_id == project_id
        and preview.conflict_strategy == plan.conflict_strategy
        and plan.principal_id == principal_id
        and plan.tenant_id == tenant_id
        and plan.project_id == project_id
        and preview.applicable
        and not plan.errors
        and plan.expires_at_epoch >= time.time()
    )


def _preview_limit_ref() -> str:
    return str(current_app.config.get("ORGANIZATION_LIMIT_PROFILE_REF") or "organization_limits@1").strip()


def _bounded_json(maximum_bytes: int) -> dict:
    if request.content_length is not None and request.content_length > maximum_bytes:
        raise OrganizationRouteError("organization_bundle_payload_too_large", status_code=413)
    request.max_content_length = maximum_bytes
    try:
        raw = request.get_data(cache=True, as_text=False)
    except RequestEntityTooLarge as exc:
        raise OrganizationRouteError("organization_bundle_payload_too_large", status_code=413) from exc
    if len(raw) > maximum_bytes:
        raise OrganizationRouteError("organization_bundle_payload_too_large", status_code=413)
    if not request.is_json:
        raise OrganizationRouteError("organization_bundle_json_required", status_code=415)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrganizationRouteError("organization_bundle_json_invalid", status_code=400) from exc
    if not isinstance(value, Mapping):
        raise OrganizationRouteError("organization_bundle_payload_invalid", status_code=400)
    return dict(value)


def _query_bool(name: str, *, default: bool) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    raise OrganizationRouteError(f"{name}_invalid", status_code=400)


def _bundle_apply_status(reason_code: str) -> int:
    value = reason_code.lower()
    if "scope" in value or "grant" in value:
        return 403
    if "expired" in value or "stale" in value or "digest" in value:
        return 412
    if "idempotency" in value or "conflict" in value or "in_progress" in value:
        return 409
    if "limit" in value or "blocked" in value or "size" in value:
        return 422
    return 400


__all__ = ["organization_bundles_bp"]
