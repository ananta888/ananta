"""Add a user-chosen display name to team members (role assignments)."""

import sqlalchemy as sa
from alembic import op

revision = "b3d5f7a9c1e2"
down_revision = "a2c4e6f8b0d3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("team_members", schema=None) as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.String(length=80), nullable=True))


def downgrade():
    with op.batch_alter_table("team_members", schema=None) as batch_op:
        batch_op.drop_column("display_name")
