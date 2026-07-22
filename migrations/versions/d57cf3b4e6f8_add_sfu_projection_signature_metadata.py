"""add versioned SFU projection signature metadata

Revision ID: d57cf3b4e6f8
Revises: c46bf2a3d5e7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d57cf3b4e6f8"
down_revision: str | Sequence[str] | None = "c46bf2a3d5e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "sfu_layer_projections" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("sfu_layer_projections")}
    with op.batch_alter_table("sfu_layer_projections") as batch:
        if "signature_algorithm" not in existing:
            batch.add_column(
                sa.Column(
                    "signature_algorithm",
                    sa.String(),
                    nullable=False,
                    server_default="HMAC-SHA-256",
                )
            )
        if "signature_algorithm_version" not in existing:
            batch.add_column(
                sa.Column(
                    "signature_algorithm_version",
                    sa.Integer(),
                    nullable=False,
                    server_default="1",
                )
            )
        if "signature_key_version" not in existing:
            batch.add_column(
                sa.Column(
                    "signature_key_version",
                    sa.Integer(),
                    nullable=False,
                    server_default="1",
                )
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "sfu_layer_projections" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("sfu_layer_projections")}
    with op.batch_alter_table("sfu_layer_projections") as batch:
        for name in (
            "signature_key_version",
            "signature_algorithm_version",
            "signature_algorithm",
        ):
            if name in existing:
                batch.drop_column(name)
