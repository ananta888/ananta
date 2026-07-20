"""Durable Hub control-plane state for isolated speech adaptation.

Only content-free bindings and artifact metadata are persisted here.  Audio,
transcripts and model bytes stay behind their dedicated artifact boundaries.
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class SpeechAdaptationJobDB(SQLModel, table=True):
    """One idempotent Hub admission decision and its worker lifecycle."""

    __tablename__ = "speech_adaptation_jobs"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "idempotency_digest",
            name="uq_speech_adaptation_job_scope_idempotency",
        ),
        sa.Index(
            "ix_speech_adaptation_job_dispatch",
            "status",
            "next_dispatch_at_ms",
            "updated_at_ms",
        ),
        sa.Index(
            "ix_speech_adaptation_job_scope_created",
            "tenant_id",
            "owner_subject",
            "created_at_ms",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    task_id: str = Field(index=True)
    idempotency_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(index=True, repr=False)
    status: str = Field(default="queued", index=True)
    reason_code: str
    admission_request_payload: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    contract_payload: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    result_payload: dict | None = Field(default=None, sa_column=Column(JSON), repr=False)
    worker_status: str | None = Field(default=None, index=True)
    dispatch_attempts: int = 0
    next_dispatch_at_ms: int = Field(default=0, index=True)
    version: int = 1
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000, index=True)
    updated_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)
    terminal_at_ms: int | None = Field(default=None, index=True)


class SpeechAdaptationCapacityLeaseDB(SQLModel, table=True):
    """Cluster-wide Hub capacity slot.

    ``job_id`` intentionally has no foreign key: a lease is acquired before
    the admitted worker contract can be constructed.  A bounded expiry sweep
    safely removes a lease if the Hub crashes between those two steps.
    """

    __tablename__ = "speech_adaptation_capacity_leases"
    __table_args__ = (
        sa.UniqueConstraint("job_id", name="uq_speech_adaptation_capacity_job"),
        sa.UniqueConstraint("lease_id", name="uq_speech_adaptation_capacity_lease"),
        sa.UniqueConstraint("epoch", name="uq_speech_adaptation_capacity_epoch"),
    )

    slot: int = Field(primary_key=True)
    job_id: str = Field(index=True)
    lease_id: str = Field(index=True)
    epoch: int = Field(index=True)
    expires_at_ms: int = Field(index=True)
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


class SpeechAdaptationArtifactDB(SQLModel, table=True):
    """Hub-owned receipt for a checkpoint or candidate adapter artifact."""

    __tablename__ = "speech_adaptation_artifacts"
    __table_args__ = (
        sa.UniqueConstraint(
            "job_id",
            "attempt_id",
            "artifact_ref",
            name="uq_speech_adaptation_artifact_attempt_ref",
        ),
        sa.UniqueConstraint(
            "job_id",
            "attempt_id",
            "media_type",
            name="uq_speech_adaptation_artifact_attempt_media",
        ),
        sa.Index(
            "ix_speech_adaptation_artifact_scope",
            "tenant_id",
            "owner_subject",
            "job_id",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    job_id: str = Field(foreign_key="speech_adaptation_jobs.id", index=True)
    attempt_id: str = Field(index=True)
    artifact_ref: str = Field(index=True, repr=False)
    sha256: str = Field(index=True)
    size_bytes: int
    media_type: str
    storage_ref: str = Field(repr=False)
    state: str = Field(default="pending", index=True)
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000, index=True)
    updated_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


__all__ = [
    "SpeechAdaptationArtifactDB",
    "SpeechAdaptationCapacityLeaseDB",
    "SpeechAdaptationJobDB",
]
