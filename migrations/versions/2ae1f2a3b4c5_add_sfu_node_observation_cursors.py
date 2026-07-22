"""Add persistent SFU observation cursors and replay receipts.

Revision ID: 2ae1f2a3b4c5
Revises: 19d0e1f2a3b4
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "2ae1f2a3b4c5"
down_revision = "19d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_node_observation_cursors" not in existing:
        op.create_table(
            "sfu_node_observation_cursors",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("cluster_id", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=False),
            sa.Column("node_id", sa.String(), nullable=True),
            sa.Column("subject_key", sa.String(), nullable=False),
            sa.Column("producer_mode", sa.String(), nullable=False),
            sa.Column("producer_id", sa.String(), nullable=False),
            sa.Column("current_boot_id", sa.String(), nullable=False),
            sa.Column("retired_boot_ids", sa.JSON(), nullable=False),
            sa.Column("highest_sequence", sa.Integer(), nullable=False),
            sa.Column("last_payload_digest", sa.String(), nullable=False),
            sa.Column("last_observation_id", sa.String(), nullable=False),
            sa.Column("last_measured_at", sa.Float(), nullable=False),
            sa.Column("last_fresh_until", sa.Float(), nullable=False),
            sa.Column("normalized_observation_json", sa.JSON(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("entries_max", sa.Integer(), nullable=False),
            sa.Column("ttl_seconds", sa.Integer(), nullable=False),
            sa.Column("retention_seconds", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("retain_until", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.CheckConstraint(
                "highest_sequence >= 0",
                name="ck_sfu_observation_sequence_non_negative",
            ),
            sa.CheckConstraint(
                "fencing_token >= 0",
                name="ck_sfu_observation_fence_non_negative",
            ),
            sa.CheckConstraint(
                "version > 0",
                name="ck_sfu_observation_cursor_version_positive",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "cluster_id",
                "subject_key",
                "producer_mode",
                name="uq_sfu_node_observation_cursor_scope",
            ),
        )
        for column in (
            "tenant_id",
            "cluster_id",
            "region",
            "node_id",
            "subject_key",
            "producer_mode",
            "producer_id",
            "current_boot_id",
            "highest_sequence",
            "last_payload_digest",
            "last_observation_id",
            "last_fresh_until",
            "fencing_token",
            "version",
            "expires_at",
            "retain_until",
            "updated_at",
        ):
            op.create_index(
                f"ix_sfu_node_observation_cursors_{column}",
                "sfu_node_observation_cursors",
                [column],
            )
        op.create_index(
            "ix_sfu_node_observation_cursor_retention",
            "sfu_node_observation_cursors",
            ["expires_at", "retain_until"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "sfu_node_observation_replays" not in existing:
        op.create_table(
            "sfu_node_observation_replays",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("cursor_id", sa.String(), nullable=False),
            sa.Column("boot_id", sa.String(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("payload_digest", sa.String(), nullable=False),
            sa.Column("observation_id", sa.String(), nullable=False),
            sa.Column("normalized_observation_json", sa.JSON(), nullable=False),
            sa.Column("acceptance_status", sa.String(), nullable=False),
            sa.Column("fresh_until", sa.Float(), nullable=False),
            sa.Column("accepted_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("applied_node_version", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "cursor_id",
                "boot_id",
                "sequence",
                name="uq_sfu_node_observation_replay_sequence",
            ),
        )
        for column in (
            "cursor_id",
            "boot_id",
            "sequence",
            "payload_digest",
            "observation_id",
            "acceptance_status",
            "fresh_until",
            "accepted_at",
            "expires_at",
            "applied_node_version",
        ):
            op.create_index(
                f"ix_sfu_node_observation_replays_{column}",
                "sfu_node_observation_replays",
                [column],
            )
        op.create_index(
            "ix_sfu_node_observation_replay_retention",
            "sfu_node_observation_replays",
            ["cursor_id", "expires_at"],
        )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "sfu_node_observation_replays",
        "sfu_node_observation_cursors",
    ):
        if table in existing:
            op.drop_table(table)
