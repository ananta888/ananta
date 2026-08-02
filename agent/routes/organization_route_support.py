"""Shared authentication, scope and HTTP-boundary helpers for Organization APIs."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Mapping

from flask import current_app, request
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.auth import get_authenticated_source_control_principal
from agent.common.errors import api_response
from agent.config import settings
from agent.database import engine
from agent.db_models.organizations import OrganizationInstanceDB, OrganizationMembershipDB
from agent.db_models.projects import ProjectDB, ProjectMembershipDB
from agent.services.organization_admission_exception_service import (
    OrganizationAdmissionExceptionService,
    SqlOrganizationAdmissionPolicy,
)
from agent.services.organization_blueprint_compiler import OrganizationCompilationError
from agent.services.organization_blueprint_instantiation_service import OrganizationInstantiationError
from agent.services.organization_blueprint_validation_service import OrganizationBlueprintValidationError
from agent.services.organization_compile_application_service import (
    OrganizationCompileApplicationService,
)
from agent.services.organization_definition_application_service import (
    OrganizationDefinitionApplicationService,
)
from agent.services.organization_definition_catalog_service import (
    OrganizationDefinitionCatalogError,
    get_organization_definition_catalog,
)
from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
    OrganizationMembershipService,
)
from agent.services.organization_projection_service import OrganizationProjectionError
from agent.services.organization_read_service import OrganizationReadError
from agent.services.organization_unit_of_work import OrganizationUnitOfWork
from agent.services.project_access_authority import (
    AuthorizedProjectScope,
    ProjectAccessError,
    ProjectCapability,
)
from agent.services.project_plan_grant_service import ProjectPlanGrantService

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,190}$")
_IDEMPOTENCY = re.compile(r"^[^\s]{8,191}$")


class OrganizationRouteError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.status_code = int(status_code)
        self.details = dict(details or {})
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class OrganizationRequestPrincipal:
    subject_id: str
    tenant_id: str
    project_id: str | None
    roles: frozenset[str]

    @property
    def tenant_admin(self) -> bool:
        return "admin" in self.roles or "tenant_admin" in self.roles

    def membership_principal(self) -> OrganizationAccessPrincipal:
        return OrganizationAccessPrincipal(
            principal_id=self.subject_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
        )


@dataclass(frozen=True, slots=True)
class OrganizationResourceScope:
    principal: OrganizationRequestPrincipal
    project: AuthorizedProjectScope
    organization: OrganizationInstanceDB

    @property
    def tenant_id(self) -> str:
        return self.project.tenant_id

    @property
    def project_id(self) -> str:
        return self.project.project_id

    @property
    def organization_id(self) -> str:
        return self.organization.organization_id


def organization_boundary(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except OrganizationRouteError as exc:
            return api_response(
                status="error",
                message=exc.reason_code,
                data=exc.details or None,
                code=exc.status_code,
            )
        except ProjectAccessError as exc:
            return api_response(status="error", message=exc.reason_code, code=exc.public_status)
        except OrganizationCompilationError as exc:
            return api_response(
                status="error",
                message=exc.reason_code,
                data={"path": exc.path, "details": exc.details},
                code=422,
            )
        except OrganizationBlueprintValidationError as exc:
            return api_response(
                status="error",
                message="organization_blueprint_invalid",
                data={"diagnostics": [item.model_dump(mode="json") for item in exc.issues]},
                code=422,
            )
        except OrganizationInstantiationError as exc:
            return api_response(
                status="error",
                message=exc.reason_code,
                code=_reason_status(exc.reason_code),
            )
        except (
            OrganizationDefinitionCatalogError,
            OrganizationProjectionError,
            OrganizationReadError,
        ) as exc:
            reason = str(getattr(exc, "reason_code", "") or str(exc))
            details = dict(getattr(exc, "details", {}) or {})
            return api_response(
                status="error",
                message=reason,
                data=details or None,
                code=_reason_status(reason),
            )
        except IntegrityError:
            return api_response(
                status="error",
                message="organization_write_conflict",
                code=409,
            )
        except PydanticValidationError as exc:
            return api_response(
                status="error",
                message="organization_contract_invalid",
                data={
                    "issues": [
                        {"path": list(item.get("loc") or ()), "message": item.get("msg")}
                        for item in exc.errors(include_url=False)
                    ]
                },
                code=422,
            )
        except ValueError as exc:
            reason = str(exc) or "organization_request_invalid"
            return api_response(status="error", message=reason, code=_reason_status(reason))

    return wrapped


def request_payload(*, allowed_fields: set[str]) -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, Mapping):
        raise OrganizationRouteError("organization_payload_invalid", status_code=400)
    payload = dict(value)
    unknown = sorted(set(payload) - allowed_fields)
    if unknown:
        raise OrganizationRouteError(
            "organization_payload_fields_invalid",
            status_code=400,
            details={"unknown_fields": unknown},
        )
    return payload


def request_principal() -> OrganizationRequestPrincipal:
    raw = get_authenticated_source_control_principal()
    subject = str(getattr(raw, "subject_id", None) or "").strip()
    tenant = str(getattr(raw, "tenant_id", None) or "").strip()
    project = str(getattr(raw, "project_id", None) or "").strip() or None
    roles = frozenset(str(value).strip() for value in (getattr(raw, "roles", None) or ()) if str(value).strip())
    if not subject or not tenant:
        raise OrganizationRouteError("organization_principal_scope_required", status_code=403)
    return OrganizationRequestPrincipal(subject, tenant, project, roles)


def require_project_scope(
    capability: ProjectCapability,
    *,
    payload_project_id: object | None = None,
) -> tuple[OrganizationRequestPrincipal, AuthorizedProjectScope]:
    principal = request_principal()
    selected = str(
        payload_project_id
        or request.args.get("project_id")
        or request.args.get("projectId")
        or principal.project_id
        or ""
    ).strip()
    if not selected:
        selected = _single_authorized_project(principal)
    if _IDENTIFIER.fullmatch(selected) is None:
        raise OrganizationRouteError("project_id_invalid", status_code=400)
    if principal.project_id and selected != principal.project_id:
        raise OrganizationRouteError("organization_not_found", status_code=404)
    authority = current_app.extensions.get("project_access_authority")
    if authority is None:
        raise OrganizationRouteError("project_access_authority_unavailable", status_code=503)
    scope = authority.require(
        tenant_id=principal.tenant_id,
        project_id=selected,
        subject_id=principal.subject_id,
        capability=capability,
        tenant_admin=principal.tenant_admin,
    )
    return principal, scope


def require_organization_scope(
    organization_id: str,
    capability: ProjectCapability = ProjectCapability.READ,
    *,
    include_archived: bool = True,
) -> OrganizationResourceScope:
    principal = request_principal()
    normalized_id = str(organization_id or "").strip()
    if _IDENTIFIER.fullmatch(normalized_id) is None:
        raise OrganizationRouteError("organization_not_found", status_code=404)
    with Session(engine) as session:
        statement = (
            select(OrganizationInstanceDB, OrganizationMembershipDB)
            .join(
                OrganizationMembershipDB,
                (OrganizationMembershipDB.tenant_id == OrganizationInstanceDB.tenant_id)
                & (OrganizationMembershipDB.project_id == OrganizationInstanceDB.project_id)
                & (OrganizationMembershipDB.organization_id == OrganizationInstanceDB.organization_id),
            )
            .where(OrganizationInstanceDB.tenant_id == principal.tenant_id)
            .where(OrganizationInstanceDB.organization_id == normalized_id)
            .where(OrganizationMembershipDB.principal_id == principal.subject_id)
            .where(
                (OrganizationMembershipDB.expires_at.is_(None)) | (OrganizationMembershipDB.expires_at > time.time())
            )
        )
        # A project-bound credential may never use an Organization membership
        # from another project.  Apply the immutable claim in the first SQL
        # lookup so the resource stays non-enumerable and no downstream
        # authority is consulted with a wider project scope.
        if principal.project_id:
            statement = statement.where(OrganizationInstanceDB.project_id == principal.project_id)
        # A project-unbound credential can legitimately have memberships in
        # several projects.  Never let SQL row order select one when the same
        # public Organization id is visible more than once.
        rows = list(session.exec(statement.limit(2)).all())
        if len(rows) != 1:
            raise OrganizationRouteError("organization_not_found", status_code=404)
        organization = rows[0][0]
    authority = current_app.extensions.get("project_access_authority")
    if authority is None:
        raise OrganizationRouteError("project_access_authority_unavailable", status_code=503)
    project = authority.require(
        tenant_id=principal.tenant_id,
        project_id=organization.project_id,
        subject_id=principal.subject_id,
        capability=capability,
        tenant_admin=principal.tenant_admin,
        include_archived=include_archived,
    )
    membership = organization_membership_service()
    if not membership.can_view(
        principal=principal.membership_principal(),
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        organization_id=normalized_id,
    ):
        raise OrganizationRouteError("organization_not_found", status_code=404)
    return OrganizationResourceScope(principal, project, organization)


def require_if_match_header() -> str:
    raw = str(request.headers.get("If-Match") or "").strip()
    if not raw:
        raise OrganizationRouteError("organization_if_match_required", status_code=428)
    value = raw[2:] if raw.startswith("W/") else raw
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not value or len(value) > 191:
        raise OrganizationRouteError("organization_if_match_invalid", status_code=400)
    return value


def require_if_match(expected: str) -> str:
    value = require_if_match_header()
    if value != str(expected or ""):
        raise OrganizationRouteError("organization_revision_stale", status_code=412)
    return value


def require_idempotency_key() -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if _IDEMPOTENCY.fullmatch(value) is None:
        raise OrganizationRouteError("organization_idempotency_key_invalid", status_code=400)
    return value


def require_admin_grant_header(*, body_value: object | None = None) -> str:
    value = str(request.headers.get("X-Organization-Admin-Grant") or "").strip()
    if not value or len(value) > 191 or any(character.isspace() for character in value):
        raise OrganizationRouteError("organization_admin_grant_required", status_code=403)
    if body_value is not None and value != str(body_value or "").strip():
        raise OrganizationRouteError("organization_admin_grant_binding_invalid", status_code=403)
    return value


def organization_catalog():
    return current_app.extensions.get("organization_definition_catalog") or get_organization_definition_catalog()


def organization_compile_service() -> OrganizationCompileApplicationService:
    service = current_app.extensions.get("organization_compile_application_service")
    if service is not None:
        return service
    return OrganizationCompileApplicationService(
        catalog=organization_catalog(),
        admission_policy=current_app.extensions.get("organization_admission_policy")
        or SqlOrganizationAdmissionPolicy(),
        signing_secret=str(current_app.secret_key or settings.secret_key or ""),
        token_ttl_seconds=int(current_app.config.get("ORGANIZATION_COMPILE_TOKEN_TTL_SECONDS", 900)),
    )


def organization_admission_exception_service() -> OrganizationAdmissionExceptionService:
    service = current_app.extensions.get("organization_admission_exception_service")
    if service is not None:
        return service
    return OrganizationAdmissionExceptionService(catalog=organization_catalog())


def organization_definition_service() -> OrganizationDefinitionApplicationService:
    service = current_app.extensions.get("organization_definition_application_service")
    if service is not None:
        return service
    return OrganizationDefinitionApplicationService(
        catalog=organization_catalog(),
        plan_grants=project_plan_grant_service(),
    )


def project_plan_grant_service() -> ProjectPlanGrantService:
    return current_app.extensions.get("project_plan_grant_service") or ProjectPlanGrantService()


def organization_membership_service() -> OrganizationMembershipService:
    return current_app.extensions.get("organization_membership_service") or OrganizationMembershipService()


def organization_uow_factory():
    return current_app.extensions.get("organization_uow_factory") or OrganizationUnitOfWork


def _single_authorized_project(principal: OrganizationRequestPrincipal) -> str:
    with Session(engine) as session:
        if principal.tenant_admin:
            rows = session.exec(
                select(ProjectDB.project_id)
                .where(ProjectDB.tenant_id == principal.tenant_id)
                .where(ProjectDB.status == "active")
                .order_by(ProjectDB.project_id)
            ).all()
        else:
            rows = session.exec(
                select(ProjectMembershipDB.project_id)
                .where(ProjectMembershipDB.tenant_id == principal.tenant_id)
                .where(ProjectMembershipDB.subject_id == principal.subject_id)
                .where(ProjectMembershipDB.state == "active")
                .order_by(ProjectMembershipDB.project_id)
            ).all()
    project_ids = sorted({str(value) for value in rows if str(value)})
    if not project_ids:
        raise OrganizationRouteError("project_not_found", status_code=404)
    if len(project_ids) != 1:
        raise OrganizationRouteError(
            "project_id_required",
            status_code=400,
            details={"authorized_project_count": len(project_ids)},
        )
    return project_ids[0]


def _reason_status(reason: str) -> int:
    normalized = str(reason or "").lower()
    if "not_found" in normalized or "scope_mismatch" in normalized:
        return 404
    if (
        "stale" in normalized
        or "if_match" in normalized
        or "precondition" in normalized
        or "digest_mismatch" in normalized
    ):
        return 412
    if (
        "idempotency" in normalized
        or "conflict" in normalized
        or "in_progress" in normalized
        or "active_instances" in normalized
        or "already_retired" in normalized
        or "content_hash_mismatch" in normalized
        or "referenced_definition_hash_mismatch" in normalized
        or "no_changes" in normalized
        or "version_not_next" in normalized
    ):
        return 409
    if "grant" in normalized or "access" in normalized or "forbidden" in normalized:
        return 403
    if "unavailable" in normalized:
        return 503
    if any(value in normalized for value in ("cursor", "query", "parameter", "payload")):
        return 400
    if any(value in normalized for value in ("limit", "invalid", "required", "blocked", "missing")):
        return 422
    return 400


__all__ = [
    "OrganizationRequestPrincipal",
    "OrganizationResourceScope",
    "OrganizationRouteError",
    "organization_boundary",
    "organization_admission_exception_service",
    "organization_catalog",
    "organization_compile_service",
    "organization_definition_service",
    "organization_membership_service",
    "project_plan_grant_service",
    "organization_uow_factory",
    "request_payload",
    "request_principal",
    "require_admin_grant_header",
    "require_idempotency_key",
    "require_if_match",
    "require_if_match_header",
    "require_organization_scope",
    "require_project_scope",
]
