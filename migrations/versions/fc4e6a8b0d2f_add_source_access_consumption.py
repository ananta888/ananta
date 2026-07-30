"""Add persistent source-access execution policy and consumption.

Revision ID: fc4e6a8b0d2f
Revises: eb3d5f7a9c1e
"""

from alembic import op
import sqlalchemy as sa


revision = "fc4e6a8b0d2f"
down_revision = "eb3d5f7a9c1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_access_grant_execution_policy",
        sa.Column("grant_id", sa.String(length=80), nullable=False),
        sa.Column("grant_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "destination_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "consumption_mode",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column("grant_lock_version", sa.Integer(), nullable=False),
        sa.Column("concurrency_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("grant_id"),
    )
    op.create_index(
        "ix_source_access_grant_execution_policy_grant_digest",
        "source_access_grant_execution_policy",
        ["grant_digest"],
    )
    op.create_index(
        "ix_source_access_grant_execution_policy_destination_digest",
        "source_access_grant_execution_policy",
        ["destination_digest"],
    )
    op.create_table(
        "source_access_grant_consumption",
        sa.Column("grant_id", sa.String(length=80), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column(
            "consumption_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("grant_id"),
    )
    op.create_index(
        "ix_source_access_grant_consumption_consumption_digest",
        "source_access_grant_consumption",
        ["consumption_digest"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_access_grant_consumption_consumption_digest",
        table_name="source_access_grant_consumption",
    )
    op.drop_table("source_access_grant_consumption")
    op.drop_index(
        "ix_source_access_grant_execution_policy_destination_digest",
        table_name="source_access_grant_execution_policy",
    )
    op.drop_index(
        "ix_source_access_grant_execution_policy_grant_digest",
        table_name="source_access_grant_execution_policy",
    )
    op.drop_table("source_access_grant_execution_policy")
