"""Additive persistence models for canonical source-control state."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Column, Integer, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class SourceConnectionDB(SQLModel, table=True):
    __tablename__ = "source_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "connection_id",
            name="uq_source_connections_scope_id",
        ),
    )

    connection_id: str = Field(primary_key=True, max_length=69)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    connector_type: str = Field(index=True, max_length=64)
    connection_identity_digest: str = Field(max_length=64)
    display_name: str = Field(max_length=200)
    sensitivity: str = Field(index=True, max_length=32)
    state: str = Field(index=True, max_length=32)
    lock_version: int = Field(default=1, ge=1)
    created_at_epoch: float
    updated_at_epoch: float
    disabled_at_epoch: Optional[float] = None
    tombstoned_at_epoch: Optional[float] = None


class SourceConnectionSelectorDB(SQLModel, table=True):
    """Secret-free selector bound one-to-one to a canonical connection."""

    __tablename__ = "source_connection_selectors"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "owner_id",
            "public_connector_type",
            "selector_id",
            "relative_path",
            name="uq_source_connection_selectors_coordinates",
        ),
    )

    connection_id: str = Field(
        primary_key=True,
        foreign_key="source_connections.connection_id",
        max_length=69,
    )
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    public_connector_type: str = Field(index=True, max_length=32)
    implementation_connector_type: str = Field(index=True, max_length=32)
    selector_kind: str = Field(index=True, max_length=16)
    selector_id: str = Field(index=True, max_length=192)
    relative_path: Optional[str] = Field(default=None, max_length=512)
    repository_identifier: Optional[str] = Field(default=None, max_length=201)
    binding_digest: str = Field(index=True, max_length=64)
    created_at_epoch: float


class SourceRevisionDB(SQLModel, table=True):
    __tablename__ = "source_revisions"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "revision_digest",
            name="uq_source_revisions_connection_digest",
        ),
    )

    source_revision_id: str = Field(primary_key=True, max_length=69)
    connection_id: str = Field(
        foreign_key="source_connections.connection_id",
        index=True,
        max_length=69,
    )
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    connector_type: str = Field(index=True, max_length=64)
    sensitivity: str = Field(index=True, max_length=32)
    revision_token: str = Field(max_length=256)
    revision_digest: str = Field(index=True, max_length=64)
    content_manifest_id: str = Field(index=True, max_length=73)
    content_manifest_digest: str = Field(max_length=64)
    admission_state: str = Field(index=True, max_length=32)
    captured_at_epoch: float


class SourceAccessGrantDB(SQLModel, table=True):
    __tablename__ = "source_access_grants"
    __table_args__ = (
        UniqueConstraint(
            "grant_family_id",
            "grant_version",
            name="uq_source_access_grants_family_version",
        ),
    )

    grant_id: str = Field(primary_key=True, max_length=70)
    grant_family_id: str = Field(index=True, max_length=80)
    grant_version: int = Field(ge=1)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    source_revision_id: str = Field(
        foreign_key="source_revisions.source_revision_id",
        index=True,
        max_length=69,
    )
    destination_id: str = Field(index=True, max_length=68)
    operation: str = Field(index=True, max_length=32)
    transformation: str = Field(index=True, max_length=32)
    purpose: str = Field(max_length=128)
    policy_version: str = Field(index=True, max_length=128)
    policy_snapshot_digest: Optional[str] = Field(
        default=None,
        index=True,
        max_length=64,
    )
    state: str = Field(index=True, max_length=32)
    issued_at_epoch: float
    expires_at_epoch: float = Field(index=True)
    rollback_of_grant_id: Optional[str] = Field(
        default=None,
        index=True,
        max_length=70,
    )
    lock_version: int = Field(default=1, ge=1)
    updated_at_epoch: float


class SourceAccessGrantAuditDB(SQLModel, table=True):
    __tablename__ = "source_access_grant_audit"

    audit_id: str = Field(primary_key=True, max_length=70)
    grant_id: str = Field(index=True, max_length=70)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    action: str = Field(index=True, max_length=32)
    from_state: Optional[str] = Field(default=None, max_length=32)
    to_state: Optional[str] = Field(default=None, max_length=32)
    reason_code: str = Field(max_length=128)
    grant_lock_version: int = Field(ge=1)
    occurred_at_epoch: float


class KnowledgeIndexSourceBindingDB(SQLModel, table=True):
    __tablename__ = "knowledge_index_source_bindings"

    knowledge_index_id: str = Field(primary_key=True, max_length=128)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    connection_id: str = Field(
        foreign_key="source_connections.connection_id",
        index=True,
        max_length=69,
    )
    source_revision_id: str = Field(
        foreign_key="source_revisions.source_revision_id",
        index=True,
        max_length=69,
    )
    policy_snapshot_id: str = Field(index=True, max_length=128)
    policy_snapshot_digest: str = Field(index=True, max_length=64)
    index_contract_version: str = Field(max_length=128)
    status: str = Field(index=True, max_length=32)
    artifact_manifest_digest: Optional[str] = Field(
        default=None,
        max_length=64,
    )
    activation_requested: bool = Field(default=False, index=True)
    lock_version: int = Field(default=1, ge=1)
    created_at_epoch: float
    updated_at_epoch: float


class KnowledgeIndexRunSourceBindingDB(SQLModel, table=True):
    __tablename__ = "knowledge_index_run_source_bindings"

    index_run_id: str = Field(primary_key=True, max_length=128)
    knowledge_index_id: str = Field(
        foreign_key="knowledge_index_source_bindings.knowledge_index_id",
        index=True,
        max_length=128,
    )
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    source_revision_id: str = Field(
        foreign_key="source_revisions.source_revision_id",
        index=True,
        max_length=69,
    )
    policy_snapshot_id: str = Field(index=True, max_length=128)
    policy_snapshot_digest: str = Field(index=True, max_length=64)
    status: str = Field(index=True, max_length=32)
    artifact_manifest_digest: Optional[str] = Field(
        default=None,
        max_length=64,
    )
    artifacts_verified: bool = Field(default=False, index=True)
    lock_version: int = Field(default=1, ge=1)
    created_at_epoch: float
    completed_at_epoch: Optional[float] = None


class ActiveKnowledgeIndexDB(SQLModel, table=True):
    __tablename__ = "active_knowledge_indexes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "connection_id",
            name="uq_active_knowledge_indexes_scope",
        ),
    )

    active_index_id: str = Field(primary_key=True, max_length=72)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    connection_id: str = Field(
        foreign_key="source_connections.connection_id",
        index=True,
        max_length=69,
    )
    source_revision_id: str = Field(
        foreign_key="source_revisions.source_revision_id",
        index=True,
        max_length=69,
    )
    policy_snapshot_digest: str = Field(index=True, max_length=64)
    knowledge_index_id: str = Field(
        foreign_key="knowledge_index_source_bindings.knowledge_index_id",
        index=True,
        max_length=128,
    )
    previous_knowledge_index_id: Optional[str] = Field(
        default=None,
        index=True,
        max_length=128,
    )
    generation: int = Field(default=1, ge=1)
    updated_at_epoch: float


class ActiveKnowledgeIndexEventDB(SQLModel, table=True):
    __tablename__ = "active_knowledge_index_events"
    __table_args__ = (
        UniqueConstraint(
            "active_index_id",
            "generation",
            name="uq_active_knowledge_index_events_generation",
        ),
    )

    event_id: str = Field(primary_key=True, max_length=70)
    active_index_id: str = Field(index=True, max_length=72)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    connection_id: str = Field(index=True, max_length=69)
    action: str = Field(index=True, max_length=32)
    from_knowledge_index_id: Optional[str] = Field(
        default=None,
        max_length=128,
    )
    to_knowledge_index_id: str = Field(max_length=128)
    generation: int = Field(ge=1)
    occurred_at_epoch: float


class SourceControlJobEventOutboxDB(SQLModel, table=True):
    """Content-free source-control event with a DB-assigned durable cursor."""

    __tablename__ = "source_control_job_event_outbox"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            name="uq_source_control_job_event_outbox_event",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "job_id",
            "event_id",
            name="uq_source_control_job_event_outbox_scope_job_event",
        ),
    )

    sequence: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger().with_variant(Integer, "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
    )
    event_id: str = Field(index=True, max_length=70)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    resource_id: str = Field(index=True, max_length=128)
    job_id: str = Field(index=True, max_length=128)
    event_type: str = Field(index=True, max_length=64)
    status: str = Field(index=True, max_length=32)
    reason_code: Optional[str] = Field(default=None, max_length=128)
    trace_id: str = Field(index=True, max_length=128)
    occurred_at_epoch: float
    created_at_epoch: float


class SourceControlOperationDB(SQLModel, table=True):
    """Persistent unique-key claim for source-control API mutations."""

    __tablename__ = "source_control_operations"

    idempotency_key: str = Field(primary_key=True, max_length=96)
    request_digest: str = Field(index=True, max_length=64)
    operation: str = Field(index=True, max_length=64)
    state: str = Field(index=True, max_length=16)
    result_json: Optional[str] = None
    claim_token: Optional[str] = Field(default=None, index=True, max_length=96)
    lease_expires_at_epoch: Optional[float] = Field(default=None, index=True)
    lock_version: int = Field(default=1, ge=1)
    created_at_epoch: float
    updated_at_epoch: float


class SourceControlBulkTargetCheckpointDB(SQLModel, table=True):
    """Per-target durable journal for resumable bulk mutations."""

    __tablename__ = "source_control_bulk_target_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            "target_ordinal",
            name="uq_source_control_bulk_checkpoint_ordinal",
        ),
        UniqueConstraint(
            "idempotency_key",
            "target_digest",
            name="uq_source_control_bulk_checkpoint_target",
        ),
    )

    checkpoint_id: str = Field(primary_key=True, max_length=80)
    idempotency_key: str = Field(
        foreign_key="source_control_operations.idempotency_key",
        index=True,
        max_length=96,
    )
    plan_digest: str = Field(index=True, max_length=64)
    target_ordinal: int = Field(ge=0)
    resource_id: str = Field(index=True, max_length=255)
    target_digest: str = Field(index=True, max_length=64)
    state: str = Field(index=True, max_length=16)
    result_json: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    attempt_count: int = Field(default=1, ge=1)
    created_at_epoch: float
    updated_at_epoch: float


class SourceControlPurgeApprovalDB(SQLModel, table=True):
    """Scope/digest/expiry-bound one-time approval for physical purge."""

    __tablename__ = "source_control_purge_approvals"

    approval_id: str = Field(primary_key=True, max_length=96)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    action: str = Field(index=True, max_length=32)
    object_type: str = Field(index=True, max_length=32)
    object_id: str = Field(index=True, max_length=128)
    request_digest: str = Field(index=True, max_length=64)
    approved_by: str = Field(index=True, max_length=128)
    state: str = Field(index=True, max_length=16)
    claim_id: Optional[str] = Field(default=None, index=True, max_length=96)
    claim_expires_at_epoch: Optional[float] = Field(default=None, index=True)
    issued_at_epoch: float
    expires_at_epoch: float = Field(index=True)
    consumed_at_epoch: Optional[float] = None
    lock_version: int = Field(default=1, ge=1)


class SourceControlIndexReferenceDB(SQLModel, table=True):
    """Canonical active-reference registry used by fail-closed purge."""

    __tablename__ = "source_control_index_references"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "knowledge_index_id",
            "reference_kind",
            "reference_id",
            name="uq_source_control_index_reference",
        ),
    )

    binding_id: str = Field(primary_key=True, max_length=80)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    knowledge_index_id: str = Field(index=True, max_length=128)
    reference_kind: str = Field(index=True, max_length=32)
    reference_id: str = Field(index=True, max_length=255)
    reference_digest: str = Field(max_length=64)
    state: str = Field(index=True, max_length=16)
    expires_at_epoch: Optional[float] = Field(default=None, index=True)
    created_at_epoch: float
    released_at_epoch: Optional[float] = None


class SourceControlArtifactDeletionDB(SQLModel, table=True):
    """Auditable idempotent deletion receipt without persisting local paths."""

    __tablename__ = "source_control_artifact_deletions"

    knowledge_index_id: str = Field(primary_key=True, max_length=128)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    request_digest: str = Field(max_length=64)
    manifest_digest: str = Field(max_length=64)
    approval_id: str = Field(max_length=255)
    quarantine_path_digest: str = Field(max_length=64)
    state: str = Field(index=True, max_length=16)
    created_at_epoch: float
    completed_at_epoch: Optional[float] = None


class SourceControlContentDB(SQLModel, table=True):
    """Bounded immutable content owned by one canonical source revision."""

    __tablename__ = "source_control_contents"

    source_revision_id: str = Field(
        primary_key=True,
        foreign_key="source_revisions.source_revision_id",
        max_length=69,
    )
    connection_id: str = Field(
        foreign_key="source_connections.connection_id",
        index=True,
        max_length=69,
    )
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    content_kind: str = Field(index=True, max_length=32)
    display_name: str = Field(max_length=200)
    media_type: str = Field(max_length=128)
    manifest_digest: str = Field(index=True, max_length=64)
    byte_size: int = Field(ge=0)
    cell_count: int = Field(default=0, ge=0)
    output_bytes: int = Field(default=0, ge=0)
    normalized_content_json: str = Field(
        sa_column=Column(Text, nullable=False)
    )
    created_at_epoch: float


class RemoteSourcePayloadDB(SQLModel, table=True):
    """Content-addressed Hub artifact created by one exact Git fetch."""

    __tablename__ = "remote_source_payloads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "owner_id",
            "connector_type",
            "source_id",
            "connection_ref",
            "repository_key",
            "requested_ref",
            "commit_sha",
            "source_revision_digest",
            name="uq_remote_source_payload_coordinates",
        ),
    )

    payload_digest: str = Field(primary_key=True, max_length=64)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    connector_type: str = Field(index=True, max_length=64)
    source_id: str = Field(index=True, max_length=255)
    connection_ref: str = Field(index=True, max_length=192)
    repository_key: str = Field(default="", max_length=201)
    requested_ref: str = Field(max_length=255)
    commit_sha: str = Field(index=True, max_length=64)
    source_revision_digest: str = Field(index=True, max_length=64)
    git_manifest_digest: str = Field(index=True, max_length=64)
    authorization_binding_digest: str = Field(max_length=64)
    artifact_id: str = Field(max_length=128)
    artifact_filename: str = Field(max_length=128)
    artifact_version: int = Field(default=1, ge=1)
    byte_size: int = Field(ge=0)
    file_count: int = Field(ge=0)
    metrics_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at_epoch: float


class RemoteSourcePayloadBindingDB(SQLModel, table=True):
    """Append-only authority binding from a payload to an admitted revision."""

    __tablename__ = "remote_source_payload_bindings"

    source_revision_id: str = Field(
        primary_key=True,
        foreign_key="source_revisions.source_revision_id",
        max_length=69,
    )
    connection_id: str = Field(
        foreign_key="source_connections.connection_id",
        index=True,
        max_length=69,
    )
    payload_digest: str = Field(
        foreign_key="remote_source_payloads.payload_digest",
        index=True,
        max_length=64,
    )
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    source_revision_digest: str = Field(max_length=64)
    manifest_digest: str = Field(max_length=64)
    bound_at_epoch: float
