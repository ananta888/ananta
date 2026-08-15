"""Add append-only workflow transition queue reservation receipts.

The reservation gets its own table rather than an attribution column on tasks:
tasks are also created outside transitions, so a shared row would put two
writers with different fencing rules on one piece of state.

Revision ID: b2d4f6a8c0e3
Revises: a1c3e5f7b9d2
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "b2d4f6a8c0e3"
down_revision: str | Sequence[str] | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_transition_queue_reservations"
_PREREQUISITE = "workflow_transition_effects"

_STRING_LENGTHS = {
    "receipt_id": 256,
    "transition_id": 256,
    "effect_id": 256,
    "operation_fence_id": 256,
    "attempt_id": 256,
    "task_id": 256,
    "tenant_id": 256,
    "workflow_id": 256,
    "run_id": 256,
    "runtime_id": 64,
    "step_id": 256,
    "queue_intent_digest": 64,
    "reservation_record_digest": 64,
    "receipt_digest": 64,
}
_BIG_INTEGERS = ("creator_claim_generation", "reserved_revision")
_INDEXES = {
    "ix_workflow_transition_queue_res_transition": ["transition_id"],
    "ix_workflow_transition_queue_res_tenant_run": ["tenant_id", "run_id"],
    "ix_workflow_transition_queue_res_scope": ["tenant_id", "run_id", "step_id"],
    "ix_workflow_transition_queue_res_task": ["task_id"],
}


def _tables(connection: sa.Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def upgrade() -> None:
    connection = op.get_bind()
    present = _tables(connection)
    if _PREREQUISITE not in present:
        raise RuntimeError("workflow_transition_queue_reservation_prerequisite_missing")
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
        sa.Column("maximum_retries", sa.Integer(), nullable=False),
        sa.Column("retry_consumed", sa.Boolean(), nullable=False),
        sa.Column("planned_at", sa.Float(), nullable=False),
        sa.Column("reserved_at", sa.Float(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.UniqueConstraint("effect_id", name="uq_workflow_transition_queue_res_effect"),
        sa.UniqueConstraint("operation_fence_id", name="uq_workflow_transition_queue_res_fence"),
        sa.UniqueConstraint("attempt_id", name="uq_workflow_transition_queue_res_attempt"),
        sa.UniqueConstraint("tenant_id", "run_id", "task_id", name="uq_workflow_transition_queue_res_task"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            "reserved_revision",
            name="uq_workflow_transition_queue_res_revision",
        ),
        sa.CheckConstraint(
            "creator_claim_generation > 0 "
            "AND reserved_revision > 0 "
            "AND reserved_revision <= 2147483647 "
            "AND maximum_retries >= 0 "
            "AND maximum_retries <= 2147483647 "
            "AND (retry_consumed = FALSE OR retry_consumed = TRUE) "
            "AND planned_at > 0 "
            "AND reserved_at >= planned_at",
            name="ck_workflow_transition_queue_res_valid",
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
