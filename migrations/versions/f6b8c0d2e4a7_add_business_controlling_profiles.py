"""add immutable business controlling profiles

Revision ID: f6b8c0d2e4a7
Revises: e5a7b9d1f3c6
"""

import sqlalchemy as sa
from alembic import op

revision = "f6b8c0d2e4a7"
down_revision = "e5a7b9d1f3c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "business_controlling_profiles" not in existing:
        op.create_table(
            "business_controlling_profiles",
            sa.Column("profile_digest", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("source_revision_id", sa.String(69), nullable=False),
            sa.Column("revision_digest", sa.String(64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at_epoch", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "project_id", "source_revision_id", "revision_digest",
                name="uq_business_controlling_profile_source",
            ),
        )
    op.create_index(
        "ix_business_controlling_profile_scope",
        "business_controlling_profiles",
        ["tenant_id", "project_id", "source_revision_id"],
        if_not_exists=True,
    )
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "business_controlling_mappings" not in existing:
        op.create_table(
            "business_controlling_mappings",
            sa.Column("confirmation_digest", sa.String(64), primary_key=True),
            sa.Column("profile_digest", sa.String(64), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.String(128), nullable=False),
            sa.Column("column_mapping", sa.JSON(), nullable=False),
            sa.Column("confirmed_by", sa.String(128), nullable=False),
            sa.Column("created_at_epoch", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(
                ["profile_digest"],
                ["business_controlling_profiles.profile_digest"],
                name="fk_business_controlling_mapping_profile",
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("profile_digest", name="uq_business_controlling_mapping_profile"),
        )
    op.create_index(
        "ix_business_controlling_mapping_scope",
        "business_controlling_mappings",
        ["tenant_id", "project_id", "profile_digest"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("business_controlling_mappings", if_exists=True)
    op.drop_table("business_controlling_profiles", if_exists=True)
