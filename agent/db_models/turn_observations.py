from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class TurnObservationCursorDB(SQLModel, table=True):
    __tablename__ = "turn_observation_cursors"
    __table_args__ = (
        sa.UniqueConstraint("pool_id", "instance_id", name="uq_turn_observation_cursor_scope"),
        sa.CheckConstraint("highest_sequence >= 0", name="ck_turn_observation_sequence_non_negative"),
        sa.CheckConstraint("fencing_token > 0", name="ck_turn_observation_fence_positive"),
        sa.CheckConstraint("version > 0", name="ck_turn_observation_version_positive"),
        sa.Index("ix_turn_observation_cursor_freshness", "fresh_until", "retain_until"),
    )

    id: str = Field(default_factory=lambda: f"turn-observation-cursor-{uuid.uuid4().hex}", primary_key=True)
    pool_id: str = Field(index=True)
    instance_id: str = Field(index=True)
    observer_identity_id: str = Field(index=True)
    observer_identity_version: int = Field(index=True)
    current_boot_id: str = Field(index=True)
    retired_boot_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    highest_sequence: int = Field(index=True)
    last_payload_digest: str = Field(index=True)
    last_observation_id: str = Field(index=True)
    last_measured_at: float
    last_counters_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    normalized_observation_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    health_status: str = Field(index=True)
    capacity_status: str = Field(index=True)
    fencing_token: int = Field(default=1, index=True)
    version: int = Field(default=1, index=True)
    fresh_until: float = Field(index=True)
    retain_until: float = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class TurnObservationReplayDB(SQLModel, table=True):
    __tablename__ = "turn_observation_replays"
    __table_args__ = (
        sa.UniqueConstraint("observation_id_digest", name="uq_turn_observation_replay_id"),
        sa.Index("ix_turn_observation_replay_scope_expiry", "pool_id", "instance_id", "expires_at"),
    )

    id: str = Field(default_factory=lambda: f"turn-observation-replay-{uuid.uuid4().hex}", primary_key=True)
    pool_id: str = Field(index=True)
    instance_id: str = Field(index=True)
    observation_id_digest: str = Field(index=True, repr=False)
    payload_digest: str = Field(index=True, repr=False)
    boot_id_digest: str = Field(index=True, repr=False)
    sequence: int = Field(index=True)
    accepted_at: float = Field(default_factory=time.time, index=True)
    expires_at: float = Field(index=True)


__all__ = ["TurnObservationCursorDB", "TurnObservationReplayDB"]
