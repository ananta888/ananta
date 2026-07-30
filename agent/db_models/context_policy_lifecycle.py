"""Persistent records for immutable Context Policy versions and mutations."""

from __future__ import annotations

import sqlalchemy as sa
from sqlmodel import Column, Field, JSON, SQLModel


class ContextPolicyVersionDB(SQLModel, table=True):
    __tablename__ = "source_control_context_policy_versions"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "policy_id",
            "version",
            name="uq_sc_context_policy_scope_version",
        ),
        sa.Index(
            "uq_sc_context_policy_active_scope",
            "tenant_id",
            "project_id",
            "policy_id",
            unique=True,
            sqlite_where=sa.text("state = 'active'"),
            postgresql_where=sa.text("state = 'active'"),
        ),
    )

    record_id: str = Field(primary_key=True, max_length=64)
    tenant_id: str = Field(index=True, max_length=255)
    project_id: str = Field(index=True, max_length=255)
    policy_id: str = Field(index=True, max_length=255)
    version: int = Field(index=True)
    state: str = Field(index=True, max_length=16)
    document_json: dict = Field(sa_column=Column(JSON))
    policy_digest: str = Field(index=True, max_length=64)
    etag: str = Field(index=True, max_length=64)
    created_by: str = Field(max_length=255)
    created_at: str = Field(max_length=40)
    updated_by: str = Field(max_length=255)
    updated_at: str = Field(max_length=40)


class ContextPolicyMutationDB(SQLModel, table=True):
    __tablename__ = "source_control_context_policy_mutations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "policy_id",
            "operation",
            "idempotency_key",
            name="uq_sc_context_policy_mutation_key",
        ),
    )

    mutation_id: str = Field(primary_key=True, max_length=64)
    tenant_id: str = Field(index=True, max_length=255)
    project_id: str = Field(index=True, max_length=255)
    policy_id: str = Field(index=True, max_length=255)
    operation: str = Field(index=True, max_length=32)
    idempotency_key: str = Field(max_length=64)
    request_digest: str = Field(max_length=64)
    result_version: int
    result_etag: str = Field(max_length=64)
    result_json: dict = Field(sa_column=Column(JSON))
    created_at: str = Field(max_length=40)


class ContextPolicyLifecycleAuditDB(SQLModel, table=True):
    __tablename__ = "source_control_context_policy_audit"

    audit_id: str = Field(primary_key=True, max_length=64)
    operation: str = Field(index=True, max_length=32)
    actor_id: str = Field(index=True, max_length=255)
    tenant_id: str = Field(index=True, max_length=255)
    project_id: str = Field(index=True, max_length=255)
    policy_id: str = Field(index=True, max_length=255)
    version: int
    policy_digest: str = Field(max_length=64)
    reason_code: str = Field(max_length=255)
    created_at: str = Field(max_length=40)


__all__ = [
    "ContextPolicyLifecycleAuditDB",
    "ContextPolicyMutationDB",
    "ContextPolicyVersionDB",
]
