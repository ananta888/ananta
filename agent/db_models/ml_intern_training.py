from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class MlInternDatasetDB(SQLModel, table=True):
    """Hub-owned metadata for one tenant-scoped LoRA dataset.

    Dataset contents stay in bounded artifact storage; this table is only the
    control-plane catalogue and therefore safe to list without loading JSONL.
    """

    __tablename__ = "ml_intern_datasets"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "content_sha256",
            name="uq_ml_intern_dataset_scope_hash",
        ),
        sa.Index("ix_ml_intern_dataset_scope_created", "tenant_id", "owner_subject", "created_at"),
    )

    id: str = Field(default_factory=lambda: f"lora-dataset-{uuid.uuid4()}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    name: str
    status: str = Field(default="uploaded", index=True)
    format_type: str = Field(default="instruction", index=True)
    content_sha256: str = Field(index=True)
    size_bytes: int = 0
    record_count: int = 0
    train_record_count: int = 0
    validation_record_count: int = 0
    rejected_record_count: int = 0
    duplicate_record_count: int = 0
    secret_finding_count: int = 0
    storage_ref: str = Field(repr=False)
    train_storage_ref: str | None = Field(default=None, repr=False)
    validation_storage_ref: str | None = Field(default=None, repr=False)
    split_manifest: dict = Field(default_factory=dict, sa_column=Column(JSON))
    validation_report: dict = Field(default_factory=dict, sa_column=Column(JSON))
    dataset_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    version: int = 1
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


class MlInternTrainingJobDB(SQLModel, table=True):
    """Durable Hub control-plane state for a long-running training job."""

    __tablename__ = "ml_intern_training_jobs"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "idempotency_key_digest",
            name="uq_ml_intern_training_job_scope_idempotency",
        ),
        sa.Index("ix_ml_intern_training_job_scope_created", "tenant_id", "owner_subject", "created_at"),
    )

    id: str = Field(default_factory=lambda: f"lora-job-{uuid.uuid4()}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    task_id: str = Field(index=True)
    dataset_id: str | None = Field(default=None, foreign_key="ml_intern_datasets.id", index=True)
    job_type: str = Field(default="train_lora", index=True)
    mode: str = Field(default="dry_run", index=True)
    backend: str = Field(default="mock", index=True)
    base_model: str | None = Field(default=None, index=True)
    status: str = Field(default="queued", index=True)
    phase: str = Field(default="queued", index=True)
    progress_percent: float = 0.0
    current_step: int | None = None
    max_steps: int | None = None
    epoch: float | None = None
    train_loss: float | None = None
    eval_loss: float | None = None
    learning_rate: float | None = None
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(index=True, repr=False)
    request_spec: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    worker_job_id: str | None = Field(default=None, index=True)
    active_attempt_id: str | None = Field(default=None, index=True)
    queue_position: int | None = None
    cancel_requested: bool = False
    checkpoint_ref: str | None = Field(default=None, index=True)
    result_ref: str | None = Field(default=None, index=True)
    result_summary: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    adapter_id: str | None = Field(default=None, index=True)
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    version: int = 1
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None


class MlInternTrainingCapacityLeaseDB(SQLModel, table=True):
    """One globally unique outstanding-job slot owned by the Hub control plane."""

    __tablename__ = "ml_intern_training_capacity_leases"

    slot: int = Field(primary_key=True)
    job_id: str = Field(foreign_key="ml_intern_training_jobs.id", unique=True, index=True)
    created_at: float = Field(default_factory=time.time)


class MlInternTrainingExecutionLeaseDB(SQLModel, table=True):
    """Cluster-wide execution semaphore with crash-expiring leases."""

    __tablename__ = "ml_intern_training_execution_leases"

    slot: int = Field(primary_key=True)
    job_id: str = Field(foreign_key="ml_intern_training_jobs.id", unique=True, index=True)
    lease_expires_at: float = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class MlInternTrainingAttemptDB(SQLModel, table=True):
    """Fenced execution attempt owned by one selected training worker."""

    __tablename__ = "ml_intern_training_attempts"
    __table_args__ = (sa.UniqueConstraint("job_id", "attempt_number", name="uq_ml_intern_attempt_job_number"),)

    id: str = Field(default_factory=lambda: f"lora-attempt-{uuid.uuid4()}", primary_key=True)
    job_id: str = Field(foreign_key="ml_intern_training_jobs.id", index=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    attempt_number: int
    status: str = Field(default="claimed", index=True)
    worker_id: str
    worker_url: str = Field(repr=False)
    fencing_token_digest: str = Field(index=True, repr=False)
    lease_expires_at: float = Field(index=True)
    deadline_at: float = Field(index=True)
    last_heartbeat_at: float = Field(default_factory=time.time, index=True)
    checkpoint_ref: str | None = Field(default=None, index=True)
    result_ref: str | None = Field(default=None, index=True)
    error_code: str | None = None
    version: int = 1
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    finished_at: float | None = None


class MlInternTrainingEventDB(SQLModel, table=True):
    """Content-free, append-only event used by polling and SSE projections."""

    __tablename__ = "ml_intern_training_events"
    __table_args__ = (
        sa.UniqueConstraint("job_id", "sequence", name="uq_ml_intern_event_job_sequence"),
        sa.UniqueConstraint("job_id", "dedupe_key", name="uq_ml_intern_event_job_dedupe"),
        sa.Index("ix_ml_intern_event_scope_job", "tenant_id", "owner_subject", "job_id"),
    )

    id: str = Field(default_factory=lambda: f"lora-event-{uuid.uuid4()}", primary_key=True)
    job_id: str = Field(foreign_key="ml_intern_training_jobs.id", index=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    sequence: int = Field(index=True)
    event_type: str = Field(index=True)
    dedupe_key: str
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
