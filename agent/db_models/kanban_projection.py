"""Durable delivery state for the Hub-owned Kanban projection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class KanbanBoardSequenceDB(SQLModel, table=True):
    """Monotonic committed event cursor for one logical Kanban board."""

    __tablename__ = "kanban_board_sequences"
    __table_args__ = (
        sa.CheckConstraint(
            "last_sequence >= 0",
            name="ck_kanban_board_sequences_non_negative",
        ),
    )

    board_id: str = Field(primary_key=True, max_length=320)
    last_sequence: int = Field(
        default=0,
        sa_column=Column(sa.BigInteger(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )


class KanbanOutboxEventDB(SQLModel, table=True):
    """Immutable event written in the same transaction as its task mutation."""

    __tablename__ = "kanban_event_outbox"
    __table_args__ = (
        sa.CheckConstraint(
            "sequence >= 1",
            name="ck_kanban_event_outbox_positive_sequence",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_kanban_event_outbox_non_negative_revision",
        ),
        sa.UniqueConstraint(
            "dedupe_key",
            name="uq_kanban_event_outbox_dedupe_key",
        ),
    )

    board_id: str = Field(primary_key=True, max_length=320)
    sequence: int = Field(
        sa_column=Column(sa.BigInteger(), primary_key=True, nullable=False),
    )
    event_id: str = Field(max_length=64)
    task_id: str = Field(max_length=255, index=True)
    revision: int = Field(nullable=False)
    event_type: str = Field(max_length=96)
    occurred_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON(), nullable=False),
    )
    dedupe_key: str = Field(max_length=64)

