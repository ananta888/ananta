"""Add capability and one-time handle bindings to spreadsheet jobs.

Revision ID: d1f3a5b7c9e2
Revises: c0e2f4a6d8b1
"""

import sqlalchemy as sa
from alembic import op

revision = "d1f3a5b7c9e2"
down_revision = "c0e2f4a6d8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("spreadsheet_execution_jobs", sa.Column("callback_jti", sa.String(length=128), nullable=True))
    op.add_column(
        "spreadsheet_execution_jobs",
        sa.Column("artifact_handle_jti", sa.String(length=128), nullable=True),
    )
    op.add_column("spreadsheet_execution_jobs", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "spreadsheet_execution_jobs",
        sa.Column("artifact_consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "spreadsheet_execution_jobs",
        sa.Column("callback_payload_digest", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("spreadsheet_execution_jobs", "callback_payload_digest")
    op.drop_column("spreadsheet_execution_jobs", "artifact_consumed_at")
    op.drop_column("spreadsheet_execution_jobs", "claimed_at")
    op.drop_column("spreadsheet_execution_jobs", "artifact_handle_jti")
    op.drop_column("spreadsheet_execution_jobs", "callback_jti")
