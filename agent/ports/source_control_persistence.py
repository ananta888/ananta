"""Segregated persistence ports for canonical source-control state."""

from __future__ import annotations

from typing import Protocol

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
    SourceRevisionRecord,
)
from ananta_contracts.source_control import (
    ConnectionState,
    GrantOperation,
    GrantState,
    GrantTransformation,
    SourceAccessGrant,
    SourceConnection,
    SourceRevision,
)


class SourceCatalogRepositoryPort(Protocol):
    def save_connection(
        self, contract: SourceConnection
    ) -> SourceConnectionRecord: ...

    def get_connection(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
    ) -> SourceConnectionRecord | None: ...

    def transition_connection(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        target_state: ConnectionState,
        expected_lock_version: int,
    ) -> SourceConnectionRecord: ...

    def append_revision(
        self, contract: SourceRevision
    ) -> SourceRevisionRecord: ...

    def get_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        source_revision_id: str,
    ) -> SourceRevisionRecord | None: ...


class SourceGrantRepositoryPort(Protocol):
    def save_grant(
        self,
        contract: SourceAccessGrant,
        *,
        owner_id: str,
        grant_family_id: str,
        rollback_of_grant_id: str | None = None,
    ) -> SourceAccessGrantRecord: ...

    def preview_grant(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        grant_id: str,
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
        at_epoch: float,
    ) -> SourceAccessGrantPreview: ...

    def transition_grant(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        grant_id: str,
        target_state: GrantState,
        expected_lock_version: int,
        reason_code: str,
    ) -> SourceAccessGrantRecord: ...

    def rollback_grant(
        self,
        *,
        previous_grant_id: str,
        replacement: SourceAccessGrant,
        owner_id: str,
        grant_family_id: str,
        expected_previous_lock_version: int,
        reason_code: str,
    ) -> SourceAccessGrantRecord: ...

    def list_grant_audit(
        self, *, grant_id: str
    ) -> tuple[SourceAccessGrantAuditRecord, ...]: ...


class SourceIndexLifecycleRepositoryPort(Protocol):
    def save_index_binding(
        self, record: KnowledgeIndexBindingRecord
    ) -> KnowledgeIndexBindingRecord: ...

    def save_index_run_binding(
        self, record: KnowledgeIndexRunBindingRecord
    ) -> KnowledgeIndexRunBindingRecord: ...

    def complete_index_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        index_run_id: str,
        expected_run_lock_version: int,
        expected_index_lock_version: int,
        artifact_manifest_digest: str,
        completed_at_epoch: float,
    ) -> tuple[
        KnowledgeIndexBindingRecord,
        KnowledgeIndexRunBindingRecord,
    ]: ...

    def activate_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        knowledge_index_id: str,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
        expected_generation: int,
        action: str,
    ) -> ActiveKnowledgeIndexRecord: ...

    def get_active_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
    ) -> ActiveKnowledgeIndexRecord | None: ...

    def project_index_lifecycle(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        knowledge_index_id: str,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
    ) -> IndexLifecycleProjection: ...

    def reconcile_activation(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
    ) -> ActivationReconciliationResult: ...

    def list_activation_events(
        self, *, active_index_id: str
    ) -> tuple[ActiveKnowledgeIndexEventRecord, ...]: ...



__all__ = [
    "SourceCatalogRepositoryPort",
    "SourceGrantRepositoryPort",
    "SourceIndexLifecycleRepositoryPort",
]
