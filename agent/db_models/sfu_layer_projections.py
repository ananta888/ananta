from __future__ import annotations

import time

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class SfuLayerProjectionDB(SQLModel, table=True):
    __tablename__ = "sfu_layer_projections"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "room_id", "projection_kind", "subject_ref",
            name="uq_sfu_layer_projection_scope",
        ),
        sa.Index(
            "ix_sfu_layer_projection_current",
            "tenant_id", "room_id", "projection_kind", "status", "expires_at_ms",
        ),
        sa.CheckConstraint("projection_kind IN ('room','publisher','receiver')", name="ck_sfu_layer_projection_kind"),
        sa.CheckConstraint("projection_version > 0 AND fencing_token > 0", name="ck_sfu_layer_projection_version"),
        sa.CheckConstraint("membership_epoch > 0", name="ck_sfu_layer_projection_membership_epoch"),
        sa.CheckConstraint("status IN ('active','revoked','expired')", name="ck_sfu_layer_projection_status"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    projection_kind: str = Field(index=True)
    subject_ref: str = Field(index=True)
    projection_ref: str = Field(index=True)
    projection_version: int = Field(index=True)
    session_projection_version: int = Field(index=True)
    membership_epoch: int = Field(index=True)
    route_epoch: int = 0
    topology_epoch: int = 0
    key_epoch: int = 0
    fencing_token: int = Field(index=True)
    expected_previous_version: int
    payload_json: dict = Field(default_factory=dict, sa_column=sa.Column(sa.JSON(), nullable=False), repr=False)
    payload_digest: str = Field(repr=False)
    signature: str = Field(repr=False)
    signature_key_id: str
    signature_algorithm: str = "HMAC-SHA-256"
    signature_algorithm_version: int = 1
    signature_key_version: int = 1
    mode: str
    status: str = Field(default="active", index=True)
    expires_at_ms: int = Field(index=True)
    retain_until_ms: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class SfuLayerProjectionReceiptDB(SQLModel, table=True):
    __tablename__ = "sfu_layer_projection_receipts"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "projection_ref", "actor_digest", "receipt_sequence",
            name="uq_sfu_layer_projection_receipt_sequence",
        ),
        sa.Index("ix_sfu_layer_projection_receipt_retention", "projection_ref", "expires_at_ms"),
        sa.CheckConstraint("receipt_sequence > 0", name="ck_sfu_layer_projection_receipt_sequence"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    projection_ref: str = Field(index=True)
    actor_digest: str = Field(repr=False, index=True)
    receipt_sequence: int
    receipt_json: dict = Field(default_factory=dict, sa_column=sa.Column(sa.JSON(), nullable=False), repr=False)
    receipt_digest: str = Field(repr=False)
    expires_at_ms: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)


__all__ = ["SfuLayerProjectionDB", "SfuLayerProjectionReceiptDB"]
