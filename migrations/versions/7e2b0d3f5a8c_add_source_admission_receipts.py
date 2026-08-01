"""add content-free source admission receipts

Revision ID: 7e2b0d3f5a8c
Revises: 6d1a9c2e4f7b
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "7e2b0d3f5a8c"
down_revision = "6d1a9c2e4f7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_admission_receipts",
        sa.Column("receipt_id", sa.String(69), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("source_revision_id", sa.String(69), nullable=False),
        sa.Column("decision_state", sa.String(16), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("revision_digest", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("inventory_evidence_digest", sa.String(64), nullable=False),
        sa.Column("scan_evidence_digest", sa.String(64), nullable=False),
        sa.Column("admission_digest", sa.String(64), nullable=False),
        sa.Column("file_count", sa.BigInteger(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("largest_file_bytes", sa.BigInteger(), nullable=False),
        sa.Column("archive_expansion_ratio", sa.Float(), nullable=False),
        sa.Column("symlink_count", sa.BigInteger(), nullable=False),
        sa.Column("hardlink_count", sa.BigInteger(), nullable=False),
        sa.Column("sparse_file_count", sa.BigInteger(), nullable=False),
        sa.Column("archive_count", sa.BigInteger(), nullable=False),
        sa.Column("binary_count", sa.BigInteger(), nullable=False),
        sa.Column("secret_findings", sa.BigInteger(), nullable=False),
        sa.Column("injection_findings", sa.BigInteger(), nullable=False),
        sa.Column("rejected_type_findings", sa.BigInteger(), nullable=False),
        sa.Column("malformed_archive_findings", sa.BigInteger(), nullable=False),
        sa.Column("scan_error_count", sa.BigInteger(), nullable=False),
        sa.Column("evaluated_at_epoch", sa.Float(), nullable=False),
        sa.Column("persisted_at_epoch", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_revision_id"],
            ["source_revisions.source_revision_id"],
            name="fk_source_admission_receipts_revision",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "admission_digest",
            name="uq_source_admission_receipts_digest",
        ),
        sa.CheckConstraint(
            "decision_state IN ('admitted', 'blocked')",
            name="ck_source_admission_receipts_decision",
        ),
        sa.CheckConstraint(
            "file_count >= 0 AND total_bytes >= 0 "
            "AND largest_file_bytes >= 0 AND symlink_count >= 0 "
            "AND hardlink_count >= 0 AND sparse_file_count >= 0 "
            "AND archive_count >= 0 AND binary_count >= 0 "
            "AND secret_findings >= 0 AND injection_findings >= 0 "
            "AND rejected_type_findings >= 0 "
            "AND malformed_archive_findings >= 0 AND scan_error_count >= 0",
            name="ck_source_admission_receipts_counters",
        ),
        sa.CheckConstraint(
            "archive_expansion_ratio >= 0",
            name="ck_source_admission_receipts_expansion_ratio",
        ),
    )
    for column in (
        "tenant_id",
        "project_id",
        "source_revision_id",
        "decision_state",
        "admission_digest",
    ):
        op.create_index(
            f"ix_source_admission_receipts_{column}",
            "source_admission_receipts",
            [column],
        )
    op.create_index(
        "ix_source_admission_receipts_scope",
        "source_admission_receipts",
        ["tenant_id", "project_id", "source_revision_id", "evaluated_at_epoch"],
    )


def downgrade() -> None:
    op.drop_table("source_admission_receipts")
