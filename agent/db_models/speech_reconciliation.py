"""Content-free Hub persistence for offline speech reconciliation."""

from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class SpeechReconciliationJobDB(SQLModel, table=True):
    __tablename__ = "speech_reconciliation_jobs"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "owner_subject", "idempotency_key_digest", name="uq_speech_reconciliation_job_idempotency"
        ),
        sa.Index("ix_speech_reconciliation_job_scope_created", "tenant_id", "owner_subject", "created_at_ms"),
    )

    id: str = Field(default_factory=lambda: f"speech-reconciliation-{uuid.uuid4()}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    pair_scope_digest: str = Field(index=True, repr=False)
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(index=True, repr=False)
    state: str = Field(default="queued", index=True)
    stage: str = Field(default="admission", index=True)
    reason_code: str = Field(default="speech_reconciliation_admitted")
    consent_id: str = Field(index=True)
    consent_version: int
    revocation_epoch: int = 0
    input_manifest_digest: str = Field(index=True, repr=False)
    input_lineage_digest: str = Field(index=True, repr=False)
    input_artifact_ref: str = Field(repr=False)
    policy_digest: str = Field(index=True, repr=False)
    research_policy_ref: str | None = Field(default=None, repr=False)
    budget_plan: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    source_duration_ms: int
    max_compute_factor: int
    current_compute_factor: int = 1
    quality_history: list = Field(default_factory=list, sa_column=Column(JSON), repr=False)
    training_budget: dict | None = Field(default=None, sa_column=Column(JSON), repr=False)
    ledger_sequence: int = 0
    key_epoch: int
    deadline_at_ms: int = Field(index=True)
    active_attempt_id: str | None = Field(default=None, index=True)
    fencing_epoch: int = 0
    checkpoint_count: int = 0
    resolved_count: int = 0
    unresolved_count: int = 0
    rejected_count: int = 0
    quarantined_count: int = 0
    version: int = 1
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000, index=True)
    updated_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)
    finished_at_ms: int | None = None


class SpeechReconciliationMutationDB(SQLModel, table=True):
    """Content-free idempotency receipt for one Hub-owned job mutation."""

    __tablename__ = "speech_reconciliation_mutations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "job_id",
            "operation",
            "idempotency_key_digest",
            name="uq_speech_reconciliation_mutation_idempotency",
        ),
        sa.Index(
            "ix_speech_reconciliation_mutation_scope_created",
            "tenant_id",
            "owner_subject",
            "job_id",
            "created_at_ms",
        ),
    )

    id: str = Field(default_factory=lambda: f"speech-reconciliation-mutation-{uuid.uuid4()}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    job_id: str = Field(foreign_key="speech_reconciliation_jobs.id", index=True)
    operation: str = Field(index=True)
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(index=True, repr=False)
    result_job_version: int
    result_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    affected_attempt_id: str | None = Field(default=None, repr=False)
    affected_fencing_epoch: int | None = Field(default=None, repr=False)
    state_changed: bool = False
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000, index=True)


class SpeechReconciliationAttemptDB(SQLModel, table=True):
    __tablename__ = "speech_reconciliation_attempts"
    __table_args__ = (
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_speech_reconciliation_attempt_number"),
        sa.UniqueConstraint("job_id", "fencing_epoch", name="uq_speech_reconciliation_attempt_fence"),
    )

    id: str = Field(default_factory=lambda: f"speech-reconciliation-attempt-{uuid.uuid4()}", primary_key=True)
    job_id: str = Field(foreign_key="speech_reconciliation_jobs.id", index=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    attempt_number: int
    state: str = Field(default="running", index=True)
    worker_id_digest: str = Field(index=True, repr=False)
    worker_capability_digest: str = Field(repr=False)
    location_digest: str = Field(repr=False)
    resource_profile_digest: str = Field(repr=False)
    fencing_token_digest: str = Field(index=True, repr=False)
    fencing_epoch: int = Field(index=True)
    lease_expires_at_ms: int = Field(index=True)
    deadline_at_ms: int = Field(index=True)
    last_heartbeat_at_ms: int = Field(index=True)
    checkpoint_sequence: int = 0
    checkpoint_digest: str | None = Field(default=None, index=True, repr=False)
    checkpoint_ref: str | None = Field(default=None, repr=False)
    version: int = 1
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)
    updated_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)
    finished_at_ms: int | None = None


class SpeechReconciliationBudgetLedgerDB(SQLModel, table=True):
    __tablename__ = "speech_reconciliation_budget_ledgers"
    __table_args__ = (sa.UniqueConstraint("job_id", "sequence", name="uq_speech_reconciliation_ledger_sequence"),)

    id: str = Field(default_factory=lambda: f"speech-reconciliation-ledger-{uuid.uuid4()}", primary_key=True)
    job_id: str = Field(foreign_key="speech_reconciliation_jobs.id", index=True)
    attempt_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    fencing_epoch: int
    sequence: int = Field(index=True)
    stage: str = Field(index=True)
    source_duration_ms: int
    compute_factor: int
    allocated: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reserved: dict = Field(default_factory=dict, sa_column=Column(JSON))
    consumed: dict = Field(default_factory=dict, sa_column=Column(JSON))
    remaining: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


class SpeechReconciliationCheckpointDB(SQLModel, table=True):
    __tablename__ = "speech_reconciliation_checkpoints"
    __table_args__ = (
        sa.UniqueConstraint(
            "job_id",
            "attempt_id",
            "checkpoint_sequence",
            name="uq_speech_reconciliation_checkpoint_attempt_sequence",
        ),
        sa.UniqueConstraint(
            "tenant_id", "owner_subject", "checkpoint_digest", name="uq_speech_reconciliation_checkpoint_digest"
        ),
    )

    id: str = Field(default_factory=lambda: f"speech-reconciliation-checkpoint-{uuid.uuid4()}", primary_key=True)
    job_id: str = Field(foreign_key="speech_reconciliation_jobs.id", index=True)
    attempt_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    fencing_epoch: int
    consent_version: int
    revocation_epoch: int
    input_manifest_digest: str = Field(repr=False)
    policy_digest: str = Field(repr=False)
    ledger_sequence: int
    key_epoch: int
    checkpoint_sequence: int
    checkpoint_digest: str = Field(index=True, repr=False)
    checkpoint_ref: str = Field(repr=False)
    stage: str
    state_digest: str = Field(repr=False)
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


class SpeechReconciliationArtifactDB(SQLModel, table=True):
    __tablename__ = "speech_reconciliation_artifacts"
    __table_args__ = (
        sa.UniqueConstraint("job_id", "artifact_kind", "artifact_digest", name="uq_speech_reconciliation_artifact"),
    )

    id: str = Field(default_factory=lambda: f"speech-reconciliation-artifact-{uuid.uuid4()}", primary_key=True)
    job_id: str = Field(foreign_key="speech_reconciliation_jobs.id", index=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    artifact_kind: str = Field(index=True)
    artifact_digest: str = Field(index=True, repr=False)
    artifact_ref: str = Field(repr=False)
    consent_version: int
    revocation_epoch: int
    key_epoch: int
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


__all__ = [
    "SpeechReconciliationArtifactDB",
    "SpeechReconciliationAttemptDB",
    "SpeechReconciliationBudgetLedgerDB",
    "SpeechReconciliationCheckpointDB",
    "SpeechReconciliationJobDB",
    "SpeechReconciliationMutationDB",
]
