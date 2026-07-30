"""Add restart-safe legacy source-control migration mappings.

Revision ID: da2f4b6c8e0a
Revises: c9e1f3a5b7d9
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "da2f4b6c8e0a"
down_revision: str | Sequence[str] | None = "c9e1f3a5b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _table_names()
    if "source_ref_mappings" not in tables:
        op.create_table(
            "source_ref_mappings",
            sa.Column("source_ref_id", sa.String(69), primary_key=True),
            sa.Column(
                "connection_id",
                sa.String(69),
                sa.ForeignKey("source_connections.connection_id"),
                nullable=False,
            ),
            sa.Column(
                "source_revision_id",
                sa.String(69),
                sa.ForeignKey("source_revisions.source_revision_id"),
                nullable=False,
            ),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("provenance_digest", sa.String(64), nullable=False),
            sa.Column("created_at_epoch", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "source_revision_id",
                "provenance_digest",
                name="uq_source_ref_mappings_revision_provenance",
            ),
        )
        op.create_index(
            "ix_source_ref_mappings_scope",
            "source_ref_mappings",
            ["tenant_id", "project_id", "owner_id", "connection_id"],
        )
    tables = _table_names()
    if "source_control_migration_runs" not in tables:
        op.create_table(
            "source_control_migration_runs",
            sa.Column("migration_id", sa.String(70), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("inventory_digest", sa.String(64), nullable=False),
            sa.Column("state", sa.String(32), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_entries", sa.Integer(), nullable=False),
            sa.Column(
                "created_mapping_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "reused_mapping_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "conflict_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "lock_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("failure_reason", sa.String(160), nullable=True),
            sa.Column("started_at_epoch", sa.Float(), nullable=False),
            sa.Column("updated_at_epoch", sa.Float(), nullable=False),
            sa.Column("completed_at_epoch", sa.Float(), nullable=True),
            sa.UniqueConstraint(
                "tenant_id",
                "project_id",
                "owner_id",
                "inventory_digest",
                name="uq_source_control_migration_scope_inventory",
            ),
        )
        op.create_index(
            "ix_source_control_migration_runs_scope",
            "source_control_migration_runs",
            ["tenant_id", "project_id", "owner_id", "state"],
        )
    tables = _table_names()
    if "source_control_legacy_mappings" not in tables:
        op.create_table(
            "source_control_legacy_mappings",
            sa.Column("mapping_id", sa.String(69), primary_key=True),
            sa.Column(
                "migration_id",
                sa.String(70),
                sa.ForeignKey("source_control_migration_runs.migration_id"),
                nullable=False,
            ),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("legacy_kind", sa.String(32), nullable=False),
            sa.Column("legacy_key", sa.String(256), nullable=False),
            sa.Column("legacy_record_digest", sa.String(64), nullable=False),
            sa.Column("connection_id", sa.String(69), nullable=True),
            sa.Column("source_revision_id", sa.String(69), nullable=True),
            sa.Column("source_ref_id", sa.String(69), nullable=True),
            sa.Column("knowledge_index_id", sa.String(128), nullable=True),
            sa.Column("index_run_id", sa.String(128), nullable=True),
            sa.Column("policy_snapshot_id", sa.String(128), nullable=True),
            sa.Column("policy_version", sa.String(128), nullable=True),
            sa.Column(
                "created_source_ref_mapping",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "created_index_binding",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "created_run_binding",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("created_at_epoch", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "migration_id",
                "sequence",
                name="uq_source_control_legacy_mappings_sequence",
            ),
        )
        op.create_index(
            "ix_source_control_legacy_mappings_run",
            "source_control_legacy_mappings",
            ["migration_id", "sequence"],
        )


def downgrade() -> None:
    for table_name in (
        "source_control_legacy_mappings",
        "source_control_migration_runs",
        "source_ref_mappings",
    ):
        if table_name in _table_names():
            op.drop_table(table_name)
