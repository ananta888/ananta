"""Add production Spreadsheet Studio learning and split-lock store.

Revision ID: f3b5d7e9a1c4
Revises: e2a4c6d8f0b3
"""

import sqlalchemy as sa
from alembic import op

revision = "f3b5d7e9a1c4"
down_revision = "e2a4c6d8f0b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spreadsheet_feedback_events",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("record_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "event_id"),
    )
    op.create_index(
        "ix_spreadsheet_feedback_owner",
        "spreadsheet_feedback_events",
        ["tenant_id", "owner_id", "event_id"],
    )
    op.create_index(
        "ix_spreadsheet_feedback_document",
        "spreadsheet_feedback_events",
        ["tenant_id", "document_id", "event_id"],
    )
    op.create_table(
        "spreadsheet_training_consents",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("consent_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("feedback_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("consent_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_spreadsheet_training_consent_version"),
        sa.CheckConstraint("state IN ('active','revoked')", name="ck_spreadsheet_training_consent_state"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "feedback_id"],
            ["spreadsheet_feedback_events.tenant_id", "spreadsheet_feedback_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "consent_id", "version"),
    )
    op.create_index(
        "ix_spreadsheet_training_consent_feedback",
        "spreadsheet_training_consents",
        ["tenant_id", "feedback_id", "version"],
    )
    op.create_table(
        "spreadsheet_datasets",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_digest", sa.String(length=64), nullable=False),
        sa.Column("split_lock_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "dataset_id"),
    )
    op.create_index("ix_spreadsheet_dataset_owner", "spreadsheet_datasets", ["tenant_id", "owner_id", "dataset_id"])
    op.create_table(
        "spreadsheet_training_lineage",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("job_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["spreadsheet_datasets.tenant_id", "spreadsheet_datasets.dataset_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "job_id"),
    )
    op.create_index(
        "ix_spreadsheet_training_lineage_dataset",
        "spreadsheet_training_lineage",
        ["tenant_id", "dataset_id", "job_id"],
    )
    op.create_table(
        "spreadsheet_consent_revocation_impacts",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("impact_id", sa.String(length=128), nullable=False),
        sa.Column("consent_id", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "impact_id"),
    )
    op.create_index(
        "ix_spreadsheet_revocation_consent",
        "spreadsheet_consent_revocation_impacts",
        ["tenant_id", "consent_id", "impact_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_spreadsheet_revocation_consent", table_name="spreadsheet_consent_revocation_impacts")
    op.drop_table("spreadsheet_consent_revocation_impacts")
    op.drop_index("ix_spreadsheet_training_lineage_dataset", table_name="spreadsheet_training_lineage")
    op.drop_table("spreadsheet_training_lineage")
    op.drop_index("ix_spreadsheet_dataset_owner", table_name="spreadsheet_datasets")
    op.drop_table("spreadsheet_datasets")
    op.drop_index("ix_spreadsheet_training_consent_feedback", table_name="spreadsheet_training_consents")
    op.drop_table("spreadsheet_training_consents")
    op.drop_index("ix_spreadsheet_feedback_document", table_name="spreadsheet_feedback_events")
    op.drop_index("ix_spreadsheet_feedback_owner", table_name="spreadsheet_feedback_events")
    op.drop_table("spreadsheet_feedback_events")
