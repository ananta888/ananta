from __future__ import annotations

import time

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


_ACTIVE = sa.text("status = 'active'")


class SfuBroadcastGroupKeyAuthorizationDB(SQLModel, table=True):
    __tablename__ = "sfu_broadcast_group_key_authorizations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "room_id", "publication_id", "key_epoch",
            name="uq_sfu_group_key_authorization_epoch",
        ),
        sa.Index(
            "uq_sfu_group_key_authorization_active",
            "tenant_id", "room_id", "publication_id",
            unique=True,
            sqlite_where=_ACTIVE,
            postgresql_where=_ACTIVE,
        ),
        sa.Index(
            "ix_sfu_group_key_authorization_expiry",
            "status", "expires_at_ms", "updated_at",
        ),
        sa.CheckConstraint("membership_epoch > 0 AND key_epoch > 0", name="ck_sfu_group_key_epochs"),
        sa.CheckConstraint("previous_key_epoch >= 0 AND previous_key_epoch < key_epoch", name="ck_sfu_group_key_transition"),
        sa.CheckConstraint("fencing_token > 0 AND version > 0", name="ck_sfu_group_key_fence_version"),
        sa.CheckConstraint("package_count >= 0 AND total_package_bytes >= 0", name="ck_sfu_group_key_bounds"),
        sa.CheckConstraint("distribution_mode = 'bounded_rewrap'", name="ck_sfu_group_key_distribution_mode"),
        sa.CheckConstraint("status IN ('active','revoked','expired','tombstoned')", name="ck_sfu_group_key_status"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    room_id: str = Field(index=True)
    publication_id: str = Field(index=True)
    publisher_digest: str = Field(repr=False, index=True)
    membership_epoch: int = Field(index=True)
    key_epoch: int = Field(index=True)
    previous_key_epoch: int
    member_set_digest: str = Field(repr=False)
    authorization_json: dict = Field(default_factory=dict, sa_column=sa.Column(sa.JSON(), nullable=False), repr=False)
    authorization_ciphertext: bytes | None = Field(
        default=None, repr=False, sa_column=sa.Column(sa.LargeBinary(), nullable=True)
    )
    authorization_nonce: bytes | None = Field(
        default=None, repr=False, sa_column=sa.Column(sa.LargeBinary(), nullable=True)
    )
    authorization_wrapping_key_id: str | None = Field(default=None, repr=False, index=True)
    distribution_mode: str = "bounded_rewrap"
    package_count: int = 0
    total_package_bytes: int = 0
    status: str = Field(default="active", index=True)
    fencing_token: int = Field(default=1, index=True)
    version: int = Field(default=1, index=True)
    valid_from_ms: int
    expires_at_ms: int = Field(index=True)
    rekey_deadline_ms: int
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)
    tombstoned_at: float | None = None


class SfuBroadcastGroupKeyPackageDB(SQLModel, table=True):
    __tablename__ = "sfu_broadcast_group_key_packages"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["authorization_id"], ["sfu_broadcast_group_key_authorizations.id"],
            name="fk_sfu_group_key_package_authorization", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("authorization_id", "recipient_digest", name="uq_sfu_group_key_package_recipient"),
        sa.CheckConstraint("membership_epoch > 0 AND key_epoch > 0", name="ck_sfu_group_key_package_epochs"),
        sa.CheckConstraint("package_bytes > 0 AND version > 0 AND fencing_token > 0", name="ck_sfu_group_key_package_bounds"),
        sa.CheckConstraint("status IN ('delivered','acknowledged','revoked','expired','tombstoned')", name="ck_sfu_group_key_package_status"),
        sa.Index("ix_sfu_group_key_package_delivery", "tenant_id", "session_id", "recipient_digest", "status", "expires_at_ms"),
    )

    id: str = Field(primary_key=True)
    authorization_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    recipient_digest: str = Field(repr=False, index=True)
    recipient_digest_key_id: str | None = Field(default=None, repr=False, index=True)
    package_digest: str = Field(repr=False)
    package_bytes: int
    sealed_package: bytes | None = Field(
        default=None,
        repr=False,
        sa_column=sa.Column(sa.LargeBinary(), nullable=True),
    )
    wrapping_nonce: bytes | None = Field(
        default=None,
        repr=False,
        sa_column=sa.Column(sa.LargeBinary(), nullable=True),
    )
    wrapping_key_id: str | None = Field(default=None, repr=False)
    membership_epoch: int
    key_epoch: int
    status: str = Field(default="delivered", index=True)
    fencing_token: int
    version: int = 1
    expires_at_ms: int = Field(index=True)
    delivered_at: float = Field(default_factory=time.time)
    acknowledged_at: float | None = None
    updated_at: float = Field(default_factory=time.time)


class SfuBroadcastGroupKeyReceiptDB(SQLModel, table=True):
    __tablename__ = "sfu_broadcast_group_key_receipts"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "actor_digest", "operation", "idempotency_key_digest",
            name="uq_sfu_group_key_receipt_idempotency",
        ),
        sa.CheckConstraint("operation IN ('prepare','deliver')", name="ck_sfu_group_key_receipt_operation"),
        sa.Index("ix_sfu_group_key_receipt_expiry", "expires_at_ms", "created_at"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    actor_digest: str = Field(repr=False, index=True)
    operation: str = Field(index=True)
    idempotency_key_digest: str = Field(repr=False)
    request_digest: str = Field(repr=False)
    result_json: dict = Field(sa_column=sa.Column(sa.JSON(), nullable=False), repr=False)
    expires_at_ms: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)


__all__ = [
    "SfuBroadcastGroupKeyAuthorizationDB",
    "SfuBroadcastGroupKeyPackageDB",
    "SfuBroadcastGroupKeyReceiptDB",
]
