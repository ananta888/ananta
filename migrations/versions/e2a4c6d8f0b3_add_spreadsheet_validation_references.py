"""Add immutable tenant-scoped Spreadsheet Studio validation references.

Revision ID: e2a4c6d8f0b3
Revises: d1f3a5b7c9e2
"""

import sqlalchemy as sa
from alembic import op

revision = "e2a4c6d8f0b3"
down_revision = "d1f3a5b7c9e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spreadsheet_validation_references",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("reference_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("document_version", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("reference_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("document_version >= 1", name="ck_spreadsheet_validation_reference_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "reference_id"),
    )
    op.create_index(
        "ix_spreadsheet_validation_reference_document",
        "spreadsheet_validation_references",
        ["tenant_id", "document_id", "document_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_spreadsheet_validation_reference_document",
        table_name="spreadsheet_validation_references",
    )
    op.drop_table("spreadsheet_validation_references")
