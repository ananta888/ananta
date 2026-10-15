"""Separate append-only route certificates from immutable genome content."""

import sqlalchemy as sa
from alembic import op

revision = "a2c4e6f8b0d3"
down_revision = "f1b3d5e7a9c2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "harness_route_bindings",
        sa.Column("binding_digest", sa.String(), primary_key=True),
        sa.Column("genome_id", sa.String(), sa.ForeignKey("harness_genomes.genome_id"), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("binding_json", sa.String(), nullable=False),
    )
    for field in ("genome_id", "tenant_id", "project_id"):
        op.create_index("ix_harness_route_bindings_" + field, "harness_route_bindings", [field])


def downgrade():
    op.drop_table("harness_route_bindings")
