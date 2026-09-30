"""Hub domain service for canonical source-control persistence.

Records and derivations live in ``agent.models.source_control_persistence``;
the repository ports live in ``agent.ports.source_control_persistence``. Both
are re-exported here so existing imports keep resolving to the same objects.
"""

from __future__ import annotations

import time

from agent.models.source_control_persistence import (
    ActivationReconciliationResult,
    ActiveKnowledgeIndexEventRecord,
    ActiveKnowledgeIndexRecord,
    IndexLifecycleProjection,
    KnowledgeIndexBindingRecord,
    KnowledgeIndexRunBindingRecord,
    SourceAccessGrantAuditRecord,
    SourceAccessGrantPreview,
    SourceAccessGrantRecord,
    SourceConnectionRecord,
    SourceControlPersistenceError,
    SourceRevisionRecord,
    derive_active_index_id,
    derive_grant_family_id,
    derive_index_lifecycle,
)
from agent.ports.source_control_persistence import (
    SourceCatalogRepositoryPort,
    SourceGrantRepositoryPort,
    SourceIndexLifecycleRepositoryPort,
)
from ananta_contracts.source_control import (
    ConnectionState,
    GrantState,
    SourceAccessGrant,
    SourceConnection,
    SourceRevision,
)


class SourceControlPersistenceService:
    """Hub-owned use cases composed over segregated persistence ports."""

    def __init__(
        self,
        *,
        catalog: SourceCatalogRepositoryPort,
        grants: SourceGrantRepositoryPort,
        indexes: SourceIndexLifecycleRepositoryPort,
        clock: callable = time.time,
    ) -> None:
        self._catalog = catalog
        self._grants = grants
        self._indexes = indexes
        self._clock = clock

    def register_connection(
        self, contract: SourceConnection
    ) -> SourceConnectionRecord:
        return self._catalog.save_connection(contract)

    def transition_connection(
        self,
        contract: SourceConnection,
        *,
        target_state: ConnectionState,
        expected_lock_version: int,
    ) -> SourceConnectionRecord:
        return self._catalog.transition_connection(
            tenant_id=contract.tenant_id,
            project_id=contract.project_id,
            owner_id=contract.owner_id,
            connection_id=contract.connection_id,
            target_state=target_state,
            expected_lock_version=expected_lock_version,
        )

    def append_revision(
        self, contract: SourceRevision
    ) -> SourceRevisionRecord:
        return self._catalog.append_revision(contract)

    def create_grant(
        self,
        contract: SourceAccessGrant,
        *,
        owner_id: str,
    ) -> SourceAccessGrantRecord:
        return self._grants.save_grant(
            contract,
            owner_id=owner_id,
            grant_family_id=derive_grant_family_id(contract),
        )

    def preview_grant(
        self,
        record: SourceAccessGrantRecord,
    ) -> SourceAccessGrantPreview:
        contract = record.contract
        return self._grants.preview_grant(
            tenant_id=contract.tenant_id,
            project_id=contract.project_id,
            owner_id=record.owner_id,
            grant_id=contract.grant_id,
            source_revision_id=contract.source_revision_id,
            destination_id=contract.destination_id,
            operation=contract.operation,
            transformation=contract.transformation,
            at_epoch=float(self._clock()),
        )

    def transition_grant(
        self,
        record: SourceAccessGrantRecord,
        *,
        target_state: GrantState,
        expected_lock_version: int,
        reason_code: str,
    ) -> SourceAccessGrantRecord:
        contract = record.contract
        return self._grants.transition_grant(
            tenant_id=contract.tenant_id,
            project_id=contract.project_id,
            owner_id=record.owner_id,
            grant_id=contract.grant_id,
            target_state=target_state,
            expected_lock_version=expected_lock_version,
            reason_code=reason_code,
        )

    def rollback_grant(
        self,
        previous: SourceAccessGrantRecord,
        replacement: SourceAccessGrant,
        *,
        expected_previous_lock_version: int,
        reason_code: str,
    ) -> SourceAccessGrantRecord:
        return self._grants.rollback_grant(
            previous_grant_id=previous.contract.grant_id,
            replacement=replacement,
            owner_id=previous.owner_id,
            grant_family_id=previous.grant_family_id,
            expected_previous_lock_version=expected_previous_lock_version,
            reason_code=reason_code,
        )

    def bind_knowledge_index(
        self,
        *,
        knowledge_index_id: str,
        revision: SourceRevision,
        policy_snapshot_id: str,
        policy_snapshot_digest: str,
        index_contract_version: str,
    ) -> KnowledgeIndexBindingRecord:
        now = float(self._clock())
        return self._indexes.save_index_binding(
            KnowledgeIndexBindingRecord(
                knowledge_index_id=knowledge_index_id,
                tenant_id=revision.tenant_id,
                project_id=revision.project_id,
                owner_id=revision.owner_id,
                connection_id=revision.connection_id,
                source_revision_id=revision.source_revision_id,
                policy_snapshot_id=policy_snapshot_id,
                policy_snapshot_digest=policy_snapshot_digest,
                index_contract_version=index_contract_version,
                status="pending",
                artifact_manifest_digest=None,
                activation_requested=False,
                lock_version=1,
                created_at_epoch=now,
                updated_at_epoch=now,
            )
        )

    def bind_index_run(
        self,
        *,
        index_run_id: str,
        index: KnowledgeIndexBindingRecord,
    ) -> KnowledgeIndexRunBindingRecord:
        return self._indexes.save_index_run_binding(
            KnowledgeIndexRunBindingRecord(
                index_run_id=index_run_id,
                knowledge_index_id=index.knowledge_index_id,
                tenant_id=index.tenant_id,
                project_id=index.project_id,
                owner_id=index.owner_id,
                source_revision_id=index.source_revision_id,
                policy_snapshot_id=index.policy_snapshot_id,
                policy_snapshot_digest=index.policy_snapshot_digest,
                status="pending",
                artifact_manifest_digest=None,
                artifacts_verified=False,
                lock_version=1,
                created_at_epoch=float(self._clock()),
                completed_at_epoch=None,
            )
        )

    def complete_index_run(
        self,
        run: KnowledgeIndexRunBindingRecord,
        index: KnowledgeIndexBindingRecord,
        *,
        artifact_manifest_digest: str,
    ) -> tuple[
        KnowledgeIndexBindingRecord,
        KnowledgeIndexRunBindingRecord,
    ]:
        return self._indexes.complete_index_run(
            tenant_id=index.tenant_id,
            project_id=index.project_id,
            owner_id=index.owner_id,
            index_run_id=run.index_run_id,
            expected_run_lock_version=run.lock_version,
            expected_index_lock_version=index.lock_version,
            artifact_manifest_digest=artifact_manifest_digest,
            completed_at_epoch=float(self._clock()),
        )

    def activate_index(
        self,
        index: KnowledgeIndexBindingRecord,
        *,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
        expected_generation: int,
    ) -> ActiveKnowledgeIndexRecord:
        return self._indexes.activate_index(
            tenant_id=index.tenant_id,
            project_id=index.project_id,
            owner_id=index.owner_id,
            connection_id=index.connection_id,
            knowledge_index_id=index.knowledge_index_id,
            current_source_revision_id=current_source_revision_id,
            current_policy_snapshot_digest=current_policy_snapshot_digest,
            expected_generation=expected_generation,
            action="activate",
        )

    def rollback_index(
        self,
        index: KnowledgeIndexBindingRecord,
        *,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
        expected_generation: int,
    ) -> ActiveKnowledgeIndexRecord:
        projection = self._indexes.project_index_lifecycle(
            tenant_id=index.tenant_id,
            project_id=index.project_id,
            owner_id=index.owner_id,
            connection_id=index.connection_id,
            knowledge_index_id=index.knowledge_index_id,
            current_source_revision_id=current_source_revision_id,
            current_policy_snapshot_digest=current_policy_snapshot_digest,
        )
        if not projection.rollback_candidate:
            raise SourceControlPersistenceError(
                "source_control_index_not_rollback_candidate"
            )
        return self._indexes.activate_index(
            tenant_id=index.tenant_id,
            project_id=index.project_id,
            owner_id=index.owner_id,
            connection_id=index.connection_id,
            knowledge_index_id=index.knowledge_index_id,
            current_source_revision_id=current_source_revision_id,
            current_policy_snapshot_digest=current_policy_snapshot_digest,
            expected_generation=expected_generation,
            action="rollback",
        )


__all__ = [
    "ActivationReconciliationResult",
    "ActiveKnowledgeIndexEventRecord",
    "ActiveKnowledgeIndexRecord",
    "IndexLifecycleProjection",
    "KnowledgeIndexBindingRecord",
    "KnowledgeIndexRunBindingRecord",
    "SourceAccessGrantAuditRecord",
    "SourceAccessGrantPreview",
    "SourceAccessGrantRecord",
    "SourceCatalogRepositoryPort",
    "SourceConnectionRecord",
    "SourceControlPersistenceError",
    "SourceControlPersistenceService",
    "SourceGrantRepositoryPort",
    "SourceIndexLifecycleRepositoryPort",
    "SourceRevisionRecord",
    "derive_active_index_id",
    "derive_grant_family_id",
    "derive_index_lifecycle",
]
