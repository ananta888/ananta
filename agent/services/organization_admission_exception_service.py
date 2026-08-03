"""Issue, validate and project one-shot custom-organization admission grants."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.organizations import OrganizationAdmissionExceptionDB
from agent.models.organization_models import (
    VersionedDefinitionRef,
    canonical_definition_sha256,
    canonical_sha256,
)
from agent.services.organization_custom_composition_service import (
    OrganizationCustomCompositionService,
    custom_composition_digest,
)
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
    OrganizationDefinitionCatalogService,
)


class OrganizationAdmissionExceptionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SqlOrganizationAdmissionPolicy:
    """Read-only compiler port backed by unconsumed Hub grants."""

    def __init__(self, *, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or self._default_session

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def validate_exception(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        exception_ref: str,
        definition_ref: str,
        definition_revision: str,
        policy_hash: str,
        composition_digest: str,
        composition: dict[str, int],
    ) -> tuple[bool, str | None]:
        try:
            definition = VersionedDefinitionRef.parse(definition_ref)
        except ValueError:
            return False, "organization_admission_definition_invalid"
        with self._session_factory() as session:
            row = session.exec(
                select(OrganizationAdmissionExceptionDB).where(
                    OrganizationAdmissionExceptionDB.exception_id == str(exception_ref or ""),
                    OrganizationAdmissionExceptionDB.tenant_id == tenant_id,
                    OrganizationAdmissionExceptionDB.project_id == project_id,
                    OrganizationAdmissionExceptionDB.principal_id == principal_id,
                    OrganizationAdmissionExceptionDB.definition_key == definition.key,
                    OrganizationAdmissionExceptionDB.definition_version == definition.version,
                    OrganizationAdmissionExceptionDB.definition_revision == definition_revision,
                    OrganizationAdmissionExceptionDB.policy_hash == policy_hash,
                    OrganizationAdmissionExceptionDB.composition_digest == composition_digest,
                    OrganizationAdmissionExceptionDB.status == "issued",
                    OrganizationAdmissionExceptionDB.revoked_at.is_(None),  # type: ignore[union-attr]
                    OrganizationAdmissionExceptionDB.expires_at > time.time(),
                )
            ).one_or_none()
        if row is None:
            return False, "organization_admission_exception_invalid"
        normalized = {str(key): int(value) for key, value in sorted(composition.items())}
        if dict(row.composition_json or {}) != normalized:
            return False, "organization_admission_composition_mismatch"
        return True, None


class OrganizationAdmissionExceptionService:
    """Project-scoped command service; compilation itself remains write-free."""

    def __init__(
        self,
        *,
        catalog: OrganizationDefinitionCatalogService,
        session_factory: Callable[[], Session] | None = None,
        custom_compositions: OrganizationCustomCompositionService | None = None,
    ) -> None:
        self._catalog = catalog
        self._session_factory = session_factory or self._default_session
        self._custom = custom_compositions or OrganizationCustomCompositionService()

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def issue(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        definition_key: str,
        definition_version: int | None,
        composition: Mapping[str, int],
        reason: str,
        idempotency_key: str,
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        normalized_reason = str(reason or "").strip()
        if not normalized_reason or len(normalized_reason) > 512:
            raise OrganizationAdmissionExceptionError("organization_admission_reason_invalid")
        definition, limits = self._resolve_active_definition(
            tenant_id=tenant_id,
            project_id=project_id,
            definition_key=definition_key,
            definition_version=definition_version,
        )
        custom = self._custom.validate(
            definition=definition,
            composition=composition,
            maximum_team_count=limits.max_team_instances_per_organization,
        )
        definition_ref = f"{definition.key}@{definition.version}"
        definition_revision = canonical_definition_sha256(definition)
        policy_hash = limits.content_hash()
        composition_digest = custom_composition_digest(
            definition_ref=definition_ref,
            definition_revision=definition_revision,
            policy_hash=policy_hash,
            composition=custom.team_blueprint_counts,
        )
        bounded_ttl = max(60, min(int(ttl_seconds), 3600))
        request_digest = canonical_sha256(
            {
                "schema": "organization_admission_exception_request.v1",
                "tenant_id": tenant_id,
                "project_id": project_id,
                "principal_id": principal_id,
                "definition_ref": definition_ref,
                "definition_revision": definition_revision,
                "policy_hash": policy_hash,
                "composition_digest": composition_digest,
                "reason": normalized_reason,
                "ttl_seconds": bounded_ttl,
            }
        )
        with self._session_factory() as session:
            existing = self._by_idempotency(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return self._replay(existing, request_digest=request_digest)
            row = OrganizationAdmissionExceptionDB(
                tenant_id=tenant_id,
                project_id=project_id,
                principal_id=principal_id,
                definition_key=definition.key,
                definition_version=definition.version,
                definition_revision=definition_revision,
                composition_digest=composition_digest,
                policy_hash=policy_hash,
                team_count=custom.team_count,
                composition_json=custom.team_blueprint_counts,
                capability_gaps=list(custom.capability_gaps),
                reason=normalized_reason,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                status="issued",
                issued_by=principal_id,
                expires_at=time.time() + bounded_ttl,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                concurrent = self._by_idempotency(
                    session,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    principal_id=principal_id,
                    idempotency_key=idempotency_key,
                )
                if concurrent is None:
                    raise
                return self._replay(concurrent, request_digest=request_digest)
            session.refresh(row)
            return self._response(row, replayed=False)

    def _resolve_active_definition(
        self,
        *,
        tenant_id: str,
        project_id: str,
        definition_key: str,
        definition_version: int | None,
    ):
        from agent.repositories.organizations.adapters import (
            SqlOrganizationDefinitionCatalogAdapter,
            SqlOrganizationLimitProfileAdapter,
        )
        from agent.repositories.organizations.definitions import (
            SqlOrganizationDefinitionRepository,
        )

        with self._session_factory() as session:
            repository = FileCatalogDefinitionRepositoryAdapter(
                SqlOrganizationDefinitionRepository(session),
                self._catalog,
                session,
            )
            versions = (
                {int(definition_version)}
                if definition_version is not None
                else {
                    value.version
                    for value in self._catalog.list_organization_blueprints()
                    if value.key == definition_key
                }
            )
            if definition_version is None:
                versions.update(
                    row.version
                    for row in repository.list_organization_blueprint_revisions(
                        tenant_id,
                        project_id,
                        key=definition_key,
                    )
                )
            definitions = SqlOrganizationDefinitionCatalogAdapter(
                repository,
                tenant_id=tenant_id,
                project_id=project_id,
            )
            for version in sorted(versions, reverse=True):
                row = repository.get_organization_blueprint(
                    tenant_id,
                    project_id,
                    definition_key,
                    version,
                )
                if row is None or str(row.lifecycle) != "active":
                    continue
                definition = definitions.get_organization_blueprint(
                    definition_key,
                    version,
                )
                if definition is None or canonical_definition_sha256(definition) != str(row.content_hash or ""):
                    raise OrganizationAdmissionExceptionError("organization_definition_content_hash_mismatch")
                limits = SqlOrganizationLimitProfileAdapter(repository).resolve_limit_profile(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    policy_ref=definition.limit_policy_ref,
                )
                return definition, limits
        raise OrganizationAdmissionExceptionError("organization_blueprint_not_found")

    @staticmethod
    def _by_idempotency(
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        idempotency_key: str,
    ) -> OrganizationAdmissionExceptionDB | None:
        return session.exec(
            select(OrganizationAdmissionExceptionDB).where(
                OrganizationAdmissionExceptionDB.tenant_id == tenant_id,
                OrganizationAdmissionExceptionDB.project_id == project_id,
                OrganizationAdmissionExceptionDB.principal_id == principal_id,
                OrganizationAdmissionExceptionDB.idempotency_key == idempotency_key,
            )
        ).one_or_none()

    def _replay(
        self,
        row: OrganizationAdmissionExceptionDB,
        *,
        request_digest: str,
    ) -> dict[str, Any]:
        if row.request_digest != request_digest:
            raise OrganizationAdmissionExceptionError("organization_admission_idempotency_conflict")
        return self._response(row, replayed=True)

    @staticmethod
    def _response(
        row: OrganizationAdmissionExceptionDB,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "admission_exception_ref": row.exception_id,
            "definition_ref": f"{row.definition_key}@{row.definition_version}",
            "definition_revision": row.definition_revision,
            "composition_digest": row.composition_digest,
            "team_blueprint_counts": dict(row.composition_json or {}),
            "team_count": row.team_count,
            "capability_gaps": list(row.capability_gaps or []),
            "policy_hash": row.policy_hash,
            "status": row.status,
            "expires_at": row.expires_at,
            "replayed": replayed,
        }


__all__ = [
    "OrganizationAdmissionExceptionError",
    "OrganizationAdmissionExceptionService",
    "SqlOrganizationAdmissionPolicy",
]
