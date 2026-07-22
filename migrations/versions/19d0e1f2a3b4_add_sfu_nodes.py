"""Add the persistent multi-Hub SFU node directory.

Revision ID: 19d0e1f2a3b4
Revises: 08c9d0e1f2a3
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "19d0e1f2a3b4"
down_revision = "08c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_nodes" not in existing:
        op.create_table(
            "sfu_nodes",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("cluster_id", sa.String(), nullable=False),
            sa.Column("node_id", sa.String(), nullable=False),
            sa.Column("runtime_identity_id", sa.String(), nullable=False),
            sa.Column("enrollment_status", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=False),
            sa.Column("adapter_name", sa.String(), nullable=False),
            sa.Column("adapter_version", sa.String(), nullable=False),
            sa.Column("protocol_version", sa.String(), nullable=False),
            sa.Column("capability_digest", sa.String(), nullable=False),
            sa.Column("last_observation_id", sa.String(), nullable=True),
            sa.Column("last_observed_at", sa.Float(), nullable=True),
            sa.Column("observation_expires_at", sa.Float(), nullable=True),
            sa.Column("health_status", sa.String(), nullable=False),
            sa.Column("drain_state", sa.String(), nullable=False),
            sa.Column("drain_reason", sa.String(), nullable=True),
            sa.Column("drain_requested_at", sa.Float(), nullable=True),
            sa.Column("drained_at", sa.Float(), nullable=True),
            sa.Column("revoked_at", sa.Float(), nullable=True),
            sa.Column("revocation_reason", sa.String(), nullable=True),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.CheckConstraint("version > 0", name="ck_sfu_nodes_version_positive"),
            sa.CheckConstraint("fencing_token >= 0", name="ck_sfu_nodes_fencing_non_negative"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("node_id", name="uq_sfu_nodes_node_id"),
            sa.UniqueConstraint(
                "runtime_identity_id",
                name="uq_sfu_nodes_runtime_identity_id",
            ),
        )
        for column in (
            "tenant_id",
            "cluster_id",
            "node_id",
            "runtime_identity_id",
            "enrollment_status",
            "region",
            "capability_digest",
            "last_observation_id",
            "last_observed_at",
            "observation_expires_at",
            "health_status",
            "drain_state",
            "drain_requested_at",
            "revoked_at",
            "fencing_token",
            "version",
            "updated_at",
        ):
            op.create_index(f"ix_sfu_nodes_{column}", "sfu_nodes", [column])
        op.create_index(
            "ix_sfu_nodes_scope_sort",
            "sfu_nodes",
            ["tenant_id", "cluster_id", "node_id", "id"],
        )
        op.create_index(
            "ix_sfu_nodes_scope_observation",
            "sfu_nodes",
            ["tenant_id", "cluster_id", "observation_expires_at"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_node_mutations" not in existing:
        op.create_table(
            "sfu_node_mutations",
            sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("cluster_id", sa.String(), nullable=False),
            sa.Column("node_id", sa.String(), nullable=False),
            sa.Column("node_version", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("snapshot_json", sa.JSON(), nullable=False),
            sa.Column("occurred_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("sequence"),
            sa.UniqueConstraint(
                "tenant_id",
                "cluster_id",
                "node_id",
                "node_version",
                name="uq_sfu_node_mutations_scope_node_version",
            ),
        )
        for column in (
            "tenant_id",
            "cluster_id",
            "node_id",
            "node_version",
            "event_type",
            "occurred_at",
        ):
            op.create_index(
                f"ix_sfu_node_mutations_{column}",
                "sfu_node_mutations",
                [column],
            )
        op.create_index(
            "ix_sfu_node_mutations_scope_sequence",
            "sfu_node_mutations",
            ["tenant_id", "cluster_id", "sequence"],
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in ("sfu_node_mutations", "sfu_nodes"):
        if table in existing:
            op.drop_table(table)
