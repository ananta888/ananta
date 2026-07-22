"""Add durable SFU broadcast user intents and content-free command audit.

Revision ID: e68df4c5b7a9
Revises: d57cf3b4e6f8
"""

from alembic import op
import sqlalchemy as sa


revision = "e68df4c5b7a9"
down_revision = "d57cf3b4e6f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sfu_broadcast_user_intents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("room_id", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("requested_action", sa.String(length=32), nullable=False),
        sa.Column("data_saver", sa.Boolean(), nullable=True),
        sa.Column("audio_only", sa.Boolean(), nullable=True),
        sa.Column("quality_preference", sa.String(length=16), nullable=True),
        sa.Column("policy_version", sa.BigInteger(), nullable=False),
        sa.Column("admission_epoch", sa.BigInteger(), nullable=True),
        sa.Column("membership_epoch", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("last_operation_id", sa.String(length=96), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('inactive','active')", name="ck_sfu_user_intent_state"),
        sa.CheckConstraint(
            "requested_action IN ('start','stop','set_preferences','data_saver','audio_only','quality_preference')",
            name="ck_sfu_user_intent_action",
        ),
        sa.CheckConstraint(
            "quality_preference IS NULL OR quality_preference IN ('auto','low','medium','high')",
            name="ck_sfu_user_intent_quality",
        ),
        sa.CheckConstraint("version >= 1", name="ck_sfu_user_intent_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "room_id", name="uq_sfu_user_intent_tenant_room"),
    )
    op.create_index(
        "ix_sfu_user_intent_retention",
        "sfu_broadcast_user_intents",
        ["retain_until", "state"],
    )
    op.create_table(
        "sfu_broadcast_command_audits",
        sa.Column("operation_id", sa.String(length=96), nullable=False),
        sa.Column("intent_id", sa.String(length=36), nullable=True),
        sa.Column("tenant_diagnostic_ref", sa.String(length=24), nullable=False),
        sa.Column("room_diagnostic_ref", sa.String(length=24), nullable=False),
        sa.Column("actor_diagnostic_ref", sa.String(length=24), nullable=False),
        sa.Column("actor_role", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("expected_version", sa.BigInteger(), nullable=False),
        sa.Column("effective_version", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("data_saver", sa.Boolean(), nullable=True),
        sa.Column("audio_only", sa.Boolean(), nullable=True),
        sa.Column("quality_preference", sa.String(length=16), nullable=True),
        sa.Column("policy_version", sa.BigInteger(), nullable=False),
        sa.Column("admission_epoch", sa.BigInteger(), nullable=True),
        sa.Column("membership_epoch", sa.BigInteger(), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('start','stop','set_preferences','data_saver','audio_only','quality_preference')",
            name="ck_sfu_command_audit_action",
        ),
        sa.CheckConstraint(
            "state IN ('inactive','active','denied','unknown')",
            name="ck_sfu_command_audit_state",
        ),
        sa.CheckConstraint(
            "quality_preference IS NULL OR quality_preference IN ('auto','low','medium','high')",
            name="ck_sfu_command_audit_quality",
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"], ["sfu_broadcast_user_intents.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_sfu_command_audit_tenant_retention",
        "sfu_broadcast_command_audits",
        ["tenant_diagnostic_ref", "retain_until"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sfu_command_audit_tenant_retention",
        table_name="sfu_broadcast_command_audits",
    )
    op.drop_table("sfu_broadcast_command_audits")
    op.drop_index(
        "ix_sfu_user_intent_retention",
        table_name="sfu_broadcast_user_intents",
    )
    op.drop_table("sfu_broadcast_user_intents")
