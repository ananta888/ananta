from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class SfuNodeObservationCursorDB(SQLModel, table=True):
    """Persistent ordering and freshness fence for one observation producer."""

    __tablename__ = "sfu_node_observation_cursors"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "cluster_id",
            "subject_key",
            "producer_mode",
            name="uq_sfu_node_observation_cursor_scope",
        ),
        sa.CheckConstraint("highest_sequence >= 0", name="ck_sfu_observation_sequence_non_negative"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_sfu_observation_fence_non_negative"),
        sa.CheckConstraint("version > 0", name="ck_sfu_observation_cursor_version_positive"),
        sa.Index(
            "ix_sfu_node_observation_cursor_retention",
            "expires_at",
            "retain_until",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    cluster_id: str = Field(index=True)
    region: str = Field(index=True)
    node_id: str | None = Field(default=None, index=True)
    subject_key: str = Field(index=True)
    producer_mode: str = Field(index=True)
    producer_id: str = Field(index=True)
    current_boot_id: str = Field(index=True)
    retired_boot_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    highest_sequence: int = Field(index=True)
    last_payload_digest: str = Field(index=True)
    last_observation_id: str = Field(index=True)
    last_measured_at: float
    last_fresh_until: float = Field(index=True)
    normalized_observation_json: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    fencing_token: int = Field(index=True)
    version: int = Field(default=1, index=True)
    entries_max: int
    ttl_seconds: int
    retention_seconds: int
    expires_at: float = Field(index=True)
    retain_until: float = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class SfuNodeObservationReplayDB(SQLModel, table=True):
    """Bounded durable receipt for idempotency and replay rejection."""

    __tablename__ = "sfu_node_observation_replays"
    __table_args__ = (
        sa.UniqueConstraint(
            "cursor_id",
            "boot_id",
            "sequence",
            name="uq_sfu_node_observation_replay_sequence",
        ),
        sa.Index(
            "ix_sfu_node_observation_replay_retention",
            "cursor_id",
            "expires_at",
        ),
    )

    id: str = Field(default_factory=lambda: f"sfu-observation-{uuid.uuid4().hex}", primary_key=True)
    cursor_id: str = Field(index=True)
    boot_id: str = Field(index=True)
    sequence: int = Field(index=True)
    payload_digest: str = Field(index=True)
    observation_id: str = Field(index=True)
    normalized_observation_json: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    acceptance_status: str = Field(index=True)
    fresh_until: float = Field(index=True)
    accepted_at: float = Field(index=True)
    expires_at: float = Field(index=True)
    applied_node_version: int | None = Field(default=None, index=True)


__all__ = ["SfuNodeObservationCursorDB", "SfuNodeObservationReplayDB"]
