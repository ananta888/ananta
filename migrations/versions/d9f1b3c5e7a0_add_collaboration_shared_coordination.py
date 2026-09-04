"""Add shared collaboration live-state, presence and cache boundaries.

Revision ID: d9f1b3c5e7a0
Revises: c8e0a2b4d6f9
"""

import sqlalchemy as sa
from alembic import op

revision = "d9f1b3c5e7a0"
down_revision = "c8e0a2b4d6f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collaboration_shared_cursors",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("room_id", sa.String(128), nullable=False),
        sa.Column("actor_binding_id", sa.String(128), nullable=False),
        sa.Column("view_id", sa.String(128), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "room_id", "actor_binding_id"),
        sa.CheckConstraint("epoch >= 1", name="ck_collaboration_shared_cursor_epoch"),
    )
    op.create_index(
        "ix_collaboration_shared_cursor_view",
        "collaboration_shared_cursors",
        ["tenant_id", "workspace_id", "room_id", "view_id", "expires_at"],
    )
    op.create_table(
        "collaboration_shared_control_grants",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("controlled_actor_binding_id", sa.String(128), nullable=False),
        sa.Column("controller_actor_binding_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "controlled_actor_binding_id"),
        sa.CheckConstraint("revision >= 1", name="ck_collaboration_shared_grant_revision"),
    )
    op.create_index(
        "ix_collaboration_shared_grant_controller",
        "collaboration_shared_control_grants",
        ["tenant_id", "workspace_id", "controller_actor_binding_id", "expires_at"],
    )
    op.create_table(
        "collaboration_shared_presence",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("actor_binding_id", sa.String(128), nullable=False),
        sa.Column("lease_id", sa.String(128), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "actor_binding_id"),
        sa.CheckConstraint("epoch >= 1", name="ck_collaboration_shared_presence_epoch"),
    )
    op.create_index(
        "ix_collaboration_shared_presence_expiry",
        "collaboration_shared_presence",
        ["tenant_id", "workspace_id", "expires_at"],
    )
    op.create_table(
        "collaboration_shared_cache",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("cache_key", sa.String(256), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "namespace", "cache_key"),
        sa.CheckConstraint("revision >= 1", name="ck_collaboration_shared_cache_revision"),
    )
    op.create_index(
        "ix_collaboration_shared_cache_expiry",
        "collaboration_shared_cache",
        ["tenant_id", "workspace_id", "namespace", "expires_at"],
    )


def downgrade() -> None:
    op.drop_table("collaboration_shared_cache")
    op.drop_table("collaboration_shared_presence")
    op.drop_table("collaboration_shared_control_grants")
    op.drop_table("collaboration_shared_cursors")
