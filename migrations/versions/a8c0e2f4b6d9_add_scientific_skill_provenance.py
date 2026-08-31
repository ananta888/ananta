"""add scientific skill provenance receipts

Revision ID: a8c0e2f4b6d9
Revises: f6b8c0d2e4a7
"""

import sqlalchemy as sa
from alembic import op

revision = "a8c0e2f4b6d9"
down_revision = "f6b8c0d2e4a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scientific_skill_provenance_receipts",
        sa.Column("receipt_digest", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("entry_id", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_scientific_skill_receipt_scope",
        "scientific_skill_provenance_receipts",
        ["tenant_id", "project_id", "task_id"],
    )


def downgrade() -> None:
    op.drop_table("scientific_skill_provenance_receipts")
