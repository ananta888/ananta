"""add persistent pseudonymized TURN accounting ledger

Revision ID: c46bf2a3d5e7
Revises: b35ae1f2c4d6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c46bf2a3d5e7"
down_revision: str | Sequence[str] | None = "b35ae1f2c4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COUNTERS = (
    "allocation_count",
    "active_ports",
    "ingress_bytes",
    "egress_bytes",
    "packet_count",
    "duration_seconds",
    "auth_failures",
    "exhaustion_events",
)


def upgrade() -> None:
    op.create_table(
        "turn_accounting_ledger",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("source_pseudonym", sa.String(), nullable=False),
        sa.Column("runtime_epoch_pseudonym", sa.String(), nullable=False),
        sa.Column("credential_pseudonym", sa.String(), nullable=False),
        sa.Column("tenant_pseudonym", sa.String(), nullable=False),
        sa.Column("pool_pseudonym", sa.String(), nullable=False),
        sa.Column("room_pseudonym", sa.String(), nullable=False),
        sa.Column("allocation_pseudonym", sa.String(), nullable=False),
        sa.Column("node_pseudonym", sa.String(), nullable=False),
        sa.Column("receiver_class", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("observed_at_seconds", sa.Integer(), nullable=False),
        sa.Column("window_started_at_seconds", sa.Integer(), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=False) for name in _COUNTERS),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("retained_until", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "source_pseudonym",
            "runtime_epoch_pseudonym",
            "sequence",
            name="uq_turn_accounting_source_runtime_sequence",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_turn_accounting_sequence_positive"),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in _COUNTERS),
            name="ck_turn_accounting_counters_non_negative",
        ),
        sa.CheckConstraint(
            "retained_until > observed_at_seconds",
            name="ck_turn_accounting_retention_after_observation",
        ),
    )
    op.create_index(
        "ix_turn_accounting_scope_window",
        "turn_accounting_ledger",
        ["tenant_pseudonym", "pool_pseudonym", "window_started_at_seconds", "id"],
    )
    op.create_index(
        "ix_turn_accounting_retention",
        "turn_accounting_ledger",
        ["retained_until", "id"],
    )
    op.create_table(
        "turn_accounting_source_cursors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_pseudonym", sa.String(), nullable=False),
        sa.Column("pool_pseudonym", sa.String(), nullable=False),
        sa.Column("allocation_pseudonym", sa.String(), nullable=False),
        sa.Column("node_pseudonym", sa.String(), nullable=False),
        sa.Column("runtime_epoch_pseudonym", sa.String(), nullable=False),
        sa.Column("highest_sequence", sa.Integer(), nullable=False),
        sa.Column("window_started_at_seconds", sa.Integer(), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=False) for name in _COUNTERS),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("retained_until", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "highest_sequence > 0",
            name="ck_turn_accounting_cursor_sequence_positive",
        ),
        sa.CheckConstraint("version > 0", name="ck_turn_accounting_cursor_version_positive"),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in _COUNTERS),
            name="ck_turn_accounting_cursor_counters_non_negative",
        ),
    )
    op.create_index(
        "ix_turn_accounting_cursor_scope_retention",
        "turn_accounting_source_cursors",
        ["tenant_pseudonym", "pool_pseudonym", "retained_until"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_turn_accounting_cursor_scope_retention",
        table_name="turn_accounting_source_cursors",
    )
    op.drop_table("turn_accounting_source_cursors")
    op.drop_index("ix_turn_accounting_retention", table_name="turn_accounting_ledger")
    op.drop_index("ix_turn_accounting_scope_window", table_name="turn_accounting_ledger")
    op.drop_table("turn_accounting_ledger")
