"""Write-free topology patch planning and atomic Hub-owned application.

Definitions, runtime overlays and presentation data stay separate: this
service mutates normalized organization definition-instance rows only.  It
never accepts runtime edges and never writes UI layout coordinates.
"""

from __future__ import annotations

import time

from agent.db_models.organizations import (
    OrganizationAuditOutboxDB,
    OrganizationOperationDB,
    OrganizationTopologyPatchGrantDB,
)
from agent.models.organization_models import (
    OrganizationLimitProfile,
    canonical_sha256,
)
from agent.ports.organization_definitions import OrganizationLimitProfilePort
from agent.repositories.organizations.adapters import SqlOrganizationLimitProfileAdapter
from agent.services.organization_active_work_service import (
    SqlOrganizationActiveWorkService,
)
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
)
from agent.services.organization_topology_patch_contracts import (
    OrganizationPatchReadPort,
    OrganizationPatchState,
    OrganizationTopologyPatchApplyResult,
    OrganizationTopologyPatchDocument,
    OrganizationTopologyPatchError,
    OrganizationTopologyPatchGrantResult,
    OrganizationTopologyPatchPreview,
    TopologyAddOperation,
    TopologyAddValue,
    TopologyAssignOperation,
    TopologyConnectOperation,
    TopologyMigrationTarget,
    TopologyPatchOperation,
    TopologyRemoveOperation,
    TopologyReparentOperation,
)
from agent.services.organization_topology_patch_evaluator import (
    OrganizationTopologyPatchEvaluator,
    TopologyPatchEvaluation,
)
from agent.services.organization_topology_patch_reader import SqlOrganizationPatchReadAdapter
from agent.services.organization_topology_patch_stager import OrganizationTopologyPatchStager
from agent.services.organization_unit_of_work import OrganizationUnitOfWork


class OrganizationTopologyApplyService:
    def __init__(
        self,
        *,
        reader: OrganizationPatchReadPort,
        limit_profiles: OrganizationLimitProfilePort,
        uow_factory=OrganizationUnitOfWork,
        clock=time.time,
        preview_ttl_seconds: int = 300,
        grant_ttl_seconds: int = 120,
        fault_injector=None,
        catalog=None,
        assignment_eligibility: OrganizationAssignmentEligibilityService | None = None,
        active_work: SqlOrganizationActiveWorkService | None = None,
        evaluator: OrganizationTopologyPatchEvaluator | None = None,
        stager: OrganizationTopologyPatchStager | None = None,
    ) -> None:
        self._reader = reader
        self._limit_profiles = limit_profiles
        self._uow_factory = uow_factory
        self._clock = clock
        self._ttl = max(30, min(int(preview_ttl_seconds), 1800))
        self._grant_ttl = max(30, min(int(grant_ttl_seconds), 300))
        self._fault_injector = fault_injector or (lambda _step: None)
        self._catalog = catalog
        self._assignment_eligibility = assignment_eligibility or OrganizationAssignmentEligibilityService()
        self._active_work = active_work or SqlOrganizationActiveWorkService()
        self._evaluator = evaluator or OrganizationTopologyPatchEvaluator(
            assignment_eligibility=self._assignment_eligibility,
        )
        self._stager = stager or OrganizationTopologyPatchStager(
            active_work=self._active_work,
            clock=self._clock,
            fault_injector=self._fault_injector,
        )

    def preview(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        document: OrganizationTopologyPatchDocument,
    ) -> OrganizationTopologyPatchPreview:
        agent_ids = {row.agent_id for row in document.operations if isinstance(row, TopologyAssignOperation)}
        state = self._reader.load_state(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            agent_ids=agent_ids,
        )
        if state is None:
            raise OrganizationTopologyPatchError("organization_not_found", public_status=404)
        limits = self._resolve_limits(state)
        return self._evaluate(
            state=state,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            document=document,
            limits=limits,
            expires_at_epoch=self._clock() + self._ttl,
        ).preview

    def issue_grant(
        self,
        *,
        preview: OrganizationTopologyPatchPreview,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        expected_revision: str,
        expected_patch_digest: str,
        issue_idempotency_key: str,
        parent_admin_grant_id: str,
    ) -> OrganizationTopologyPatchGrantResult:
        """Issue one short-lived child grant from an unchanged preview."""

        self._validate_preview_envelope(
            preview=preview,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            expected_revision=expected_revision,
            expected_patch_digest=expected_patch_digest,
        )
        now = self._clock()
        if preview.expires_at_epoch < now:
            raise OrganizationTopologyPatchError(
                "organization_patch_preview_expired",
                public_status=412,
            )
        if not issue_idempotency_key or not parent_admin_grant_id:
            raise OrganizationTopologyPatchError(
                "organization_patch_grant_issue_binding_missing",
                public_status=400,
            )

        with self._uow_factory() as uow:
            membership = self._active_admin_membership(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                principal_id=principal_id,
            )
            parent = uow.admin_grants.get_scoped(
                tenant_id,
                project_id,
                organization_id,
                parent_admin_grant_id,
                for_update=True,
            )
            if membership is None or parent is None:
                raise OrganizationTopologyPatchError(
                    "organization_patch_parent_authority_invalid",
                    public_status=403,
                )
            if (
                parent.principal_id != principal_id
                or parent.grant_kind != "organization_admin"
                or parent.revoked_at is not None
                or (parent.expires_at is not None and float(parent.expires_at) < now)
            ):
                raise OrganizationTopologyPatchError(
                    "organization_patch_parent_grant_invalid",
                    public_status=403,
                )

            existing = uow.topology_patch_grants.get_by_issue_idempotency_key(
                tenant_id,
                project_id,
                organization_id,
                principal_id,
                issue_idempotency_key,
                for_update=True,
            )
            if existing is not None:
                self._validate_grant_binding(
                    existing,
                    preview=preview,
                    principal_id=principal_id,
                )
                if existing.parent_admin_grant_id != parent_admin_grant_id:
                    raise OrganizationTopologyPatchError("organization_patch_grant_idempotency_conflict")
                return self._grant_result(existing, replayed=True)

            current = self._authoritative_preview(
                uow,
                preview=preview,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                principal_id=principal_id,
            )
            self._require_unchanged_preview(preview, current)
            expires_at = min(float(preview.expires_at_epoch), now + self._grant_ttl)
            if expires_at <= now:
                raise OrganizationTopologyPatchError(
                    "organization_patch_preview_expired",
                    public_status=412,
                )
            grant = OrganizationTopologyPatchGrantDB(
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                principal_id=principal_id,
                parent_admin_grant_id=parent_admin_grant_id,
                patch_digest=preview.patch_digest,
                policy_hash=preview.effective_policy_hash,
                limit_hash=preview.effective_limit_profile_hash,
                expected_revision=preview.expected_revision,
                issue_idempotency_key=issue_idempotency_key,
                granted_by=principal_id,
                expires_at=expires_at,
            )
            uow.topology_patch_grants.add(grant)
            uow.audit_outbox.add(
                OrganizationAuditOutboxDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    event_key=f"organization-topology-patch-grant-issued:{grant.grant_id}",
                    event_kind="organization.topology_patch_grant_issued.v1",
                    payload_json={
                        "grant_id": grant.grant_id,
                        "principal_id": principal_id,
                        "patch_digest": preview.patch_digest,
                        "policy_hash": preview.effective_policy_hash,
                        "limit_hash": preview.effective_limit_profile_hash,
                        "expires_at": expires_at,
                    },
                )
            )
            return self._grant_result(grant)

    def apply(
        self,
        *,
        preview: OrganizationTopologyPatchPreview,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        expected_revision: str,
        expected_patch_digest: str,
        idempotency_key: str,
        topology_patch_grant_id: str,
    ) -> OrganizationTopologyPatchApplyResult:
        self._validate_preview_envelope(
            preview=preview,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            expected_revision=expected_revision,
            expected_patch_digest=expected_patch_digest,
        )
        if not idempotency_key or not topology_patch_grant_id:
            raise OrganizationTopologyPatchError("organization_patch_apply_binding_missing", public_status=400)

        request_digest = canonical_sha256(
            {
                "patch_digest": preview.patch_digest,
                "idempotency_key": idempotency_key,
                "topology_patch_grant_id": topology_patch_grant_id,
                "principal_id": principal_id,
            }
        )
        result: OrganizationTopologyPatchApplyResult | None = None
        with self._uow_factory() as uow:
            grant = uow.topology_patch_grants.get_scoped(
                tenant_id,
                project_id,
                organization_id,
                topology_patch_grant_id,
                for_update=True,
            )
            if grant is None:
                raise OrganizationTopologyPatchError(
                    "organization_patch_grant_invalid",
                    public_status=403,
                )
            self._validate_grant_binding(
                grant,
                preview=preview,
                principal_id=principal_id,
            )
            existing = uow.operations.get_by_idempotency_key(
                tenant_id,
                project_id,
                "topology_patch_apply",
                idempotency_key,
                for_update=True,
            )
            if existing is not None:
                if (
                    existing.request_digest != request_digest
                    or existing.plan_digest != preview.patch_digest
                    or grant.consumed_idempotency_key != idempotency_key
                    or grant.consumed_request_digest != request_digest
                    or grant.consumed_at is None
                ):
                    raise OrganizationTopologyPatchError("organization_patch_idempotency_conflict")
                if existing.status != "applied" or not existing.result_json:
                    raise OrganizationTopologyPatchError("organization_patch_apply_in_progress")
                result = OrganizationTopologyPatchApplyResult.model_validate({**existing.result_json, "replayed": True})
            else:
                membership = self._active_admin_membership(
                    uow,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    principal_id=principal_id,
                )
                if membership is None:
                    raise OrganizationTopologyPatchError(
                        "organization_patch_admin_authority_invalid",
                        public_status=403,
                    )
                now = self._clock()
                if (
                    grant.revoked_at is not None
                    or grant.consumed_at is not None
                    or float(grant.expires_at) < now
                    or preview.expires_at_epoch < now
                ):
                    raise OrganizationTopologyPatchError(
                        "organization_patch_grant_expired_or_consumed",
                        public_status=403,
                    )
                current = self._authoritative_preview(
                    uow,
                    preview=preview,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    principal_id=principal_id,
                )
                self._require_unchanged_preview(preview, current)
                state = current[1]
                operation = OrganizationOperationDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    operation_kind="topology_patch_apply",
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    plan_digest=preview.patch_digest,
                    expected_revision=preview.expected_revision,
                    status="pending",
                )
                uow.operations.add(operation)
                self._fault_injector("operation")
                self._stage_operations(
                    uow,
                    state,
                    preview.operations,
                    operation_key=preview.patch_digest,
                    principal_id=principal_id,
                )
                uow.flush()
                self._fault_injector("entities")
                snapshot_hash = self._stage_snapshot(uow, state, preview)
                state.organization.lock_version += 1
                state.organization.updated_at = self._clock()
                uow.instances.add(state.organization)
                result = OrganizationTopologyPatchApplyResult(
                    organization_id=organization_id,
                    definition_revision=state.organization.definition_revision,
                    snapshot_hash=snapshot_hash,
                    patch_digest=preview.patch_digest,
                    applied_operations=len(preview.operations),
                )
                uow.audit_outbox.add(
                    OrganizationAuditOutboxDB(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        organization_id=organization_id,
                        event_key=f"organization-topology-patched:{operation.operation_id}",
                        event_kind="organization.topology_patched.v1",
                        payload_json={
                            **result.model_dump(mode="json"),
                            "principal_id": principal_id,
                            "topology_patch_grant_id": topology_patch_grant_id,
                        },
                    )
                )
                operation.status = "applied"
                operation.result_ref = snapshot_hash
                operation.result_json = result.model_dump(mode="json")
                operation.applied_at = self._clock()
                uow.operations.add(operation)
                grant.consumed_at = self._clock()
                grant.revoked_at = grant.consumed_at
                grant.consumed_idempotency_key = idempotency_key
                grant.consumed_request_digest = request_digest
                uow.topology_patch_grants.add(grant)
                self._fault_injector("audit_outbox")
        if result is None:
            raise OrganizationTopologyPatchError("organization_patch_result_missing")
        return result

    @staticmethod
    def _grant_result(
        grant: OrganizationTopologyPatchGrantDB,
        *,
        replayed: bool = False,
    ) -> OrganizationTopologyPatchGrantResult:
        return OrganizationTopologyPatchGrantResult(
            grant_id=grant.grant_id,
            tenant_id=grant.tenant_id,
            project_id=grant.project_id,
            organization_id=grant.organization_id,
            principal_id=grant.principal_id,
            patch_digest=grant.patch_digest,
            policy_hash=grant.policy_hash,
            limit_hash=grant.limit_hash,
            expected_revision=grant.expected_revision,
            expires_at=grant.expires_at,
            replayed=replayed,
        )

    @staticmethod
    def _validate_grant_binding(
        grant: OrganizationTopologyPatchGrantDB,
        *,
        preview: OrganizationTopologyPatchPreview,
        principal_id: str,
    ) -> None:
        if (
            grant.tenant_id != preview.tenant_id
            or grant.project_id != preview.project_id
            or grant.organization_id != preview.organization_id
            or grant.principal_id != principal_id
            or grant.patch_digest != preview.patch_digest
            or grant.policy_hash != preview.effective_policy_hash
            or grant.limit_hash != preview.effective_limit_profile_hash
            or grant.expected_revision != preview.expected_revision
        ):
            raise OrganizationTopologyPatchError(
                "organization_patch_grant_binding_mismatch",
                public_status=403,
            )

    @staticmethod
    def _validate_preview_envelope(
        *,
        preview: OrganizationTopologyPatchPreview,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        expected_revision: str,
        expected_patch_digest: str,
    ) -> None:
        if canonical_sha256(preview.digest_payload()) != preview.patch_digest:
            raise OrganizationTopologyPatchError("organization_patch_preview_tampered")
        if preview.patch_digest != expected_patch_digest:
            raise OrganizationTopologyPatchError(
                "organization_patch_digest_header_mismatch",
                public_status=412,
            )
        if (
            preview.tenant_id != tenant_id
            or preview.project_id != project_id
            or preview.organization_id != organization_id
            or preview.principal_id != principal_id
        ):
            raise OrganizationTopologyPatchError(
                "organization_patch_scope_mismatch",
                public_status=403,
            )
        if preview.expected_revision != expected_revision:
            raise OrganizationTopologyPatchError(
                "organization_patch_if_match_mismatch",
                public_status=412,
            )
        if not preview.applicable:
            raise OrganizationTopologyPatchError(
                "organization_patch_preview_blocked",
                public_status=422,
            )

    def _active_admin_membership(
        self,
        uow,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
    ):
        return next(
            (
                row
                for row in uow.memberships.list_for_organization(
                    tenant_id,
                    project_id,
                    organization_id,
                )
                if row.principal_id == principal_id
                and row.membership_kind == "organization_admin"
                and (row.expires_at is None or float(row.expires_at) >= self._clock())
            ),
            None,
        )

    def _authoritative_preview(
        self,
        uow,
        *,
        preview: OrganizationTopologyPatchPreview,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
    ) -> tuple[OrganizationTopologyPatchPreview, OrganizationPatchState]:
        agent_ids = {row.agent_id for row in preview.operations if isinstance(row, TopologyAssignOperation)}
        state = self._reader.load_state(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            agent_ids=agent_ids,
            session=uow.session,
            for_update=True,
        )
        if state is None:
            raise OrganizationTopologyPatchError(
                "organization_not_found",
                public_status=404,
            )
        transaction_definitions = uow.definitions
        if self._catalog is not None:
            transaction_definitions = FileCatalogDefinitionRepositoryAdapter(
                transaction_definitions,
                self._catalog,
                uow.session,
            )
        limits = self._resolve_limits(
            state,
            port=SqlOrganizationLimitProfileAdapter(transaction_definitions),
        )
        current = self._evaluate(
            state=state,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            document=OrganizationTopologyPatchDocument(
                expected_revision=preview.expected_revision,
                operations=preview.operations,
            ),
            limits=limits,
            expires_at_epoch=preview.expires_at_epoch,
        ).preview
        return current, state

    @staticmethod
    def _require_unchanged_preview(
        preview: OrganizationTopologyPatchPreview,
        current: tuple[OrganizationTopologyPatchPreview, OrganizationPatchState],
    ) -> None:
        authoritative = current[0]
        if (
            authoritative.patch_digest != preview.patch_digest
            or authoritative.effective_policy_hash != preview.effective_policy_hash
            or authoritative.effective_limit_profile_hash != preview.effective_limit_profile_hash
        ):
            raise OrganizationTopologyPatchError(
                "organization_patch_preview_stale",
                public_status=412,
            )

    def _resolve_limits(
        self,
        state: OrganizationPatchState,
        *,
        port: OrganizationLimitProfilePort | None = None,
    ) -> OrganizationLimitProfile:
        reference = str(state.organization.effective_limit_profile_ref or "")
        if "@" not in reference:
            reference = f"{reference}@{state.organization.effective_limit_profile_revision}"
        return (port or self._limit_profiles).resolve_limit_profile(
            tenant_id=state.organization.tenant_id,
            project_id=state.organization.project_id,
            policy_ref=reference,
        )

    def _evaluate(
        self, *, state, tenant_id, project_id, organization_id, principal_id, document, limits, expires_at_epoch
    ) -> TopologyPatchEvaluation:
        return self._evaluator.evaluate(
            state=state,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            document=document,
            limits=limits,
            expires_at_epoch=expires_at_epoch,
        )

    def _stage_operations(self, uow, state, operations, *, operation_key: str, principal_id: str):
        return self._stager.stage_operations(
            uow,
            state,
            operations,
            operation_key=operation_key,
            principal_id=principal_id,
        )

    def _stage_snapshot(self, uow, state, preview):
        return self._stager.stage_snapshot(uow, state, preview)


__all__ = [
    "OrganizationPatchReadPort",
    "OrganizationPatchState",
    "OrganizationTopologyApplyService",
    "OrganizationTopologyPatchApplyResult",
    "OrganizationTopologyPatchDocument",
    "OrganizationTopologyPatchError",
    "OrganizationTopologyPatchGrantResult",
    "OrganizationTopologyPatchPreview",
    "SqlOrganizationPatchReadAdapter",
    "TopologyAddOperation",
    "TopologyAddValue",
    "TopologyAssignOperation",
    "TopologyConnectOperation",
    "TopologyMigrationTarget",
    "TopologyPatchOperation",
    "TopologyRemoveOperation",
    "TopologyReparentOperation",
]
