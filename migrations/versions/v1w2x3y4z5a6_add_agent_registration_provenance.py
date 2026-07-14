"""Add strict Worker registration provenance and Hub-authorized capabilities.

Revision ID: v1w2x3y4z5a6
Revises: u1v2w3x4y5z6
Create Date: 2026-07-14 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v1w2x3y4z5a6"
down_revision: str | Sequence[str] | None = "u1v2w3x4y5z6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("agents")}

    if "registration_provenance" not in columns:
        op.add_column(
            "agents",
            sa.Column(
                "registration_provenance",
                sa.String(length=64),
                nullable=False,
                server_default="legacy",
            ),
        )
    if "authorized_capabilities" not in columns:
        op.add_column(
            "agents",
            sa.Column(
                "authorized_capabilities",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("authorized_capabilities")
        batch_op.drop_column("registration_provenance")
