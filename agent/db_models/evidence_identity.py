"""Persistent Hub authority for source and run evidence identities."""

from __future__ import annotations

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class HubSourceEvidenceIdentityDB(SQLModel, table=True):
    __tablename__ = "hub_source_evidence_identities"
    __table_args__ = (
        sa.Index(
            "ix_hub_source_evidence_scope",
            "tenant_id",
            "project_id",
            "evidence_scope",
            "state",
        ),
    )

    tenant_id: str = Field(primary_key=True, max_length=191)
    project_id: str = Field(primary_key=True, max_length=191)
    source_id: str = Field(primary_key=True, max_length=192)
    origin_type: str = Field(max_length=64)
    origin_digest: str = Field(max_length=64)
    content_digest: str = Field(max_length=64)
    policy_digest: str = Field(max_length=64)
    evidence_scope: str = Field(max_length=32)
    synthetic: bool = Field(default=False)
    issuer: str = Field(max_length=128)
    state: str = Field(max_length=32)
    binding_digest: str = Field(unique=True, index=True, max_length=64)
    created_at_epoch: float


class HubRunEvidenceIdentityDB(SQLModel, table=True):
    __tablename__ = "hub_run_evidence_identities"
    __table_args__ = (
        sa.Index(
            "ix_hub_run_evidence_scope",
            "tenant_id",
            "project_id",
            "task_id",
            "state",
        ),
    )

    tenant_id: str = Field(primary_key=True, max_length=191)
    project_id: str = Field(primary_key=True, max_length=191)
    run_id: str = Field(primary_key=True, max_length=192)
    task_id: str = Field(index=True, max_length=191)
    assignment_id: str = Field(index=True, max_length=191)
    dispatch_lease_id: str = Field(index=True, max_length=191)
    repository_revision: str = Field(max_length=64)
    input_digest: str = Field(max_length=64)
    execution_profile_digest: str = Field(max_length=64)
    environment_digest: str = Field(max_length=64)
    source_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    evidence_scope: str = Field(max_length=32)
    synthetic: bool = Field(default=False)
    issuer: str = Field(max_length=128)
    reservation_key_digest: str = Field(unique=True, index=True, max_length=64)
    binding_digest: str = Field(unique=True, index=True, max_length=64)
    state: str = Field(max_length=32)
    result_digest: str | None = Field(default=None, max_length=64)
    created_at_epoch: float
    updated_at_epoch: float


__all__ = ["HubRunEvidenceIdentityDB", "HubSourceEvidenceIdentityDB"]
