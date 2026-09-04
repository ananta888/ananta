"""Add the shared collaboration event, outbox and checkpoint store.

Revision ID: c8e0a2b4d6f9
Revises: b7d9f1a3c5e8
"""

import sqlalchemy as sa
from alembic import op

revision = "c8e0a2b4d6f9"
down_revision = "b7d9f1a3c5e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collaboration_event_streams",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("next_sequence", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id"),
        sa.CheckConstraint("next_sequence >= 1", name="ck_collaboration_event_stream_next_sequence"),
    )
    op.create_table(
        "collaboration_durable_events",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("room_id", sa.String(128), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("admitted_at", sa.Float(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["collaboration_event_streams.tenant_id", "collaboration_event_streams.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "sequence"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "event_id",
            name="uq_collaboration_durable_event_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "idempotency_key",
            name="uq_collaboration_durable_idempotency",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_collaboration_durable_event_sequence"),
    )
    op.create_index(
        "ix_collaboration_durable_room_sequence",
        "collaboration_durable_events",
        ["tenant_id", "workspace_id", "room_id", "sequence"],
    )
    op.create_table(
        "collaboration_shared_outbox",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "sequence"],
            [
                "collaboration_durable_events.tenant_id",
                "collaboration_durable_events.workspace_id",
                "collaboration_durable_events.sequence",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "event_id"),
        sa.CheckConstraint(
            "status IN ('pending','leased','delivered','dead_letter')",
            name="ck_collaboration_shared_outbox_status",
        ),
    )
    op.create_index(
        "ix_collaboration_shared_outbox_delivery",
        "collaboration_shared_outbox",
        ["tenant_id", "status", "sequence"],
    )
    op.create_table(
        "collaboration_shared_projection_checkpoints",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("projection_name", sa.String(128), nullable=False),
        sa.Column("checkpoint", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("state_digest", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["collaboration_event_streams.tenant_id", "collaboration_event_streams.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "projection_name"),
        sa.CheckConstraint("checkpoint >= 0", name="ck_collaboration_shared_checkpoint"),
        sa.CheckConstraint("revision >= 1", name="ck_collaboration_shared_checkpoint_revision"),
    )


def downgrade() -> None:
    op.drop_table("collaboration_shared_projection_checkpoints")
    op.drop_table("collaboration_shared_outbox")
    op.drop_table("collaboration_durable_events")
    op.drop_table("collaboration_event_streams")
