from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class SfuBroadcastAdmissionOperationDB(SQLModel, table=True):
    """Durable saga journal; access tokens and opaque identity secrets are never stored."""

    __tablename__ = "sfu_broadcast_admission_operations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key_digest", name="uq_sfu_admission_operation_idempotency"
        ),
        sa.CheckConstraint("version > 0", name="ck_sfu_admission_operation_version"),
        sa.Index("ix_sfu_admission_operation_recovery", "status", "deadline_at", "updated_at"),
    )

    id: str = Field(default_factory=lambda: f"sfu-admission-operation-{uuid.uuid4().hex}", primary_key=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    actor_digest: str = Field(index=True, repr=False)
    operation: str = Field(index=True)
    idempotency_key_digest: str = Field(index=True, repr=False)
    request_digest: str = Field(repr=False)
    expected_version: int
    status: str = Field(default="open", index=True)
    current_step: str = Field(default="started", index=True)
    applied_steps: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    external_request_ids: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False), repr=False)
    bindings_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False), repr=False)
    compensation_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False), repr=False)
    result_digest: str | None = Field(default=None, repr=False)
    reason_code: str | None = Field(default=None, index=True)
    deadline_at: float = Field(index=True)
    version: int = Field(default=1, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)
    completed_at: float | None = Field(default=None, index=True)


__all__ = ["SfuBroadcastAdmissionOperationDB"]
