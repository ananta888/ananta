"""Append-only scientific skill provenance receipts."""

from __future__ import annotations

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class ScientificSkillProvenanceReceiptDB(SQLModel, table=True):
    __tablename__ = "scientific_skill_provenance_receipts"
    __table_args__ = (
        sa.Index("ix_scientific_skill_receipt_scope", "tenant_id", "project_id", "task_id"),
    )

    receipt_digest: str = Field(primary_key=True, max_length=64)
    tenant_id: str = Field(max_length=128)
    project_id: str = Field(max_length=128)
    task_id: str = Field(max_length=128)
    entry_id: str = Field(max_length=80)
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at_epoch: float


__all__ = ["ScientificSkillProvenanceReceiptDB"]
