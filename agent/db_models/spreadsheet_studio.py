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


__all__ = [
    "SpreadsheetDocumentDB",
    "SpreadsheetDocumentVersionDB",
    "SpreadsheetProposalResultDB",
]
