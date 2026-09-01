"""Add spreadsheet base-model baselines and training admissions.

Revision ID: a4c6e8f0b2d5
Revises: f3b5d7e9a1c4
"""

import sqlalchemy as sa
from alembic import op

revision = "a4c6e8f0b2d5"
down_revision = "f3b5d7e9a1c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spreadsheet_training_baselines",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("baseline_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("base_model", sa.String(length=512), nullable=False),
        sa.Column("baseline_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "baseline_id"),
    )
    op.create_index(
        "ix_spreadsheet_baseline_owner",
        "spreadsheet_training_baselines",
        ["tenant_id", "owner_id", "baseline_id"],
    )
    op.create_table(
        "spreadsheet_training_admissions",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("admission_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("admission_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('go','no_go')", name="ck_spreadsheet_training_admission_decision"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["spreadsheet_datasets.tenant_id", "spreadsheet_datasets.dataset_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "admission_id"),
    )
    op.create_index(
        "ix_spreadsheet_admission_dataset",
        "spreadsheet_training_admissions",
        ["tenant_id", "dataset_id", "admission_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_spreadsheet_admission_dataset", table_name="spreadsheet_training_admissions")
    op.drop_table("spreadsheet_training_admissions")
    op.drop_index("ix_spreadsheet_baseline_owner", table_name="spreadsheet_training_baselines")
    op.drop_table("spreadsheet_training_baselines")
