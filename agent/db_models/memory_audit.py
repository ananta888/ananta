"""Hub-owned decision audit chain, independent of memory content retention."""

from __future__ import annotations

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class MemoryAuditHeadDB(SQLModel, table=True):
    __tablename__ = "memory_audit_heads"
    __table_args__ = (sa.CheckConstraint("last_sequence >= 0", name="ck_memory_audit_head_sequence"),)

    tenant_id: str = Field(primary_key=True, max_length=160)
    last_sequence: int = 0
    last_hash: str = Field(default="0" * 64, max_length=64)


class MemoryAuditEventDB(SQLModel, table=True):
    __tablename__ = "memory_audit_events"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "sequence", name="uq_memory_audit_tenant_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_memory_audit_event_sequence"),
        sa.Index("ix_memory_audit_tenant_task", "tenant_id", "task_id", "sequence"),
        sa.Index("ix_memory_audit_tenant_trace", "tenant_id", "trace_id", "sequence"),
    )

    id: str = Field(primary_key=True, max_length=80)
    tenant_id: str = Field(index=True, max_length=160)
    sequence: int
    task_id: str | None = Field(default=None, max_length=160)
    trace_id: str | None = Field(default=None, max_length=160)
    previous_hash: str = Field(max_length=64)
    event_hash: str = Field(max_length=64)
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: float
    retention_until: float
