"""Add durable Spreadsheet Studio documents, versions and proposal results.

Revision ID: b9d1f3a5c7e0
Revises: a8c0e2f4b6d9
"""

import sqlalchemy as sa
from alembic import op

revision = "b9d1f3a5c7e0"
down_revision = "a8c0e2f4b6d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spreadsheet_documents",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("current_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("current_version >= 1", name="ck_spreadsheet_document_current_version"),
        sa.PrimaryKeyConstraint("tenant_id", "document_id"),
    )
    op.create_index(
        "ix_spreadsheet_document_owner",
        "spreadsheet_documents",
        ["tenant_id", "owner_id", "document_id"],
    )
    op.create_table(
        "spreadsheet_document_versions",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("parent_version", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_spreadsheet_version_number"),
        sa.CheckConstraint("parent_version IS NULL OR parent_version < version", name="ck_spreadsheet_version_parent"),
        sa.CheckConstraint(
            "state IN ('published','rejected','expired','deleted','erased')",
            name="ck_spreadsheet_version_state",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "document_id", "version"),
    )
    op.create_index(
        "ix_spreadsheet_version_document",
        "spreadsheet_document_versions",
        ["tenant_id", "document_id", "version"],
    )
    op.create_table(
        "spreadsheet_proposal_results",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("proposal_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("base_version", sa.BigInteger(), nullable=False),
        sa.Column("proposal_digest", sa.String(length=64), nullable=False),
        sa.Column("result_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("base_version >= 1", name="ck_spreadsheet_proposal_base_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "proposal_id"),
    )
    op.create_index(
        "ix_spreadsheet_proposal_document",
        "spreadsheet_proposal_results",
        ["tenant_id", "document_id", "base_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_spreadsheet_proposal_document", table_name="spreadsheet_proposal_results")
    op.drop_table("spreadsheet_proposal_results")
    op.drop_index("ix_spreadsheet_version_document", table_name="spreadsheet_document_versions")
    op.drop_table("spreadsheet_document_versions")
    op.drop_index("ix_spreadsheet_document_owner", table_name="spreadsheet_documents")
    op.drop_table("spreadsheet_documents")
