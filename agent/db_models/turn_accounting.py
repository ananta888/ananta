"""Persistent, pseudonym-only TURN accounting ledger models."""

from __future__ import annotations

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


_COUNTERS_NON_NEGATIVE = " AND ".join(
    f"{name} >= 0"
    for name in (
        "allocation_count",
        "active_ports",
        "ingress_bytes",
        "egress_bytes",
        "packet_count",
        "duration_seconds",
        "auth_failures",
        "exhaustion_events",
    )
)


class TurnAccountingLedgerDB(SQLModel, table=True):
    __tablename__ = "turn_accounting_ledger"
    __table_args__ = (
        sa.UniqueConstraint(
            "source_pseudonym",
            "runtime_epoch_pseudonym",
            "sequence",
            name="uq_turn_accounting_source_runtime_sequence",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_turn_accounting_sequence_positive"),
        sa.CheckConstraint(_COUNTERS_NON_NEGATIVE, name="ck_turn_accounting_counters_non_negative"),
        sa.CheckConstraint(
            "retained_until > observed_at_seconds",
            name="ck_turn_accounting_retention_after_observation",
        ),
        sa.Index(
            "ix_turn_accounting_scope_window",
            "tenant_pseudonym",
            "pool_pseudonym",
            "window_started_at_seconds",
            "id",
        ),
        sa.Index("ix_turn_accounting_retention", "retained_until", "id"),
    )

    id: str = Field(primary_key=True)
    request_digest: str
    source_pseudonym: str
    runtime_epoch_pseudonym: str
    credential_pseudonym: str
    tenant_pseudonym: str
    pool_pseudonym: str
    room_pseudonym: str
    allocation_pseudonym: str
    node_pseudonym: str
    receiver_class: str
    sequence: int
    observed_at_seconds: int
    window_started_at_seconds: int
    allocation_count: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    active_ports: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    ingress_bytes: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    egress_bytes: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    packet_count: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    duration_seconds: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    auth_failures: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    exhaustion_events: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    reason_codes: list[str] = Field(sa_column=sa.Column(sa.JSON, nullable=False))
    retained_until: int
    created_at: float


class TurnAccountingSourceCursorDB(SQLModel, table=True):
    __tablename__ = "turn_accounting_source_cursors"
    __table_args__ = (
        sa.CheckConstraint("highest_sequence > 0", name="ck_turn_accounting_cursor_sequence_positive"),
        sa.CheckConstraint("version > 0", name="ck_turn_accounting_cursor_version_positive"),
        sa.CheckConstraint(
            _COUNTERS_NON_NEGATIVE,
            name="ck_turn_accounting_cursor_counters_non_negative",
        ),
        sa.Index(
            "ix_turn_accounting_cursor_scope_retention",
            "tenant_pseudonym",
            "pool_pseudonym",
            "retained_until",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_pseudonym: str
    pool_pseudonym: str
    allocation_pseudonym: str
    node_pseudonym: str
    runtime_epoch_pseudonym: str
    highest_sequence: int
    window_started_at_seconds: int
    allocation_count: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    active_ports: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    ingress_bytes: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    egress_bytes: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    packet_count: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    duration_seconds: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    auth_failures: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    exhaustion_events: int = Field(sa_column=sa.Column(sa.BigInteger(), nullable=False))
    version: int = 1
    retained_until: int
    created_at: float
    updated_at: float


__all__ = ["TurnAccountingLedgerDB", "TurnAccountingSourceCursorDB"]
