"""Add Hub-owned knowledge-index execution authority and lease state.

Revision ID: eb3d5f7a9c1e
Revises: da2f4b6c8e0a
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eb3d5f7a9c1e"
down_revision: str | Sequence[str] | None = "da2f4b6c8e0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "knowledge_index_execution_bindings" in _table_names():
        return
    op.create_table(
        "knowledge_index_execution_bindings",
        sa.Column("job_id", sa.String(48), primary_key=True),
        sa.Column("hub_task_id", sa.String(160), nullable=False),
        sa.Column("tenant_id", sa.String(160), nullable=False),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("owner_id", sa.String(160), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_revision_id", sa.String(69), nullable=False),
        sa.Column("source_revision_digest", sa.String(64), nullable=False),
        sa.Column("admission_digest", sa.String(64), nullable=False),
        sa.Column("policy_snapshot_id", sa.String(160), nullable=False),
        sa.Column("policy_snapshot_digest", sa.String(64), nullable=False),
        sa.Column("destination_id", sa.String(68), nullable=False),
        sa.Column("destination_digest", sa.String(64), nullable=False),
        sa.Column("source_access_grant_id", sa.String(70), nullable=False),
        sa.Column(
            "source_access_grant_digest",
            sa.String(64),
            nullable=False,
        ),
        sa.Column("authority_binding_digest", sa.String(64), nullable=False),
        sa.Column("file_manifest_digest", sa.String(64), nullable=False),
        sa.Column("assignment_id", sa.String(160), nullable=False),
        sa.Column("assigned_worker_id", sa.String(160), nullable=False),
        sa.Column("lease_id", sa.String(160), nullable=False),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("lease_expires_epoch_ms", sa.BigInteger(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("envelope_json", sa.JSON(), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("created_at_epoch_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_at_epoch_ms", sa.BigInteger(), nullable=False),
        sa.Column("completed_at_epoch_ms", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "idempotency_key_digest",
            name="uq_knowledge_index_execution_scope_idempotency",
        ),
    )
    op.create_index(
        "ix_knowledge_index_execution_dispatch",
        "knowledge_index_execution_bindings",
        ["state", "assigned_worker_id", "lease_expires_epoch_ms"],
    )
    op.create_index(
        "ix_knowledge_index_execution_scope_revision",
        "knowledge_index_execution_bindings",
        ["tenant_id", "project_id", "source_revision_id"],
    )


def downgrade() -> None:
    if "knowledge_index_execution_bindings" in _table_names():
        op.drop_table("knowledge_index_execution_bindings")
