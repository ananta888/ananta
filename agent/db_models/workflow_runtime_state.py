"""Durable runtime event, checkpoint and side-effect ledger rows.

Re-exported by :mod:`agent.db_models.workflow_runtime`, which imports these
modules in their original definition order so table registration on
``SQLModel.metadata`` is unchanged.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel


class WorkflowRuntimeEventDB(SQLModel, table=True):
    """Append-only canonical event row."""

    __tablename__ = "workflow_runtime_events"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "run_id", "sequence", name="uq_workflow_runtime_event_sequence"),
        sa.UniqueConstraint("tenant_id", "run_id", "dedupe_key", name="uq_workflow_runtime_event_dedupe"),
        sa.UniqueConstraint("tenant_id", "run_id", "event_id", name="uq_workflow_runtime_event_id"),
        sa.Index(
            "ix_workflow_runtime_events_tenant_run_sequence",
            "tenant_id",
            "run_id",
            "sequence",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    sequence: int
    event_id: str
    event_type: str = Field(index=True)
    dedupe_key: str
    content_hash: str
    occurred_at: float = Field(index=True)
    canonical_event: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowRuntimeCheckpointDB(SQLModel, table=True):
    """Immutable checkpoint revision; latest state is derived by revision."""

    __tablename__ = "workflow_runtime_checkpoints"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "checkpoint_id", name="uq_workflow_runtime_checkpoint_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "task_id",
            "revision",
            name="uq_workflow_runtime_checkpoint_revision",
        ),
        sa.Index(
            "ix_workflow_runtime_checkpoints_latest",
            "tenant_id",
            "run_id",
            "task_id",
            "revision",
        ),
    )

    id: str = Field(primary_key=True)
    checkpoint_id: str
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    task_id: str = Field(index=True)
    revision: int
    fencing_token: int
    created_at: float = Field(index=True)
    signed_checkpoint: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowSideEffectLedgerDB(SQLModel, table=True):
    """Current state of one stable, tenant-bound external operation."""

    __tablename__ = "workflow_side_effect_ledger"
    __table_args__ = (
        sa.Index(
            "ix_workflow_side_effect_ledger_tenant_run",
            "tenant_id",
            "run_id",
        ),
    )

    operation_id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    step_id: str = Field(index=True)
    status: str = Field(index=True)
    revision: int
    fencing_token: int
    updated_at: float = Field(index=True)
    record: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))
