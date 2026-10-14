"""Add scoped immutable provider assets and lifecycle history.

Revision ID: f1b3d5e7a9c2
Revises: e0a2c4d6f8b1
"""

import sqlalchemy as sa
from alembic import op

revision = "f1b3d5e7a9c2"
down_revision = "e0a2c4d6f8b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evolution_assets",
        sa.Column("tenant_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), primary_key=True),
        sa.Column("asset_digest", sa.String(), primary_key=True),
        sa.Column("asset_json", sa.String(), nullable=False),
        sa.Column("run_ref", sa.String(), nullable=False),
    )
    op.create_table(
        "evolution_asset_states",
        sa.Column("tenant_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), primary_key=True),
        sa.Column("asset_digest", sa.String(), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
    )


def downgrade():
    op.drop_table("evolution_asset_states")
    op.drop_table("evolution_assets")
