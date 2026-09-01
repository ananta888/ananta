"""Add the Hub-owned Spreadsheet Studio execution queue.

Revision ID: c0e2f4a6d8b1
Revises: b9d1f3a5c7e0
"""

import sqlalchemy as sa
from alembic import op

revision = "c0e2f4a6d8b1"
down_revision = "b9d1f3a5c7e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spreadsheet_execution_jobs",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("job_id", sa.String(length=128), nullable=False),
        sa.Column("proposal_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("proposal_digest", sa.String(length=64), nullable=False),
        sa.Column("assignment_digest", sa.String(length=64), nullable=False),
        sa.Column("assignment_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("worker_job_id", sa.String(length=128), nullable=True),
        sa.Column("slot_lease_id", sa.String(length=128), nullable=True),
        sa.Column("worker_id", sa.String(length=512), nullable=True),
        sa.Column("queue_position", sa.BigInteger(), nullable=True),
        sa.Column("result_digest", sa.String(length=64), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('dispatch_pending','queued','leased','completed','failed','cancelled')",
            name="ck_spreadsheet_execution_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "job_id"),
        sa.UniqueConstraint("tenant_id", "proposal_id", name="uq_spreadsheet_execution_proposal"),
    )
    op.create_index(
        "ix_spreadsheet_execution_queue",
        "spreadsheet_execution_jobs",
        ["status", "created_at", "job_id"],
    )
    op.create_index(
        "ix_spreadsheet_execution_jobs_worker_job_id",
        "spreadsheet_execution_jobs",
        ["worker_job_id"],
    )
    op.create_index(
        "ix_spreadsheet_execution_jobs_slot_lease_id",
        "spreadsheet_execution_jobs",
        ["slot_lease_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_spreadsheet_execution_jobs_slot_lease_id", table_name="spreadsheet_execution_jobs")
    op.drop_index("ix_spreadsheet_execution_jobs_worker_job_id", table_name="spreadsheet_execution_jobs")
    op.drop_index("ix_spreadsheet_execution_queue", table_name="spreadsheet_execution_jobs")
    op.drop_table("spreadsheet_execution_jobs")
