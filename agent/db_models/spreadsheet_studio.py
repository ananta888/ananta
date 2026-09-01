"""Durable Hub-owned Spreadsheet Studio document projections."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SpreadsheetDocumentDB(SQLModel, table=True):
    __tablename__ = "spreadsheet_documents"
    __table_args__ = (
        sa.CheckConstraint("current_version >= 1", name="ck_spreadsheet_document_current_version"),
        sa.Index("ix_spreadsheet_document_owner", "tenant_id", "owner_id", "document_id"),
    )

    tenant_id: str = Field(primary_key=True, max_length=128)
    document_id: str = Field(primary_key=True, max_length=128)
    owner_id: str = Field(max_length=128)
    current_version: int
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SpreadsheetDocumentVersionDB(SQLModel, table=True):
    __tablename__ = "spreadsheet_document_versions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version >= 1", name="ck_spreadsheet_version_number"),
        sa.CheckConstraint("parent_version IS NULL OR parent_version < version", name="ck_spreadsheet_version_parent"),
        sa.CheckConstraint(
            "state IN ('published','rejected','expired','deleted','erased')",
            name="ck_spreadsheet_version_state",
        ),
        sa.Index("ix_spreadsheet_version_document", "tenant_id", "document_id", "version"),
    )

    tenant_id: str = Field(primary_key=True, max_length=128)
    document_id: str = Field(primary_key=True, max_length=128)
    version: int = Field(primary_key=True)
    parent_version: int | None = None
    state: str = Field(max_length=24)
    snapshot_digest: str = Field(max_length=64, repr=False)
    payload_digest: str = Field(max_length=64, repr=False)
    payload_json: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)


class SpreadsheetProposalResultDB(SQLModel, table=True):
    __tablename__ = "spreadsheet_proposal_results"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("base_version >= 1", name="ck_spreadsheet_proposal_base_version"),
        sa.Index("ix_spreadsheet_proposal_document", "tenant_id", "document_id", "base_version"),
    )

    tenant_id: str = Field(primary_key=True, max_length=128)
    proposal_id: str = Field(primary_key=True, max_length=128)
    document_id: str = Field(max_length=128)
    base_version: int
    proposal_digest: str = Field(max_length=64, repr=False)
    result_digest: str = Field(max_length=64, repr=False)
    payload_json: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)


class SpreadsheetExecutionJobDB(SQLModel, table=True):
    """Hub-owned queue projection for one immutable spreadsheet assignment."""

    __tablename__ = "spreadsheet_execution_jobs"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "proposal_id", name="uq_spreadsheet_execution_proposal"),
        sa.CheckConstraint(
            "status IN ('dispatch_pending','queued','leased','completed','failed','cancelled')",
            name="ck_spreadsheet_execution_status",
        ),
        sa.Index("ix_spreadsheet_execution_queue", "status", "created_at", "job_id"),
    )

    tenant_id: str = Field(primary_key=True, max_length=128)
    job_id: str = Field(primary_key=True, max_length=128)
    proposal_id: str = Field(max_length=128)
    document_id: str = Field(max_length=128)
    principal_id: str = Field(max_length=128)
    proposal_digest: str = Field(max_length=64, repr=False)
    assignment_digest: str = Field(max_length=64, repr=False)
    assignment_json: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    status: str = Field(default="dispatch_pending", max_length=24)
    worker_job_id: str | None = Field(default=None, max_length=128, index=True)
    slot_lease_id: str | None = Field(default=None, max_length=128, index=True)
    worker_id: str | None = Field(default=None, max_length=512)
    queue_position: int | None = None
    callback_jti: str | None = Field(default=None, max_length=128, repr=False)
    artifact_handle_jti: str | None = Field(default=None, max_length=128, repr=False)
    claimed_at: datetime | None = None
    artifact_consumed_at: datetime | None = None
    callback_payload_digest: str | None = Field(default=None, max_length=64, repr=False)
    result_digest: str | None = Field(default=None, max_length=64, repr=False)
    result_json: str | None = Field(default=None, sa_column=sa.Column(sa.Text(), nullable=True))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SpreadsheetValidationReferenceDB(SQLModel, table=True):
    """Immutable Hub-owned snapshot used by tenant-bound validation rules."""

    __tablename__ = "spreadsheet_validation_references"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("document_version >= 1", name="ck_spreadsheet_validation_reference_version"),
        sa.Index(
            "ix_spreadsheet_validation_reference_document",
            "tenant_id",
            "document_id",
            "document_version",
        ),
    )

    tenant_id: str = Field(primary_key=True, max_length=128)
    reference_id: str = Field(primary_key=True, max_length=128)
    document_id: str = Field(max_length=128)
    document_version: int
    snapshot_digest: str = Field(max_length=64, repr=False)
    reference_digest: str = Field(max_length=64, repr=False)
    payload_json: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)


class SpreadsheetFeedbackEventDB(SQLModel, table=True):
    """Immutable, masked feedback event owned by the Hub."""

    __tablename__ = "spreadsheet_feedback_events"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["spreadsheet_documents.tenant_id", "spreadsheet_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.Index("ix_spreadsheet_feedback_owner", "tenant_id", "owner_id", "event_id"),
        sa.Index("ix_spreadsheet_feedback_document", "tenant_id", "document_id", "event_id"),
    )

    tenant_id: str = Field(primary_key=True, max_length=128)
    event_id: str = Field(primary_key=True, max_length=128)
    owner_id: str = Field(max_length=128)
    document_id: str = Field(max_length=128)
    record_digest: str = Field(max_length=64, repr=False)
    payload_json: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)


class SpreadsheetTrainingConsentDB(SQLModel, table=True):
    """Append-only consent revision bound to one exact masked record."""

    __tablename__ = "spreadsheet_training_consents"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "feedback_id"],
            ["spreadsheet_feedback_events.tenant_id", "spreadsheet_feedback_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version >= 1", name="ck_spreadsheet_training_consent_version"),
        sa.CheckConstraint("state IN ('active','revoked')", name="ck_spreadsheet_training_consent_state"),
        sa.Index("ix_spreadsheet_training_consent_feedback", "tenant_id", "feedback_id", "version"),
    )

    tenant_id: str = Field(primary_key=True, max_length=128)
    consent_id: str = Field(primary_key=True, max_length=128)
    version: int = Field(primary_key=True)
    feedback_id: str = Field(max_length=128)
    owner_id: str = Field(max_length=128)
    state: str = Field(max_length=16)
    consent_digest: str = Field(max_length=64, repr=False)
    payload_json: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)


class SpreadsheetDatasetDB(SQLModel, table=True):
    """Immutable dataset manifest with a digest-bound split lock."""

    __tablename__ = "spreadsheet_datasets"
    __table_args__ = (sa.Index("ix_spreadsheet_dataset_owner", "tenant_id", "owner_id", "dataset_id"),)

    tenant_id: str = Field(primary_key=True, max_length=128)
    dataset_id: str = Field(primary_key=True, max_length=128)
    owner_id: str = Field(max_length=128)
    dataset_digest: str = Field(max_length=64, repr=False)
    split_lock_digest: str = Field(max_length=64, repr=False)
    payload_json: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)


class SpreadsheetTrainingLineageDB(SQLModel, table=True):
    """Immutable link from a governed dataset to a training job and outputs."""

    __tablename__ = "spreadsheet_training_lineage"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["spreadsheet_datasets.tenant_id", "spreadsheet_datasets.dataset_id"],
            ondelete="CASCADE",
        ),
        sa.Index("ix_spreadsheet_training_lineage_dataset", "tenant_id", "dataset_id", "job_id"),
    )

    tenant_id: str = Field(primary_key=True, max_length=128)
    job_id: str = Field(primary_key=True, max_length=128)
    dataset_id: str = Field(max_length=128)
    owner_id: str = Field(max_length=128)
    payload_json: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)


class SpreadsheetConsentRevocationImpactDB(SQLModel, table=True):
    """Durable automatic fencing intent created atomically with revocation."""

    __tablename__ = "spreadsheet_consent_revocation_impacts"
    __table_args__ = (sa.Index("ix_spreadsheet_revocation_consent", "tenant_id", "consent_id", "impact_id"),)

    tenant_id: str = Field(primary_key=True, max_length=128)
    impact_id: str = Field(primary_key=True, max_length=128)
    consent_id: str = Field(max_length=128)
    payload_json: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)


__all__ = [
    "SpreadsheetConsentRevocationImpactDB",
    "SpreadsheetDatasetDB",
    "SpreadsheetDocumentDB",
    "SpreadsheetDocumentVersionDB",
    "SpreadsheetExecutionJobDB",
    "SpreadsheetFeedbackEventDB",
    "SpreadsheetProposalResultDB",
    "SpreadsheetTrainingConsentDB",
    "SpreadsheetTrainingLineageDB",
    "SpreadsheetValidationReferenceDB",
]
