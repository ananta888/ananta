from __future__ import annotations

import time

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class SfuAudienceSnapshotTombstoneDB(SQLModel, table=True):
    """Content-free deny marker; original tenant/session/projection ids are absent."""

    __tablename__ = "sfu_audience_snapshot_tombstones"
    __table_args__ = (
        sa.CheckConstraint("final_version > 0 AND fencing_token > 0", name="ck_sfu_audience_tombstone_fence_version"),
        sa.CheckConstraint("deny_until >= purged_at", name="ck_sfu_audience_tombstone_ttl"),
        sa.Index("ix_sfu_audience_tombstone_retention", "deny_until", "purged_at"),
    )

    id: str = Field(primary_key=True, repr=False)
    scope_digest: str = Field(repr=False, index=True)
    final_version: int
    reason_code: str
    fencing_token: int
    purged_at: float = Field(default_factory=time.time)
    deny_until: float = Field(index=True)


class SfuAudienceRetentionFenceDB(SQLModel, table=True):
    __tablename__ = "sfu_audience_retention_fences"
    __table_args__ = (
        sa.CheckConstraint("fencing_token > 0 AND version > 0", name="ck_sfu_audience_retention_fence"),
    )

    id: str = Field(primary_key=True, default="global")
    owner_id: str
    fencing_token: int
    lease_expires_at: float
    version: int = 1
    updated_at: float = Field(default_factory=time.time)


__all__ = ["SfuAudienceRetentionFenceDB", "SfuAudienceSnapshotTombstoneDB"]
