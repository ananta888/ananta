"""Immutable content-free Hub observation boundaries; no Evidence identities."""

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class MemorySnapshotDB(SQLModel, table=True):
    __tablename__ = "memory_snapshots"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "run_id", "channel_id", "scope", "phase",
                            name="uq_memory_snapshot_boundary"),
        sa.CheckConstraint("phase IN ('before', 'after')", name="ck_memory_snapshot_phase"),
        sa.Index("ix_memory_snapshot_project_run", "tenant_id", "project_id", "run_id"),
    )
    id: str = Field(primary_key=True, max_length=90)
    tenant_id: str = Field(max_length=160)
    project_id: str = Field(max_length=160)
    task_id: str = Field(max_length=160)
    run_id: str = Field(max_length=160)
    channel_id: str = Field(max_length=160)
    scope: str = Field(max_length=32)
    phase: str = Field(max_length=8)
    snapshot: dict = Field(sa_column=Column(JSON, nullable=False))
    snapshot_digest: str = Field(max_length=64)
    audit_event_id: str = Field(max_length=90)

