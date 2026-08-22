"""Persistent idempotency receipts for HRM control-plane mutations."""

from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class HrmIdempotencyReceiptDB(SQLModel, table=True):
    __tablename__ = "hrm_idempotency_receipts"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "operation",
            "key_digest",
            name="uq_hrm_idempotency_receipt_scope",
        ),
        sa.Index(
            "ix_hrm_idempotency_receipt_resource",
            "tenant_id",
            "operation",
            "resource_id",
        ),
    )

    id: str = Field(
        default_factory=lambda: f"hrm-idempotency-{uuid.uuid4()}",
        primary_key=True,
    )
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    operation: str = Field(index=True)
    key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(index=True, repr=False)
    state: str = Field(default="pending", index=True)
    resource_id: str | None = Field(default=None, index=True)
    response: dict = Field(default_factory=dict, sa_column=Column(JSON), repr=False)
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


__all__ = ["HrmIdempotencyReceiptDB"]
