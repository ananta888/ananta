"""Persistent hub-owned Temporal history projection rows."""

from __future__ import annotations

import time
from typing import Any, Optional

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class TemporalHistoryProjectionDB(SQLModel, table=True):
    __tablename__ = "temporal_history_projections"

    id: str = Field(primary_key=True)
    namespace: str = Field(index=True)
    workflow_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    run_id: str = Field(index=True)
    temporal_run_id: str = Field(default="", index=True)
    correlation_id: str
    last_event_id: int = 0
    next_page_token: str = ""
    mapping_version: str
    consistency_state: str = Field(default="stale", index=True)
    reason_code: str = ""
    lag_events: Optional[int] = None
    revision: int = 1
    raw_history_ref: str = ""
    activity_step_map: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)


class TemporalProjectedEventDB(SQLModel, table=True):
    __tablename__ = "temporal_projected_events"
    __table_args__ = (
        sa.UniqueConstraint(
            "projection_id",
            "temporal_event_id",
            name="uq_temporal_projected_event_sequence",
        ),
    )

    id: str = Field(primary_key=True)
    projection_id: str = Field(foreign_key="temporal_history_projections.id", index=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    temporal_run_id: str = Field(index=True)
    temporal_event_id: int = Field(index=True)
    temporal_event_type: str
    canonical_event: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    occurred_at: float
    created_at: float = Field(default_factory=time.time)
