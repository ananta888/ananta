from __future__ import annotations

import time

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class SfuBroadcastBackgroundJobDB(SQLModel, table=True):
    """Persistent lease, cursor and bounded execution policy for one Hub job partition."""

    __tablename__ = "sfu_broadcast_background_jobs"
    __table_args__ = (
        sa.UniqueConstraint("job_name", "partition_key", name="uq_sfu_background_job_partition"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_sfu_background_job_fencing"),
        sa.CheckConstraint("version > 0", name="ck_sfu_background_job_version"),
        sa.CheckConstraint("interval_ms_min > 0", name="ck_sfu_background_job_interval"),
        sa.CheckConstraint("batch_size_max > 0", name="ck_sfu_background_job_batch"),
        sa.CheckConstraint("runtime_deadline_ms > 0", name="ck_sfu_background_job_deadline"),
        sa.Index("ix_sfu_background_job_due", "enabled", "next_run_at", "lease_expires_at"),
    )

    id: str = Field(primary_key=True)
    job_name: str = Field(index=True)
    partition_key: str = Field(index=True)
    enabled: bool = Field(default=False, index=True)
    owner_id: str | None = Field(default=None, index=True)
    fencing_token: int = Field(default=0, index=True)
    lease_expires_at: float | None = Field(default=None, index=True)
    interval_ms_min: int
    batch_size_max: int
    runtime_deadline_ms: int
    retry_max: int
    backoff_ms: int
    jitter_ms: int
    retention_seconds: int
    resume_cursor: str | None = Field(default=None, repr=False)
    attempt: int = 0
    last_status: str = Field(default="never", index=True)
    last_reason_code: str | None = Field(default=None, index=True)
    last_started_at: float | None = Field(default=None, index=True)
    last_finished_at: float | None = Field(default=None, index=True)
    next_run_at: float = Field(default=0.0, index=True)
    version: int = Field(default=1, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


__all__ = ["SfuBroadcastBackgroundJobDB"]
