"""Ports and Hub domain service for canonical source-control persistence."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Protocol

from ananta_contracts.source_control import (
    ConnectionState,
    GrantOperation,
    GrantState,
    GrantTransformation,
    SourceAccessGrant,
    SourceConnection,
    SourceRevision,
)


class SourceControlPersistenceError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SourceConnectionRecord:
    contract: SourceConnection
    lock_version: int
    updated_at_epoch: float
    disabled_at_epoch: float | None = None
    tombstoned_at_epoch: float | None = None


@dataclass(frozen=True)
class SourceRevisionRecord:
    contract: SourceRevision


@dataclass(frozen=True)
class SourceAccessGrantRecord:
    contract: SourceAccessGrant
    owner_id: str
    grant_family_id: str
    lock_version: int
    updated_at_epoch: float
    rollback_of_grant_id: str | None = None


@dataclass(frozen=True)
class SourceAccessGrantAuditRecord:
    audit_id: str
    grant_id: str
    action: str
    from_state: str | None
    to_state: str | None
    reason_code: str
    grant_lock_version: int
    occurred_at_epoch: float


@dataclass(frozen=True)
class SourceAccessGrantPreview:
    grant_id: str
    allowed: bool
    reason_code: str
    source_revision_id: str
    destination_id: str
    operation: str
    transformation: str
    lock_version: int


@dataclass(frozen=True)
class KnowledgeIndexBindingRecord:
    knowledge_index_id: str
    tenant_id: str
    project_id: str
    owner_id: str
    connection_id: str
    source_revision_id: str
    policy_snapshot_id: str
    policy_snapshot_digest: str
    index_contract_version: str
    status: str
    artifact_manifest_digest: str | None
    activation_requested: bool
    lock_version: int
    created_at_epoch: float
    updated_at_epoch: float


@dataclass(frozen=True)
class KnowledgeIndexRunBindingRecord:
    index_run_id: str
    knowledge_index_id: str
    tenant_id: str
    project_id: str
    owner_id: str
    source_revision_id: str
    policy_snapshot_id: str
    policy_snapshot_digest: str
    status: str
    artifact_manifest_digest: str | None
    artifacts_verified: bool
    lock_version: int
    created_at_epoch: float
    completed_at_epoch: float | None


@dataclass(frozen=True)
class ActiveKnowledgeIndexRecord:
    active_index_id: str
    tenant_id: str
    project_id: str
    owner_id: str
    connection_id: str
    source_revision_id: str
    policy_snapshot_digest: str
    knowledge_index_id: str
    previous_knowledge_index_id: str | None
    generation: int
    updated_at_epoch: float


@dataclass(frozen=True)
class ActiveKnowledgeIndexEventRecord:
    event_id: str
    active_index_id: str
    action: str
    from_knowledge_index_id: str | None
    to_knowledge_index_id: str
    generation: int
    occurred_at_epoch: float


@dataclass(frozen=True)
class IndexLifecycleProjection:
    knowledge_index_id: str
    stale: bool
    policy_changed: bool
    superseded: bool
    rollback_candidate: bool


@dataclass(frozen=True)
class ActivationReconciliationResult:
    repaired: bool
    reason_code: str
    active: ActiveKnowledgeIndexRecord | None


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


def _derived_id(prefix: str, coordinates: dict[str, object]) -> str:
    canonical = json.dumps(
        coordinates,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"{prefix}_{hashlib.sha256(canonical).hexdigest()}"


def derive_grant_family_id(contract: SourceAccessGrant) -> str:
    return _derived_id(
        "grantfam",
        {
            "destination_id": contract.destination_id,
            "operation": contract.operation.value,
            "project_id": contract.project_id,
            "purpose": contract.purpose,
            "source_revision_id": contract.source_revision_id,
            "tenant_id": contract.tenant_id,
            "transformation": contract.transformation.value,
        },
    )


def derive_active_index_id(
    *, tenant_id: str, project_id: str, connection_id: str
) -> str:
    return _derived_id(
        "active",
        {
            "connection_id": connection_id,
            "project_id": project_id,
            "tenant_id": tenant_id,
        },
    )


def derive_index_lifecycle(
    *,
    binding: KnowledgeIndexBindingRecord,
    active: ActiveKnowledgeIndexRecord | None,
    current_source_revision_id: str,
    current_policy_snapshot_digest: str,
    has_verified_run: bool,
) -> IndexLifecycleProjection:
    policy_changed = (
        binding.policy_snapshot_digest != current_policy_snapshot_digest
    )
    stale = (
        binding.source_revision_id != current_source_revision_id
        or policy_changed
    )
    superseded = (
        binding.status == "completed"
        and active is not None
        and active.knowledge_index_id != binding.knowledge_index_id
    )
    return IndexLifecycleProjection(
        knowledge_index_id=binding.knowledge_index_id,
        stale=stale,
        policy_changed=policy_changed,
        superseded=superseded,
        rollback_candidate=superseded and has_verified_run and not stale,
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
