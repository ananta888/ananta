from __future__ import annotations

import time

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


_ACTIVE = sa.text("status = 'active'")


class SfuBroadcastVendorIdentityDB(SQLModel, table=True):
    __tablename__ = "sfu_broadcast_vendor_identities"
    __table_args__ = (
        sa.Index(
            "uq_sfu_vendor_identity_active_membership",
            "tenant_id", "room_id", "membership_digest", "membership_epoch", "identity_epoch",
            unique=True,
            sqlite_where=_ACTIVE,
            postgresql_where=_ACTIVE,
        ),
        sa.Index("ix_sfu_vendor_identity_expiry", "status", "expires_at", "updated_at"),
        sa.CheckConstraint("membership_epoch > 0 AND identity_epoch > 0", name="ck_sfu_vendor_identity_epochs"),
        sa.CheckConstraint("fencing_token > 0 AND version > 0", name="ck_sfu_vendor_identity_fence_version"),
        sa.CheckConstraint("expires_at > issued_at", name="ck_sfu_vendor_identity_ttl"),
        sa.CheckConstraint("status IN ('active','revoked','expired','tombstoned')", name="ck_sfu_vendor_identity_status"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    membership_digest: str = Field(repr=False, index=True)
    membership_digest_key_id: str | None = Field(default=None, repr=False, index=True)
    membership_ciphertext: bytes | None = Field(
        default=None, repr=False, sa_column=sa.Column(sa.LargeBinary(), nullable=True)
    )
    membership_nonce: bytes | None = Field(
        default=None, repr=False, sa_column=sa.Column(sa.LargeBinary(), nullable=True)
    )
    wrapping_key_id: str | None = Field(default=None, repr=False)
    membership_epoch: int = Field(index=True)
    identity_epoch: int = Field(index=True)
    status: str = Field(default="active", index=True)
    fencing_token: int = Field(index=True)
    version: int = Field(default=1, index=True)
    issued_at: float
    expires_at: float = Field(index=True)
    revoked_at: float | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class SfuBroadcastDestinationHandleDB(SQLModel, table=True):
    __tablename__ = "sfu_broadcast_destination_handles"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["identity_id"], ["sfu_broadcast_vendor_identities.id"],
            name="fk_sfu_destination_identity", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "identity_id", "route_digest", "publication_digest", "audience_digest",
            "route_epoch", "key_epoch",
            name="uq_sfu_destination_authorized_intent",
        ),
        sa.Index("ix_sfu_destination_expiry", "status", "expires_at", "updated_at"),
        sa.CheckConstraint("membership_epoch > 0 AND identity_epoch > 0 AND route_epoch > 0 AND key_epoch > 0", name="ck_sfu_destination_epochs"),
        sa.CheckConstraint("fencing_token > 0 AND version > 0", name="ck_sfu_destination_fence_version"),
        sa.CheckConstraint("status IN ('active','revoked','expired','tombstoned')", name="ck_sfu_destination_status"),
    )

    id: str = Field(primary_key=True)
    identity_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    route_digest: str = Field(repr=False, index=True)
    publication_digest: str = Field(repr=False, index=True)
    audience_digest: str = Field(repr=False, index=True)
    membership_epoch: int
    identity_epoch: int
    route_epoch: int
    key_epoch: int
    status: str = Field(default="active", index=True)
    fencing_token: int = Field(index=True)
    version: int = Field(default=1)
    issued_at: float
    expires_at: float = Field(index=True)
    revoked_at: float | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


__all__ = ["SfuBroadcastDestinationHandleDB", "SfuBroadcastVendorIdentityDB"]
