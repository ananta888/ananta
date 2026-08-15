"""Add append-only workflow transition checkpoint binding receipts.

A transition does not author checkpoint state; the runtime does. This table
only proves which exact checkpoint revision a transition effect advanced
against, so a restart re-adopts that binding instead of binding the run to
whatever revision happens to be current later.

Revision ID: c3e5a7b9d1f4
Revises: b2d4f6a8c0e3
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "c3e5a7b9d1f4"
down_revision: str | Sequence[str] | None = "b2d4f6a8c0e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_transition_checkpoint_bindings"
_PREREQUISITE = "workflow_transition_effects"

_STRING_LENGTHS = {
    "receipt_id": 256,
    "transition_id": 256,
    "effect_id": 256,
    "operation_fence_id": 256,
    "attempt_id": 256,
    "checkpoint_id": 256,
    "task_id": 256,
    "tenant_id": 256,
    "workflow_id": 256,
    "run_id": 256,
    "runtime_id": 64,
    "step_id": 256,
    "checkpoint_intent_digest": 64,
    "checkpoint_digest": 64,
    "receipt_digest": 64,
}
_BIG_INTEGERS = ("creator_claim_generation", "bound_revision", "bound_fencing_token")
_INDEXES = {
    "ix_workflow_transition_checkpoint_bind_transition": ["transition_id"],
    "ix_workflow_transition_checkpoint_bind_tenant_run": ["tenant_id", "run_id"],
    "ix_workflow_transition_checkpoint_bind_checkpoint": ["checkpoint_id"],
}


def _tables(connection: sa.Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def upgrade() -> None:
    connection = op.get_bind()
    present = _tables(connection)
    if _PREREQUISITE not in present:
        raise RuntimeError("workflow_transition_checkpoint_binding_prerequisite_missing")
    if _TABLE in present:
        return
    op.create_table(
        _TABLE,
        sa.Column("receipt_id", sa.String(_STRING_LENGTHS["receipt_id"]), primary_key=True),
        *[
            sa.Column(name, sa.String(length), nullable=False)
            for name, length in _STRING_LENGTHS.items()
            if name != "receipt_id"
        ],
        *[sa.Column(name, sa.BigInteger(), nullable=False) for name in _BIG_INTEGERS],
        sa.Column("planned_at", sa.Float(), nullable=False),
        sa.Column("bound_at", sa.Float(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.UniqueConstraint("effect_id", name="uq_workflow_transition_checkpoint_bind_effect"),
        sa.UniqueConstraint("operation_fence_id", name="uq_workflow_transition_checkpoint_bind_fence"),
        sa.UniqueConstraint("attempt_id", name="uq_workflow_transition_checkpoint_bind_attempt"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "task_id",
            "bound_revision",
            name="uq_workflow_transition_checkpoint_bind_revision",
        ),
        sa.CheckConstraint(
            "creator_claim_generation > 0 "
            "AND bound_revision > 0 "
            "AND bound_revision <= 2147483647 "
            "AND bound_fencing_token > 0 "
            "AND bound_fencing_token <= 2147483647 "
            "AND planned_at > 0 "
            "AND bound_at >= planned_at",
            name="ck_workflow_transition_checkpoint_bind_valid",
        ),
    )
    for name, columns in _INDEXES.items():
        op.create_index(name, _TABLE, columns)


def downgrade() -> None:
    connection = op.get_bind()
    if _TABLE not in _tables(connection):
        return
    existing = {str(index["name"]) for index in inspect(connection).get_indexes(_TABLE)}
    for name in _INDEXES:
        if name in existing:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
