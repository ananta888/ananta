"""Link terminal Voice reviews to immutable decision artifacts.

Revision ID: j1k2l3m4n5o6
Revises: i1j2k3l4m5n6
Create Date: 2026-07-12 00:00:01.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "j1k2l3m4n5o6"
down_revision: str | Sequence[str] | None = "i1j2k3l4m5n6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("voice_reviews")}
    if "decision_artifact_id" not in columns:
        with op.batch_alter_table("voice_reviews") as batch_op:
            batch_op.add_column(sa.Column("decision_artifact_id", sa.String(), nullable=True))
            batch_op.create_index(
                "ix_voice_reviews_decision_artifact_id",
                ["decision_artifact_id"],
                unique=False,
            )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("voice_reviews")}
    if "decision_artifact_id" in columns:
        with op.batch_alter_table("voice_reviews") as batch_op:
            batch_op.drop_index("ix_voice_reviews_decision_artifact_id")
            batch_op.drop_column("decision_artifact_id")
