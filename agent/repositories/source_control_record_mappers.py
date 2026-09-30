"""Pure row/record mapping for canonical source-control persistence.

The SQLModel rows in ``agent.db_models.source_control`` and the immutable
records in ``agent.models.source_control_persistence`` evolve independently
of the SQL adapters that move them. Keeping the mapping in one side-effect
free module lets every source-control store share it without inheriting
from each other.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    ActiveKnowledgeIndexEventDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantAuditDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceConnectionSelectorDB,
    SourceRevisionDB,
)
from agent.models.source_control_connection_binding import (
    SourceConnectionSelectorBinding,
)
from agent.models.source_control_persistence import (
    ActiveKnowledgeIndexEventRecord,
    ActiveKnowledgeIndexRecord,
    KnowledgeIndexBindingRecord,
    KnowledgeIndexRunBindingRecord,
    SourceAccessGrantAuditRecord,
    SourceAccessGrantRecord,
    SourceConnectionRecord,
    SourceControlPersistenceError,
    SourceRevisionRecord,
)
from ananta_contracts.source_control import (
    SourceAccessGrant,
    SourceConnection,
    SourceRevision,
)


def stable_id(prefix: str, coordinates: dict[str, object]) -> str:
    canonical = json.dumps(
        coordinates,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"{prefix}_{hashlib.sha256(canonical).hexdigest()}"


def epoch_datetime(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def require_sha256_digest(value: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise SourceControlPersistenceError(
            "source_control_digest_invalid"
        )


def new_connection_row(
    contract: SourceConnection, *, now: float
) -> SourceConnectionDB:
    return SourceConnectionDB(
        connection_id=contract.connection_id,
        tenant_id=contract.tenant_id,
        project_id=contract.project_id,
        owner_id=contract.owner_id,
        connector_type=contract.connector_type.value,
        connection_identity_digest=contract.connection_identity_digest,
        display_name=contract.display_name,
        sensitivity=contract.sensitivity.value,
        state=contract.state.value,
        lock_version=1,
        created_at_epoch=contract.created_at.timestamp(),
        updated_at_epoch=now,
    )


def new_grant_row(
    contract: SourceAccessGrant,
    *,
    owner_id: str,
    grant_family_id: str,
    rollback_of_grant_id: str | None,
    updated_at_epoch: float,
) -> SourceAccessGrantDB:
    return SourceAccessGrantDB(
        grant_id=contract.grant_id,
        grant_family_id=grant_family_id,
        grant_version=contract.version,
        tenant_id=contract.tenant_id,
        project_id=contract.project_id,
        owner_id=owner_id,
        source_revision_id=contract.source_revision_id,
        destination_id=contract.destination_id,
        operation=contract.operation.value,
        transformation=contract.transformation.value,
        purpose=contract.purpose,
        policy_version=contract.policy_version,
        state=contract.state.value,
        issued_at_epoch=contract.issued_at.timestamp(),
        expires_at_epoch=contract.expires_at.timestamp(),
        rollback_of_grant_id=rollback_of_grant_id,
        lock_version=1,
        updated_at_epoch=updated_at_epoch,
    )


def selector_binding(
    row: SourceConnectionSelectorDB,
) -> SourceConnectionSelectorBinding:
    binding = SourceConnectionSelectorBinding(
        connection_id=row.connection_id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        owner_id=row.owner_id,
        public_connector_type=row.public_connector_type,
        implementation_connector_type=(
            row.implementation_connector_type
        ),
        selector_kind=row.selector_kind,
        selector_id=row.selector_id,
        relative_path=row.relative_path,
        repository_identifier=row.repository_identifier,
    )
    if binding.binding_digest != row.binding_digest:
        raise SourceControlPersistenceError(
            "source_control_connection_selector_digest_mismatch"
        )
    return binding


def connection_record(row: SourceConnectionDB) -> SourceConnectionRecord:
    return SourceConnectionRecord(
        contract=SourceConnection(
            schema="ananta.source-control.source-connection.v1",
            authority="hub",
            connection_id=row.connection_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            owner_id=row.owner_id,
            connector_type=row.connector_type,
            connection_identity_digest=row.connection_identity_digest,
            display_name=row.display_name,
            sensitivity=row.sensitivity,
            state=row.state,
            created_at=epoch_datetime(row.created_at_epoch),
        ),
        lock_version=row.lock_version,
        updated_at_epoch=row.updated_at_epoch,
        disabled_at_epoch=row.disabled_at_epoch,
        tombstoned_at_epoch=row.tombstoned_at_epoch,
    )


def revision_record(row: SourceRevisionDB) -> SourceRevisionRecord:
    return SourceRevisionRecord(
        contract=SourceRevision(
            schema="ananta.source-control.source-revision.v1",
            authority="hub",
            source_revision_id=row.source_revision_id,
            connection_id=row.connection_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            owner_id=row.owner_id,
            connector_type=row.connector_type,
            sensitivity=row.sensitivity,
            revision_token=row.revision_token,
            revision_digest=row.revision_digest,
            content_manifest_id=row.content_manifest_id,
            content_manifest_digest=row.content_manifest_digest,
            admission_state=row.admission_state,
            captured_at=epoch_datetime(row.captured_at_epoch),
        )
    )


def grant_record(row: SourceAccessGrantDB) -> SourceAccessGrantRecord:
    return SourceAccessGrantRecord(
        contract=SourceAccessGrant(
            schema="ananta.source-control.source-access-grant.v1",
            authority="hub",
            grant_id=row.grant_id,
            version=row.grant_version,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            source_revision_id=row.source_revision_id,
            destination_id=row.destination_id,
            operation=row.operation,
            transformation=row.transformation,
            purpose=row.purpose,
            policy_version=row.policy_version,
            state=row.state,
            issued_at=epoch_datetime(row.issued_at_epoch),
            expires_at=epoch_datetime(row.expires_at_epoch),
        ),
        owner_id=row.owner_id,
        grant_family_id=row.grant_family_id,
        lock_version=row.lock_version,
        updated_at_epoch=row.updated_at_epoch,
        rollback_of_grant_id=row.rollback_of_grant_id,
    )


def grant_audit_record(
    row: SourceAccessGrantAuditDB,
) -> SourceAccessGrantAuditRecord:
    return SourceAccessGrantAuditRecord(
        audit_id=row.audit_id,
        grant_id=row.grant_id,
        action=row.action,
        from_state=row.from_state,
        to_state=row.to_state,
        reason_code=row.reason_code,
        grant_lock_version=row.grant_lock_version,
        occurred_at_epoch=row.occurred_at_epoch,
    )


def index_binding_record(
    row: KnowledgeIndexSourceBindingDB,
) -> KnowledgeIndexBindingRecord:
    return KnowledgeIndexBindingRecord(
        knowledge_index_id=row.knowledge_index_id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        owner_id=row.owner_id,
        connection_id=row.connection_id,
        source_revision_id=row.source_revision_id,
        policy_snapshot_id=row.policy_snapshot_id,
        policy_snapshot_digest=row.policy_snapshot_digest,
        index_contract_version=row.index_contract_version,
        status=row.status,
        artifact_manifest_digest=row.artifact_manifest_digest,
        activation_requested=row.activation_requested,
        lock_version=row.lock_version,
        created_at_epoch=row.created_at_epoch,
        updated_at_epoch=row.updated_at_epoch,
    )


def index_run_binding_record(
    row: KnowledgeIndexRunSourceBindingDB,
) -> KnowledgeIndexRunBindingRecord:
    return KnowledgeIndexRunBindingRecord(
        index_run_id=row.index_run_id,
        knowledge_index_id=row.knowledge_index_id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        owner_id=row.owner_id,
        source_revision_id=row.source_revision_id,
        policy_snapshot_id=row.policy_snapshot_id,
        policy_snapshot_digest=row.policy_snapshot_digest,
        status=row.status,
        artifact_manifest_digest=row.artifact_manifest_digest,
        artifacts_verified=row.artifacts_verified,
        lock_version=row.lock_version,
        created_at_epoch=row.created_at_epoch,
        completed_at_epoch=row.completed_at_epoch,
    )


def active_index_record(
    row: ActiveKnowledgeIndexDB,
) -> ActiveKnowledgeIndexRecord:
    return ActiveKnowledgeIndexRecord(
        active_index_id=row.active_index_id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        owner_id=row.owner_id,
        connection_id=row.connection_id,
        source_revision_id=row.source_revision_id,
        policy_snapshot_digest=row.policy_snapshot_digest,
        knowledge_index_id=row.knowledge_index_id,
        previous_knowledge_index_id=row.previous_knowledge_index_id,
        generation=row.generation,
        updated_at_epoch=row.updated_at_epoch,
    )


def activation_event_record(
    row: ActiveKnowledgeIndexEventDB,
) -> ActiveKnowledgeIndexEventRecord:
    return ActiveKnowledgeIndexEventRecord(
        event_id=row.event_id,
        active_index_id=row.active_index_id,
        action=row.action,
        from_knowledge_index_id=row.from_knowledge_index_id,
        to_knowledge_index_id=row.to_knowledge_index_id,
        generation=row.generation,
        occurred_at_epoch=row.occurred_at_epoch,
    )
