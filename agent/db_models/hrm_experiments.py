"""Durable Hub control-plane projections for HRM experiments."""

from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class HrmWorkerCapabilityDB(SQLModel, table=True):
    __tablename__ = "hrm_worker_capabilities"
    __table_args__ = (
        sa.UniqueConstraint("worker_url", name="uq_hrm_worker_capability_url"),
        sa.Index("ix_hrm_worker_capability_expiry", "expires_at", "observed_at"),
    )

    id: str = Field(default_factory=lambda: f"hrm-cap-{uuid.uuid4()}", primary_key=True)
    worker_id: str = Field(index=True)
    worker_url: str = Field(index=True, repr=False)
    capability_digest: str = Field(index=True, repr=False)
    projection: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    observed_at: float = Field(default_factory=time.time, index=True)
    expires_at: float = Field(index=True)
    version: int = 1


class HrmDatasetDB(SQLModel, table=True):
    __tablename__ = "hrm_datasets"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "project_id", "dataset_id", name="uq_hrm_dataset_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "content_digest",
            name="uq_hrm_dataset_scope_digest",
        ),
        sa.Index("ix_hrm_dataset_scope_created", "tenant_id", "project_id", "created_at", "id"),
    )

    id: str = Field(default_factory=lambda: f"hrm-dataset-row-{uuid.uuid4()}", primary_key=True)
    dataset_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    puzzle_type: str = Field(index=True)
    content_digest: str = Field(index=True, repr=False)
    manifest: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    records: list = Field(default_factory=list, sa_column=Column(JSON), repr=False)
    created_at: float = Field(default_factory=time.time, index=True)


class HrmCheckpointDB(SQLModel, table=True):
    __tablename__ = "hrm_checkpoints"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "project_id", "checkpoint_id", name="uq_hrm_checkpoint_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "content_digest",
            name="uq_hrm_checkpoint_scope_digest",
        ),
        sa.Index("ix_hrm_checkpoint_scope_created", "tenant_id", "project_id", "created_at", "id"),
    )

    id: str = Field(default_factory=lambda: f"hrm-checkpoint-row-{uuid.uuid4()}", primary_key=True)
    checkpoint_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    content_digest: str = Field(index=True, repr=False)
    state: str = Field(index=True)
    manifest: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    created_at: float = Field(default_factory=time.time, index=True)


class HrmRunDB(SQLModel, table=True):
    __tablename__ = "hrm_runs"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "idempotency_key_digest",
            name="uq_hrm_run_scope_idempotency",
        ),
        sa.Index("ix_hrm_run_scope_created", "tenant_id", "project_id", "created_at", "id"),
    )

    id: str = Field(default_factory=lambda: f"hrm-run-{uuid.uuid4()}", primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    task_id: str = Field(index=True)
    profile_id: str = Field(index=True)
    mode: str = Field(index=True)
    dataset_id: str = Field(index=True)
    dataset_digest: str = Field(index=True, repr=False)
    checkpoint_id: str | None = Field(default=None, index=True)
    checkpoint_digest: str | None = Field(default=None, index=True, repr=False)
    status: str = Field(default="queued", index=True)
    reason_code: str | None = Field(default=None, index=True)
    intent: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(index=True, repr=False)
    capability_digest: str = Field(repr=False)
    policy_digest: str = Field(repr=False)
    worker_url: str | None = Field(default=None, repr=False)
    worker_job_id: str | None = Field(default=None, index=True)
    assignment_id: str | None = Field(default=None, index=True)
    dispatch_lease_id: str | None = Field(default=None, index=True)
    attempt_id: str | None = Field(default=None, index=True)
    epoch: int = 0
    deadline_epoch_ms: int | None = Field(default=None, index=True)
    execution_envelope: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    result: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    cancel_requested: bool = False
    version: int = 1
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)
    started_at: float | None = Field(default=None, index=True)
    finished_at: float | None = Field(default=None, index=True)


class HrmRunEventDB(SQLModel, table=True):
    __tablename__ = "hrm_run_events"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "sequence", name="uq_hrm_run_event_sequence"),
        sa.Index("ix_hrm_run_event_scope", "tenant_id", "project_id", "run_id", "sequence"),
    )

    id: str = Field(default_factory=lambda: f"hrm-event-{uuid.uuid4()}", primary_key=True)
    run_id: str = Field(foreign_key="hrm_runs.id", index=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    sequence: int = Field(index=True)
    event: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    created_at: float = Field(default_factory=time.time, index=True)


class HrmEvaluationReportDB(SQLModel, table=True):
    __tablename__ = "hrm_evaluation_reports"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "owner_subject", "idempotency_key_digest", name="uq_hrm_report_scope_idempotency"
        ),
        sa.Index("ix_hrm_report_scope_created", "tenant_id", "project_id", "created_at", "id"),
    )

    id: str = Field(default_factory=lambda: f"hrm-report-{uuid.uuid4()}", primary_key=True)
    evaluation_id: str = Field(index=True)
    run_id: str = Field(foreign_key="hrm_runs.id", index=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(index=True, repr=False)
    content_digest: str = Field(index=True, repr=False)
    report: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    created_at: float = Field(default_factory=time.time, index=True)
