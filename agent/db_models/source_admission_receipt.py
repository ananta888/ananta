"""Content-free, append-only source admission decision receipts."""

from __future__ import annotations

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class SourceAdmissionReceiptDB(SQLModel, table=True):
    __tablename__ = "source_admission_receipts"
    __table_args__ = (
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
        sa.Index(
            "ix_source_admission_receipts_scope",
            "tenant_id",
            "project_id",
            "source_revision_id",
            "evaluated_at_epoch",
        ),
    )

    receipt_id: str = Field(primary_key=True, max_length=69)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    source_revision_id: str = Field(
        foreign_key="source_revisions.source_revision_id",
        index=True,
        max_length=69,
    )
    decision_state: str = Field(index=True, max_length=16)
    reason_codes: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    revision_digest: str = Field(max_length=64)
    manifest_digest: str = Field(max_length=64)
    policy_digest: str = Field(max_length=64)
    inventory_evidence_digest: str = Field(max_length=64)
    scan_evidence_digest: str = Field(max_length=64)
    admission_digest: str = Field(index=True, max_length=64)
    file_count: int = Field(sa_column=Column(sa.BigInteger(), nullable=False))
    total_bytes: int = Field(sa_column=Column(sa.BigInteger(), nullable=False))
    largest_file_bytes: int = Field(
        sa_column=Column(sa.BigInteger(), nullable=False)
    )
    archive_expansion_ratio: float
    symlink_count: int = Field(sa_column=Column(sa.BigInteger(), nullable=False))
    hardlink_count: int = Field(sa_column=Column(sa.BigInteger(), nullable=False))
    sparse_file_count: int = Field(
        sa_column=Column(sa.BigInteger(), nullable=False)
    )
    archive_count: int = Field(sa_column=Column(sa.BigInteger(), nullable=False))
    binary_count: int = Field(sa_column=Column(sa.BigInteger(), nullable=False))
    secret_findings: int = Field(sa_column=Column(sa.BigInteger(), nullable=False))
    injection_findings: int = Field(
        sa_column=Column(sa.BigInteger(), nullable=False)
    )
    rejected_type_findings: int = Field(
        sa_column=Column(sa.BigInteger(), nullable=False)
    )
    malformed_archive_findings: int = Field(
        sa_column=Column(sa.BigInteger(), nullable=False)
    )
    scan_error_count: int = Field(
        sa_column=Column(sa.BigInteger(), nullable=False)
    )
    evaluated_at_epoch: float
    persisted_at_epoch: float


__all__ = ["SourceAdmissionReceiptDB"]
