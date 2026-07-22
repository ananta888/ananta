from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class SfuBroadcastFeatureFlagDB(SQLModel, table=True):
    """Current Hub-owned broadcast rollout decision for one exact scope."""

    __tablename__ = "sfu_broadcast_feature_flags"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "region",
            "room_cohort",
            "flag",
            name="uq_sfu_broadcast_flag_scope",
        ),
        sa.Index(
            "ix_sfu_broadcast_flag_scope_version",
            "tenant_id",
            "region",
            "room_cohort",
            "flag",
            "version",
        ),
    )

    id: str = Field(default_factory=lambda: f"sfu-broadcast-flag-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    region: str = Field(index=True)
    room_cohort: str = Field(index=True)
    flag: str = Field(index=True)
    enabled: bool = False
    rollout_stage: str = "off"
    version: int = Field(default=1, index=True)
    actor: str
    reason: str
    idempotency_key_digest: str = Field(index=True, repr=False)
    audited_at: float = Field(default_factory=time.time, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SfuBroadcastFeatureFlagMutationDB(SQLModel, table=True):
    """Immutable, content-free audit and idempotency receipt for one mutation."""

    __tablename__ = "sfu_broadcast_feature_flag_mutations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key_digest",
            name="uq_sfu_broadcast_flag_mutation_idempotency",
        ),
        sa.Index(
            "ix_sfu_broadcast_flag_mutation_scope",
            "tenant_id",
            "region",
            "room_cohort",
            "flag",
            "result_version",
        ),
    )

    id: str = Field(default_factory=lambda: f"sfu-broadcast-mutation-{uuid.uuid4().hex}", primary_key=True)
    feature_flag_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    region: str = Field(index=True)
    room_cohort: str = Field(index=True)
    flag: str = Field(index=True)
    enabled: bool
    rollout_stage: str
    expected_version: int
    result_version: int = Field(index=True)
    result_status: str
    actor: str = Field(index=True)
    reason: str
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    audited_at: float = Field(default_factory=time.time, index=True)


__all__ = [
    "SfuBroadcastFeatureFlagDB",
    "SfuBroadcastFeatureFlagMutationDB",
]
