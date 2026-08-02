"""Application services for guarded Organization instance mutations.

The domain instantiator intentionally knows nothing about HTTP grants.  This
module composes it with a transaction-opening UoW that consumes a plan-bound
pre-creation grant in the *same* transaction as aggregate materialization.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Mapping

from sqlalchemy import update
from sqlmodel import select

from agent.db_models.organizations import (
    OrganizationAdminGrantDB,
    OrganizationAdmissionExceptionDB,
    OrganizationAuditOutboxDB,
    OrganizationMembershipDB,
    OrganizationOperationDB,
    OrganizationTopologySnapshotDB,
)
from agent.models.organization_models import CompiledOrganizationPlan, canonical_sha256
from agent.services.organization_active_work_service import (
    OrganizationActiveWorkError,
    SqlOrganizationActiveWorkService,
)
from agent.services.organization_blueprint_instantiation_service import (
    OrganizationBlueprintInstantiationService,
    OrganizationInstantiationError,
)
from agent.services.organization_custom_composition_service import (
    custom_composition_digest,
)
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
    OrganizationDefinitionCatalogService,
)
from agent.services.organization_lifecycle_service import (
    OrganizationActivitySnapshot,
    OrganizationLifecycleService,
)
from agent.services.organization_unit_of_work import OrganizationUnitOfWork

_PRECREATION_GRANT_KINDS = frozenset({"instantiate", "organization_instantiation", "organization_admin"})
_INSTANCE_ADMIN_GRANT_KINDS = frozenset({"organization_admin", "organization_lifecycle"})


class GrantConsumingOrganizationUnitOfWork(OrganizationUnitOfWork):
    """Add catalog fallback and atomic, one-shot grant consumption to a UoW."""

    def __init__(
        self,
        *,
        catalog: OrganizationDefinitionCatalogService,
        grant_id: str,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        plan_digest: str,
        policy_hash: str,
        idempotency_key: str,
        admission_exception_ref: str | None = None,
        admission_composition_digest: str | None = None,
        session_factory=None,
    ) -> None:
        super().__init__(session_factory=session_factory)
        self._catalog = catalog
        self._grant_id = grant_id
        self._tenant_id = tenant_id
        self._project_id = project_id
        self._organization_id = organization_id
        self._principal_id = principal_id
        self._plan_digest = plan_digest
        self._policy_hash = policy_hash
        self._idempotency_key = idempotency_key
        self._admission_exception_ref = str(admission_exception_ref or "").strip()
        self._admission_composition_digest = str(admission_composition_digest or "").strip()

    def __enter__(self) -> "GrantConsumingOrganizationUnitOfWork":
        super().__enter__()
        if self.session is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("organization_uow_not_entered")
        self.definitions = FileCatalogDefinitionRepositoryAdapter(
            self.definitions,
            self._catalog,
            self.session,
        )

        # A completed operation is a legitimate replay.  It was authorized
        # and consumed the grant in its original transaction, so requiring a
        # still-live one-shot grant here would break idempotency semantics.
        existing = self.operations.get_by_idempotency_key(
            self._tenant_id,
            self._project_id,
            "instantiate",
            self._idempotency_key,
            for_update=True,
        )
        if existing is not None and existing.status == "applied" and existing.plan_digest == self._plan_digest:
            return self

        now = time.time()
        if self._admission_exception_ref:
            admission = (
                update(OrganizationAdmissionExceptionDB)
                .where(OrganizationAdmissionExceptionDB.exception_id == self._admission_exception_ref)
                .where(OrganizationAdmissionExceptionDB.tenant_id == self._tenant_id)
                .where(OrganizationAdmissionExceptionDB.project_id == self._project_id)
                .where(OrganizationAdmissionExceptionDB.principal_id == self._principal_id)
                .where(OrganizationAdmissionExceptionDB.composition_digest == self._admission_composition_digest)
                .where(OrganizationAdmissionExceptionDB.status == "issued")
                .where(OrganizationAdmissionExceptionDB.revoked_at.is_(None))
                .where(OrganizationAdmissionExceptionDB.expires_at > now)
                .values(
                    status="consumed",
                    consumed_at=now,
                    consumed_organization_id=self._organization_id,
                )
            )
            admission_result = self.session.exec(admission)
            if int(getattr(admission_result, "rowcount", 0) or 0) != 1:
                self._abort_enter("organization_admission_exception_invalid")
        statement = (
            update(OrganizationAdminGrantDB)
            .where(OrganizationAdminGrantDB.grant_id == self._grant_id)
            .where(OrganizationAdminGrantDB.tenant_id == self._tenant_id)
            .where(OrganizationAdminGrantDB.project_id == self._project_id)
            .where(OrganizationAdminGrantDB.organization_id.is_(None))
            .where(OrganizationAdminGrantDB.principal_id == self._principal_id)
            .where(OrganizationAdminGrantDB.plan_digest == self._plan_digest)
            .where(OrganizationAdminGrantDB.policy_hash == self._policy_hash)
            .where(OrganizationAdminGrantDB.grant_kind.in_(_PRECREATION_GRANT_KINDS))
            .where(OrganizationAdminGrantDB.revoked_at.is_(None))
            .where((OrganizationAdminGrantDB.expires_at.is_(None)) | (OrganizationAdminGrantDB.expires_at > now))
            .values(revoked_at=now)
        )
        result = self.session.exec(statement)
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            # Raising from __enter__ does not call __exit__, therefore close
            # the transaction explicitly before returning to the HTTP layer.
            self._abort_enter("organization_precreation_admin_grant_invalid")
        return self

    def _abort_enter(self, reason_code: str) -> None:
        if self.session is not None:
            self.session.rollback()
            self.session.close()
            self.session = None
        raise OrganizationInstantiationError(reason_code)


class OrganizationInstanceApplicationService:
    def __init__(
        self,
        *,
        catalog: OrganizationDefinitionCatalogService,
        session_factory=None,
        active_work: SqlOrganizationActiveWorkService | None = None,
    ) -> None:
        self._catalog = catalog
        self._session_factory = session_factory
        self._active_work = active_work or SqlOrganizationActiveWorkService()

    def instantiate(
        self,
        *,
        plan: CompiledOrganizationPlan,
        name: str,
        idempotency_key: str,
        definition_revision: str,
        plan_digest: str,
        principal_id: str,
        grant_id: str,
        admin_policy_hash: str,
        admission_exception_ref: str | None = None,
        custom_composition: dict[str, int] | None = None,
    ):
        admission_composition_digest: str | None = None
        if plan.composition_mode == "custom":
            if not admission_exception_ref or not custom_composition:
                raise OrganizationInstantiationError("organization_admission_exception_required")
            admission_composition_digest = custom_composition_digest(
                definition_ref=plan.definition_ref,
                definition_revision=plan.definition_revision,
                policy_hash=plan.effective_limit_profile_hash,
                composition=custom_composition,
            )
        elif admission_exception_ref:
            raise OrganizationInstantiationError("organization_admission_exception_unexpected")

        def uow_factory() -> GrantConsumingOrganizationUnitOfWork:
            return GrantConsumingOrganizationUnitOfWork(
                catalog=self._catalog,
                grant_id=grant_id,
                tenant_id=plan.tenant_id,
                project_id=plan.project_id,
                organization_id=plan.organization_id,
                principal_id=principal_id,
                plan_digest=plan.plan_digest,
                policy_hash=admin_policy_hash,
                idempotency_key=idempotency_key,
                admission_exception_ref=admission_exception_ref,
                admission_composition_digest=admission_composition_digest,
                session_factory=self._session_factory,
            )

        return OrganizationBlueprintInstantiationService(
            limit_profiles=self._catalog,
            uow_factory=uow_factory,
        ).instantiate(
            plan=plan,
            name=name,
            idempotency_key=idempotency_key,
            expected_definition_revision=definition_revision,
            expected_plan_digest=plan_digest,
            principal_id=principal_id,
        )

    def transition_lifecycle(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        grant_id: str,
        expected_lock_version: int,
        idempotency_key: str,
        target_state: str,
        active_work_strategy: str | None,
        activity: OrganizationActivitySnapshot,
        migration_target: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_digest = canonical_sha256(
            {
                "organization_id": organization_id,
                "target_state": target_state,
                "active_work_strategy": active_work_strategy,
                "migration_target": dict(migration_target or {}),
                "expected_lock_version": expected_lock_version,
                "principal_id": principal_id,
            }
        )
        with OrganizationUnitOfWork(session_factory=self._session_factory) as uow:
            existing = uow.operations.get_by_idempotency_key(
                tenant_id,
                project_id,
                "lifecycle",
                idempotency_key,
                for_update=True,
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise OrganizationInstantiationError("organization_idempotency_key_conflict")
                if existing.status != "applied":
                    raise OrganizationInstantiationError("organization_lifecycle_in_progress")
                return {**dict(existing.result_json or {}), "replayed": True}

            organization = uow.instances.get_scoped(
                tenant_id,
                project_id,
                organization_id,
                for_update=True,
            )
            if organization is None:
                raise OrganizationInstantiationError("organization_not_found")
            if organization.lock_version != expected_lock_version:
                raise OrganizationInstantiationError("organization_revision_stale")
            self._require_instance_admin_grant(
                uow=uow,
                grant_id=grant_id,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                principal_id=principal_id,
                policy_hash=organization.plan_digest,
            )
            if uow.session is None:  # pragma: no cover - UoW invariant
                raise RuntimeError("organization_uow_not_entered")
            # Re-read all mutable work under the same transaction that will
            # execute the transition.  The route-level snapshot is only an
            # early diagnostic and must never be used as authorization proof.
            locked_activity = self._active_work.snapshot(
                session=uow.session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                for_update=True,
            )
            lifecycle_plan = OrganizationLifecycleService().plan_transition(
                organization_id=organization_id,
                current_state=organization.lifecycle,
                target_state=target_state,
                activity=locked_activity,
                active_work_strategy=active_work_strategy,
            )
            if not lifecycle_plan.allowed:
                raise OrganizationInstantiationError(lifecycle_plan.reason_code)

            operation = OrganizationOperationDB(
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                operation_kind="lifecycle",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                plan_digest=lifecycle_plan.plan_digest,
                expected_revision=str(expected_lock_version),
                status="pending",
            )
            uow.operations.add(operation)
            now = time.time()
            active_work_result: dict[str, Any] | None = None
            if locked_activity.has_active_work:
                try:
                    active_work_result = self._active_work.execute(
                        session=uow.session,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        organization_id=organization_id,
                        strategy=str(active_work_strategy or ""),
                        operation_key=operation.operation_id,
                        principal_id=principal_id,
                        migration_target=migration_target,
                        now=now,
                    ).as_dict()
                except OrganizationActiveWorkError as exc:
                    raise OrganizationInstantiationError(exc.reason_code) from exc
            organization.lifecycle = lifecycle_plan.to_state
            organization.lock_version += 1
            organization.updated_at = now
            organization.archived_at = now if lifecycle_plan.to_state == "archived" else None
            uow.instances.add(organization)
            snapshot_hash = self._stage_lifecycle_snapshot(
                uow=uow,
                organization=organization,
                lifecycle_plan=lifecycle_plan,
                principal_id=principal_id,
            )
            result = {
                "organization_id": organization_id,
                "from_state": lifecycle_plan.from_state,
                "lifecycle": lifecycle_plan.to_state,
                "lock_version": organization.lock_version,
                "revision": str(organization.lock_version),
                "plan_digest": lifecycle_plan.plan_digest,
                "snapshot_hash": snapshot_hash,
                "preserves_lineage": list(lifecycle_plan.preserves_lineage),
                "active_work": active_work_result,
                "starts_workers": False,
                "reruns_tasks": False,
                "replayed": False,
            }
            uow.audit_outbox.add(
                OrganizationAuditOutboxDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    event_key=f"organization-lifecycle:{operation.operation_id}",
                    event_kind="organization.lifecycle_changed.v1",
                    payload_json={**result, "principal_id": principal_id},
                )
            )
            operation.status = "applied"
            operation.result_ref = organization_id
            operation.result_json = result
            operation.applied_at = now
            uow.operations.add(operation)
            return result

    @staticmethod
    def _stage_lifecycle_snapshot(*, uow, organization, lifecycle_plan, principal_id):
        if not lifecycle_plan.required_operations:
            current = uow.snapshots.latest(
                organization.tenant_id,
                organization.project_id,
                organization.organization_id,
            )
            return current.snapshot_hash if current is not None else organization.plan_digest
        current = uow.snapshots.latest(
            organization.tenant_id,
            organization.project_id,
            organization.organization_id,
        )
        payload = (
            copy.deepcopy(dict(current.snapshot_json or {}))
            if current
            else {
                "organization_id": organization.organization_id,
                "definition_revision": organization.definition_revision,
                "units": [],
                "role_slots": [],
                "relations": [],
            }
        )
        payload["lifecycle_transition"] = {
            "from_state": lifecycle_plan.from_state,
            "to_state": lifecycle_plan.to_state,
            "plan_digest": lifecycle_plan.plan_digest,
            "required_operations": list(lifecycle_plan.required_operations),
            "principal_id": principal_id,
            "starts_workers": False,
            "reruns_tasks": False,
        }
        snapshot_hash = canonical_sha256(payload)
        uow.snapshots.add(
            OrganizationTopologySnapshotDB(
                tenant_id=organization.tenant_id,
                project_id=organization.project_id,
                organization_id=organization.organization_id,
                revision=(current.revision + 1 if current is not None else 1),
                definition_revision=organization.definition_revision,
                snapshot_hash=snapshot_hash,
                snapshot_json=payload,
            )
        )
        return snapshot_hash

    @staticmethod
    def _require_instance_admin_grant(
        *,
        uow,
        grant_id: str,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        policy_hash: str,
    ) -> None:
        if uow.session is None:
            raise RuntimeError("organization_uow_not_entered")
        now = time.time()
        membership = uow.session.exec(
            select(OrganizationMembershipDB)
            .where(OrganizationMembershipDB.tenant_id == tenant_id)
            .where(OrganizationMembershipDB.project_id == project_id)
            .where(OrganizationMembershipDB.organization_id == organization_id)
            .where(OrganizationMembershipDB.principal_id == principal_id)
            .where(OrganizationMembershipDB.membership_kind == "organization_admin")
            .where((OrganizationMembershipDB.expires_at.is_(None)) | (OrganizationMembershipDB.expires_at > now))
            .with_for_update()
        ).first()
        if membership is None:
            raise OrganizationInstantiationError("organization_admin_membership_required")
        statement = (
            select(OrganizationAdminGrantDB)
            .where(OrganizationAdminGrantDB.grant_id == grant_id)
            .where(OrganizationAdminGrantDB.tenant_id == tenant_id)
            .where(OrganizationAdminGrantDB.project_id == project_id)
            .where(OrganizationAdminGrantDB.organization_id == organization_id)
            .where(OrganizationAdminGrantDB.principal_id == principal_id)
            .where(OrganizationAdminGrantDB.policy_hash == policy_hash)
            .where(OrganizationAdminGrantDB.grant_kind.in_(_INSTANCE_ADMIN_GRANT_KINDS))
            .where(OrganizationAdminGrantDB.revoked_at.is_(None))
            .where((OrganizationAdminGrantDB.expires_at.is_(None)) | (OrganizationAdminGrantDB.expires_at > now))
            .with_for_update()
        )
        if uow.session.exec(statement).first() is None:
            raise OrganizationInstantiationError("organization_admin_grant_invalid")


__all__ = [
    "GrantConsumingOrganizationUnitOfWork",
    "OrganizationInstanceApplicationService",
]
