"""Durable, content-free Hub control records for SFU broadcast."""

from __future__ import annotations

import time

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class SfuOperationsSnapshotDB(SQLModel, table=True):
    __tablename__ = "sfu_operations_snapshots"
    __table_args__ = (
        sa.UniqueConstraint(
            "snapshot_version", name="uq_sfu_operations_snapshot_version"
        ),
        sa.CheckConstraint(
            "record_count >= 0", name="ck_sfu_operations_snapshot_record_count"
        ),
        sa.Index(
            "ix_sfu_operations_snapshot_current",
            "created_at",
            "retain_until",
        ),
    )

    id: str = Field(primary_key=True)
    snapshot_version: str = Field(index=True)
    snapshot_digest: str = Field(repr=False)
    record_count: int
    retain_until: float = Field(index=True)
    created_at: float = Field(default_factory=time.time, index=True)


class SfuOperationsSnapshotRecordDB(SQLModel, table=True):
    __tablename__ = "sfu_operations_snapshot_records"
    __table_args__ = (
        sa.UniqueConstraint(
            "snapshot_id", "ordinal", name="uq_sfu_operations_snapshot_ordinal"
        ),
        sa.CheckConstraint(
            "ordinal >= 0 AND cohort_size >= 0 AND queue_depth >= 0",
            name="ck_sfu_operations_record_counts",
        ),
        sa.CheckConstraint(
            "ingress_bytes_per_second >= 0 "
            "AND egress_bytes_per_second >= 0 "
            "AND turn_bytes_per_second >= 0",
            name="ck_sfu_operations_record_rates",
        ),
        sa.Index(
            "ix_sfu_operations_record_scope",
            "snapshot_id",
            "tenant_ref",
            "region",
            "ordinal",
        ),
    )

    id: str = Field(primary_key=True)
    snapshot_id: str = Field(
        sa_column=sa.Column(
            sa.String(),
            sa.ForeignKey("sfu_operations_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    ordinal: int
    observed_at_seconds: float
    tenant_ref: str = Field(index=True)
    region: str = Field(index=True)
    room_ref: str = Field(repr=False, index=True)
    owner_subject: str = Field(repr=False)
    receiver_ref: str = Field(repr=False, index=True)
    cohort_size: int
    group_status: str
    route_status: str
    epoch_class: str
    topology: str
    health: str
    requested_layer: str
    allowed_layer: str
    effective_layer: str
    layer_none_count: int = 0
    layer_low_count: int = 0
    layer_medium_count: int = 0
    layer_high_count: int = 0
    queue_depth: int
    drop_reason: str
    ingress_bytes_per_second: int
    egress_bytes_per_second: int
    turn_bytes_per_second: int
    rekey_status: str
    failover_status: str
    capacity_profile: str
    gate_state: str


class SfuCommandIdempotencyLedgerDB(SQLModel, table=True):
    __tablename__ = "sfu_command_idempotency_ledger"
    __table_args__ = (
        sa.UniqueConstraint(
            "scope_digest",
            "key_digest",
            name="uq_sfu_command_ledger_scope_key",
        ),
        sa.CheckConstraint(
            "status IN ('pending','completed')",
            name="ck_sfu_command_ledger_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_sfu_command_ledger_version"),
        sa.Index(
            "ix_sfu_command_ledger_retention",
            "expires_at",
            "scope_digest",
        ),
        sa.Index(
            "ix_sfu_command_idempotency_ledger_delivery_state_expires_at",
            "delivery_state",
            "expires_at",
        ),
    )

    id: str = Field(primary_key=True)
    scope_digest: str = Field(repr=False, index=True)
    key_digest: str = Field(repr=False)
    request_digest: str = Field(repr=False)
    status: str = Field(default="pending", index=True)
    operation_id: str | None = Field(default=None, index=True)
    delivery_state: str = Field(default="pending", index=True)
    delivery_attempts: int = Field(default=0)
    result_accepted: bool | None = None
    result_effective_version: int | None = None
    result_state: str | None = None
    result_reason_code: str | None = None
    result_code: str | None = None
    result_version: int | None = None
    result_command_ref: str | None = Field(default=None, repr=False)
    version: int = Field(default=1)
    expires_at: float = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SfuFanoutReconciliationControlDB(SQLModel, table=True):
    __tablename__ = "sfu_fanout_reconciliation_controls"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "room_id",
            name="uq_sfu_fanout_reconciliation_scope",
        ),
        sa.CheckConstraint(
            "fencing_token >= 0 AND version > 0",
            name="ck_sfu_fanout_reconciliation_fencing",
        ),
        sa.CheckConstraint(
            "checkpoint_phase IS NULL "
            "OR checkpoint_phase IN ('revoke','ensure')",
            name="ck_sfu_fanout_reconciliation_phase",
        ),
        sa.Index(
            "ix_sfu_fanout_reconciliation_lease",
            "tenant_id",
            "lease_expires_at_ms",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    owner_digest: str = Field(default="", repr=False)
    fencing_token: int = Field(default=0)
    lease_expires_at_ms: int = Field(default=0, index=True)
    checkpoint_phase: str | None = None
    checkpoint_token: str | None = Field(default=None, repr=False)
    version: int = Field(default=1)
    retain_until_ms: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SfuFanoutReconciliationOutcomeDB(SQLModel, table=True):
    __tablename__ = "sfu_fanout_reconciliation_outcomes"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "room_id",
            "candidate_digest",
            "fencing_token",
            name="uq_sfu_fanout_reconciliation_outcome",
        ),
        sa.CheckConstraint(
            "fencing_token > 0",
            name="ck_sfu_fanout_reconciliation_outcome_fence",
        ),
        sa.Index(
            "ix_sfu_fanout_reconciliation_outcome_retention",
            "tenant_id",
            "room_id",
            "retain_until_ms",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    candidate_digest: str = Field(repr=False)
    outcome_digest: str = Field(repr=False)
    action: str
    reason_code: str
    retryable: bool
    mutation_outcome: str | None = None
    mutation_reason_code: str | None = None
    fencing_token: int
    retain_until_ms: int = Field(index=True)
    created_at: float = Field(default_factory=time.time, index=True)


class SfuScopeEpochAuthorityDB(SQLModel, table=True):
    __tablename__ = "sfu_scope_epoch_authorities"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "room_id",
            "actor_digest",
            name="uq_sfu_scope_epoch_actor",
        ),
        sa.CheckConstraint(
            "admission_epoch > 0 AND membership_epoch > 0 "
            "AND route_epoch >= 0 AND topology_epoch >= 0 AND key_epoch >= 0",
            name="ck_sfu_scope_epoch_values",
        ),
        sa.CheckConstraint(
            "fencing_token > 0 AND version > 0",
            name="ck_sfu_scope_epoch_fencing",
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_sfu_scope_epoch_status",
        ),
        sa.Index(
            "ix_sfu_scope_epoch_active",
            "tenant_id",
            "room_id",
            "status",
            "expires_at_ms",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    actor_digest: str = Field(repr=False, index=True)
    admission_epoch: int
    membership_epoch: int
    route_epoch: int = 0
    topology_epoch: int = 0
    key_epoch: int = 0
    fencing_token: int
    version: int
    status: str = Field(default="active", index=True)
    expires_at_ms: int = Field(index=True)
    retain_until_ms: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SfuScopeEpochGrantDB(SQLModel, table=True):
    __tablename__ = "sfu_scope_epoch_grants"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "room_id",
            "actor_digest",
            "projection_kind",
            "subject_digest",
            name="uq_sfu_scope_epoch_grant",
        ),
        sa.CheckConstraint(
            "projection_kind IN ('publisher','receiver')",
            name="ck_sfu_scope_epoch_grant_kind",
        ),
        sa.CheckConstraint(
            "scope_version > 0 AND membership_epoch > 0 AND fencing_token > 0",
            name="ck_sfu_scope_epoch_grant_fencing",
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_sfu_scope_epoch_grant_status",
        ),
        sa.Index(
            "ix_sfu_scope_epoch_grant_active",
            "tenant_id",
            "room_id",
            "actor_digest",
            "status",
            "expires_at_ms",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    actor_digest: str = Field(repr=False, index=True)
    projection_kind: str
    subject_digest: str = Field(repr=False)
    scope_version: int
    membership_epoch: int
    fencing_token: int
    status: str = Field(default="active", index=True)
    expires_at_ms: int = Field(index=True)
    retain_until_ms: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


__all__ = [
    "SfuCommandIdempotencyLedgerDB",
    "SfuFanoutReconciliationControlDB",
    "SfuFanoutReconciliationOutcomeDB",
    "SfuOperationsSnapshotDB",
    "SfuOperationsSnapshotRecordDB",
    "SfuScopeEpochAuthorityDB",
    "SfuScopeEpochGrantDB",
]
