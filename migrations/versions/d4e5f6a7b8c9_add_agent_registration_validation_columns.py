"""add agent registration validation columns

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("agents")}

    if "registration_validated" not in columns:
        op.add_column("agents", sa.Column("registration_validated", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "validation_errors" not in columns:
        op.add_column("agents", sa.Column("validation_errors", sa.JSON(), nullable=True))
    if "validated_at" not in columns:
        op.add_column("agents", sa.Column("validated_at", sa.Float(), nullable=True))

    if bind.dialect.name == "postgresql":
        op.execute("UPDATE agents SET validation_errors = '[]'::json WHERE validation_errors IS NULL")
        op.alter_column("agents", "registration_validated", server_default=None)
    else:
        op.execute("UPDATE agents SET validation_errors = '[]' WHERE validation_errors IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("validated_at")
        batch_op.drop_column("validation_errors")
        batch_op.drop_column("registration_validated")
