"""Persistent Hub authority and lease state for bound index executions."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, BigInteger, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


class KnowledgeIndexExecutionBindingDB(SQLModel, table=True):
    __tablename__ = "knowledge_index_execution_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "idempotency_key_digest",
            name="uq_knowledge_index_execution_scope_idempotency",
        ),
    )

    job_id: str = Field(primary_key=True, max_length=48)
    hub_task_id: str = Field(index=True, max_length=160)
    tenant_id: str = Field(index=True, max_length=160)
    project_id: str = Field(index=True, max_length=160)
    owner_id: str = Field(index=True, max_length=160)
    idempotency_key_digest: str = Field(max_length=64)
    idempotency_fingerprint: str = Field(max_length=64)
    source_revision_id: str = Field(index=True, max_length=69)
    source_revision_digest: str = Field(max_length=64)
    admission_digest: str = Field(max_length=64)
    policy_snapshot_id: str = Field(index=True, max_length=160)
    policy_snapshot_digest: str = Field(max_length=64)
    destination_id: str = Field(index=True, max_length=68)
    destination_digest: str = Field(max_length=64)
    source_access_grant_id: str = Field(index=True, max_length=70)
    source_access_grant_digest: str = Field(max_length=64)
    authority_binding_digest: str = Field(max_length=64)
    file_manifest_digest: str = Field(max_length=64)
    assignment_id: str = Field(index=True, max_length=160)
    assigned_worker_id: str = Field(index=True, max_length=160)
    lease_id: str = Field(index=True, max_length=160)
    lease_generation: int = Field(ge=1)
    lease_expires_epoch_ms: int = Field(
        sa_column=Column(BigInteger, nullable=False)
    )
    attempt: int = Field(default=1, ge=1)
    state: str = Field(index=True, max_length=32)
    lock_version: int = Field(default=1, ge=1)
    envelope_json: dict = Field(sa_column=Column(JSON), default={})
    result_digest: Optional[str] = Field(default=None, max_length=64)
    completion_projection_state: Optional[str] = Field(
        default=None,
        index=True,
        max_length=32,
    )
    completion_projection_lock_version: int = Field(default=0, ge=0)
    completion_projection_digest: Optional[str] = Field(
        default=None,
        max_length=64,
    )
    completion_projection_payload: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON),
    )
    completion_projection_created_at_epoch_ms: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    completion_projection_updated_at_epoch_ms: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    completion_projected_at_epoch_ms: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    created_at_epoch_ms: int = Field(
        sa_column=Column(BigInteger, nullable=False)
    )
    updated_at_epoch_ms: int = Field(
        sa_column=Column(BigInteger, nullable=False)
    )
    completed_at_epoch_ms: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
