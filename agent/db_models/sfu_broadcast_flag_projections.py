from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class SfuBroadcastFlagProjectionDB(SQLModel, table=True):
    """One durable, fenced Hub projection to one runtime target."""

    __tablename__ = "sfu_broadcast_flag_projections"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "target_runtime_id",
            "flag_version",
            "cohort_version",
            "config_digest",
            name="uq_sfu_flag_projection_effective_version",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_sfu_flag_projection_attempt"),
        sa.CheckConstraint("retry_max >= 0", name="ck_sfu_flag_projection_retry"),
        sa.CheckConstraint("fencing_token > 0", name="ck_sfu_flag_projection_fencing"),
        sa.CheckConstraint("version > 0", name="ck_sfu_flag_projection_version"),
        sa.Index(
            "ix_sfu_flag_projection_due",
            "status",
            "priority",
            "next_attempt_at",
            "deadline_at",
        ),
    )

    id: str = Field(default_factory=lambda: f"sfu-flag-projection-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    target_runtime_id: str = Field(index=True)
    cluster_id: str = Field(index=True)
    region: str = Field(index=True)
    runtime_control_mode: str = Field(index=True)
    flag_version: int = Field(index=True)
    cohort_version: int = Field(index=True)
    config_digest: str = Field(index=True)
    config_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False), repr=False)
    nonce: str = Field(repr=False)
    fencing_token: int = Field(index=True)
    priority: int = Field(default=0, index=True)
    attempt: int = 0
    retry_max: int = 0
    ttl_seconds: float
    deadline_at: float = Field(index=True)
    next_attempt_at: float = Field(index=True)
    status: str = Field(default="pending", index=True)
    reason_code: str | None = Field(default=None, index=True)
    acknowledged_at: float | None = Field(default=None, index=True)
    acknowledgement_digest: str | None = Field(default=None, repr=False)
    version: int = Field(default=1, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class SfuBroadcastRuntimeProjectionStateDB(SQLModel, table=True):
    """The only admission-facing runtime projection cursor."""

    __tablename__ = "sfu_broadcast_runtime_projection_states"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "target_runtime_id", name="uq_sfu_runtime_projection_target"
        ),
        sa.CheckConstraint("fencing_token > 0", name="ck_sfu_runtime_projection_fencing"),
        sa.CheckConstraint("version > 0", name="ck_sfu_runtime_projection_version"),
        sa.Index(
            "ix_sfu_runtime_projection_admission",
            "tenant_id",
            "cluster_id",
            "region",
            "admission_allowed",
            "ack_expires_at",
        ),
    )

    id: str = Field(default_factory=lambda: f"sfu-runtime-projection-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    target_runtime_id: str = Field(index=True)
    cluster_id: str = Field(index=True)
    region: str = Field(index=True)
    flag_version: int = Field(default=0, index=True)
    cohort_version: int = Field(default=0, index=True)
    config_digest: str = Field(default="", index=True)
    fencing_token: int = Field(index=True)
    admission_allowed: bool = Field(default=False, index=True)
    status: str = Field(default="pending", index=True)
    reason_code: str | None = Field(default=None, index=True)
    ack_expires_at: float | None = Field(default=None, index=True)
    version: int = Field(default=1, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


__all__ = ["SfuBroadcastFlagProjectionDB", "SfuBroadcastRuntimeProjectionStateDB"]
