"""Dependency-free source-control persistence records and derivations.

Repositories and Hub services share these value types; they carry no
orchestration logic and import no service module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ananta_contracts.source_control import (
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
    "SourceConnectionRecord",
    "SourceControlPersistenceError",
    "SourceRevisionRecord",
    "derive_active_index_id",
    "derive_grant_family_id",
    "derive_index_lifecycle",
]
