from __future__ import annotations

import time

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class SfuBrowserCapabilityDB(SQLModel, table=True):
    __tablename__ = "sfu_browser_capabilities"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "room_id", "browser_pseudonym",
            name="uq_sfu_browser_capability_scope",
        ),
        sa.Index(
            "ix_sfu_browser_capability_active",
            "tenant_id", "room_id", "status", "expires_at_ms",
        ),
        sa.CheckConstraint("admission_epoch > 0 AND membership_epoch > 0", name="ck_sfu_browser_capability_epochs"),
        sa.CheckConstraint("sequence > 0 AND version > 0", name="ck_sfu_browser_capability_sequence"),
        sa.CheckConstraint("status IN ('active','unsupported','revoked')", name="ck_sfu_browser_capability_status"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    browser_pseudonym: str = Field(repr=False, index=True)
    admission_epoch: int = Field(index=True)
    membership_epoch: int = Field(index=True)
    capability_version: str
    schema_version: int = 1
    sequence: int = Field(index=True)
    capability_class: str
    buckets_json: list = Field(default_factory=list, sa_column=sa.Column(sa.JSON(), nullable=False), repr=False)
    document_digest: str = Field(repr=False)
    status: str = Field(default="active", index=True)
    version: int = Field(default=1, index=True)
    expires_at_ms: int = Field(index=True)
    retain_until_ms: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


__all__ = ["SfuBrowserCapabilityDB"]
