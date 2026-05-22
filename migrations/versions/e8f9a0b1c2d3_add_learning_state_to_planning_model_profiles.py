"""add learning_state to planning_model_profiles

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c3
Create Date: 2026-05-23 01:05:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "planning_model_profiles",
        sa.Column(
            "learning_state",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("planning_model_profiles", "learning_state")

