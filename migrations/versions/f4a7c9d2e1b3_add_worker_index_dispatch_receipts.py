"""Add the Worker-scoped knowledge-index dispatch replay ledger.

Revision ID: f4a7c9d2e1b3
Revises: 5a0e2b4c6d8f
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a7c9d2e1b3"
down_revision: str | Sequence[str] | None = "5a0e2b4c6d8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "knowledge_index_worker_dispatch_receipts"
_EXECUTION_TABLE = "knowledge_index_execution_bindings"


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _table_names()
    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("receipt_id", sa.String(64), primary_key=True),
            sa.Column("worker_id", sa.String(160), nullable=False),
            sa.Column("job_id", sa.String(192), nullable=False),
            sa.Column("assignment_id", sa.String(192), nullable=False),
            sa.Column("lease_id", sa.String(192), nullable=False),
            sa.Column("marker_digest", sa.String(64), nullable=False),
            sa.Column(
                "manifest_binding_digest",
                sa.String(64),
                nullable=False,
            ),
            sa.Column(
                "lease_expires_epoch_ms",
                sa.BigInteger(),
                nullable=False,
            ),
            sa.Column(
                "grant_expires_at_epoch_ms",
                sa.BigInteger(),
                nullable=False,
            ),
            sa.Column(
                "claimed_at_epoch_ms",
                sa.BigInteger(),
                nullable=False,
            ),
            sa.Column(
                "state",
                sa.String(32),
                nullable=False,
                server_default="claimed",
            ),
            sa.Column(
                "result_digest",
                sa.String(64),
                nullable=True,
            ),
            sa.Column(
                "result_payload",
                sa.JSON(),
                nullable=True,
            ),
            sa.Column(
                "completed_at_epoch_ms",
                sa.BigInteger(),
                nullable=True,
            ),
            sa.UniqueConstraint(
                "worker_id",
                "job_id",
                "assignment_id",
                "lease_id",
                "marker_digest",
                name="uq_knowledge_index_worker_dispatch_receipt_marker",
            ),
            sa.UniqueConstraint(
                "worker_id",
                "job_id",
                name="uq_knowledge_index_worker_dispatch_receipt_job",
            ),
        )
        op.create_index(
            "ix_knowledge_index_worker_dispatch_receipts_worker_id",
            _TABLE,
            ["worker_id"],
        )
        op.create_index(
            "ix_knowledge_index_worker_dispatch_receipts_job_id",
            _TABLE,
            ["job_id"],
        )

    if _EXECUTION_TABLE not in tables:
        return
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            _EXECUTION_TABLE
        )
    }
    additions = (
        sa.Column(
            "completion_projection_state",
            sa.String(32),
            nullable=True,
        ),
        sa.Column(
            "completion_projection_lock_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "completion_projection_digest",
            sa.String(64),
            nullable=True,
        ),
        sa.Column(
            "completion_projection_payload",
            sa.JSON(),
            nullable=True,
        ),
        sa.Column(
            "completion_projection_created_at_epoch_ms",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "completion_projection_updated_at_epoch_ms",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "completion_projected_at_epoch_ms",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column(_EXECUTION_TABLE, column)
    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(
            _EXECUTION_TABLE
        )
    }
    index_name = "ix_knowledge_index_execution_completion_projection"
    if index_name not in indexes:
        op.create_index(
            index_name,
            _EXECUTION_TABLE,
            ["completion_projection_state"],
        )


def downgrade() -> None:
    if _EXECUTION_TABLE in _table_names():
        inspector = sa.inspect(op.get_bind())
        indexes = {
            index["name"]
            for index in inspector.get_indexes(_EXECUTION_TABLE)
        }
        index_name = (
            "ix_knowledge_index_execution_completion_projection"
        )
        if index_name in indexes:
            op.drop_index(index_name, table_name=_EXECUTION_TABLE)
        columns = {
            column["name"]
            for column in inspector.get_columns(_EXECUTION_TABLE)
        }
        for name in (
            "completion_projected_at_epoch_ms",
            "completion_projection_updated_at_epoch_ms",
            "completion_projection_created_at_epoch_ms",
            "completion_projection_payload",
            "completion_projection_digest",
            "completion_projection_lock_version",
            "completion_projection_state",
        ):
            if name in columns:
                op.drop_column(_EXECUTION_TABLE, name)
    if _TABLE in _table_names():
        op.drop_table(_TABLE)
