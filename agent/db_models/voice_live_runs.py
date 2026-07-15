from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class VoiceLiveRunDB(SQLModel, table=True):
    """Durable, content-free Hub orchestration state for one long Voice run."""

    __tablename__ = "voice_live_runs"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "idempotency_key_digest",
            name="uq_voice_live_runs_scope_idempotency",
        ),
        sa.Index(
            "ix_voice_live_runs_scope_profile",
            "tenant_id",
            "owner_subject",
            "profile_id",
        ),
    )

    id: str = Field(
        default_factory=lambda: f"voice-live-run-{uuid.uuid4()}",
        primary_key=True,
    )
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    profile_id: str = Field(default="default", index=True)
    configuration_session_id: str | None = Field(default=None, index=True)
    idempotency_key_digest: str = Field(index=True, repr=False)
    parent_task_id: str = Field(index=True)
    source: str
    language: str | None = None
    status: str = Field(default="active", index=True)
    segment_duration_seconds: int = 60
    max_duration_seconds: int = 28_800
    overlap_milliseconds: int = 0
    last_local_sequence: int | None = None
    expected_last_sequence: int | None = None
    reported_gap_sequences: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    last_heartbeat_at: float = Field(default_factory=time.time, index=True)
    capture_deadline_at: float = Field(index=True)
    expires_at: float = Field(index=True)
    final_result_ref: str | None = Field(default=None, index=True)
    stop_reason: str | None = None
    maintenance_lease_token: str | None = Field(default=None, repr=False)
    maintenance_lease_expires_at: float | None = Field(default=None, index=True)
    maintenance_reconciled_at: float | None = Field(default=None, index=True)
    timeline_revision: int = Field(default=0, index=True)
    version: int = 1
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    stopped_at: float | None = None


class VoiceLiveRunSegmentDB(SQLModel, table=True):
    """Content-free segment ledger; transcript text remains in encrypted artifacts."""

    __tablename__ = "voice_live_run_segments"
    __table_args__ = (
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_voice_live_run_segments_run_sequence",
        ),
        sa.Index(
            "ix_voice_live_run_segments_scope_run",
            "tenant_id",
            "owner_subject",
            "run_id",
        ),
    )

    id: str = Field(
        default_factory=lambda: f"voice-live-segment-{uuid.uuid4()}",
        primary_key=True,
    )
    run_id: str = Field(foreign_key="voice_live_runs.id", index=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    sequence: int = Field(index=True)
    status: str = Field(default="processing", index=True)
    idempotency_key_digest: str = Field(repr=False)
    audio_binding: str | None = Field(default=None, repr=False)
    task_id: str | None = Field(default=None, index=True)
    result_ref: str | None = Field(default=None, index=True)
    provisional_result_ref: str | None = Field(default=None, index=True)
    correction_task_id: str | None = Field(default=None, index=True)
    correction_status: str = Field(default="not_requested", index=True)
    correction_configuration_digest: str | None = Field(default=None, repr=False)
    correction_spec_ref: str | None = Field(default=None, index=True, repr=False)
    correction_attempt_count: int = 0
    correction_failure_code: str | None = None
    text_revision: int = 0
    timeline_revision: int = Field(default=0, index=True)
    started_at_ms: int
    ended_at_ms: int
    duration_ms: int
    overlap_milliseconds: int = 0
    attempt_count: int = 1
    failure_code: str | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    completed_at: float | None = None
    correction_started_at: float | None = None
    correction_completed_at: float | None = None
