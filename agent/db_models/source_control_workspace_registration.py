"""SQL models for safe, path-free workspace registrations."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class SourceControlWorkspaceValidationDB(SQLModel, table=True):
    __tablename__ = "source_control_workspace_validations"
    __table_args__ = (
        Index(
            "ix_source_workspace_validation_scope",
            "tenant_id",
            "project_id",
            "owner_id",
            "expires_at_epoch",
        ),
    )

    handle_digest: str = Field(primary_key=True, max_length=64)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    owner_id: str = Field(index=True, max_length=191)
    folder_handle: str = Field(max_length=96)
    root_fingerprint: str = Field(max_length=64)
    manifest_digest: str = Field(max_length=64)
    expires_at_epoch: float = Field(index=True)
    consumed_at_epoch: float | None = Field(default=None)
    workspace_id: str | None = Field(default=None, max_length=96)
    created_at_epoch: float = Field()


class SourceControlWorkspaceRegistrationDB(SQLModel, table=True):
    __tablename__ = "source_control_workspace_registrations"
    __table_args__ = (
        CheckConstraint(
            "registration_state IN ('active', 'disabled')",
            name="ck_source_workspace_registration_state",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "owner_id",
            "folder_handle",
            name="uq_source_workspace_registration_folder",
        ),
        UniqueConstraint(
            "validation_handle_digest",
            name="uq_sc_workspace_reg_validation",
        ),
        Index(
            "ix_sc_workspace_reg_validation",
            "validation_handle_digest",
        ),
        Index(
            "ix_source_workspace_registration_scope",
            "tenant_id",
            "project_id",
            "owner_id",
            "workspace_id",
        ),
    )

    workspace_id: str = Field(primary_key=True, max_length=96)
    validation_handle_digest: str = Field(
        max_length=64,
    )
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    owner_id: str = Field(index=True, max_length=191)
    folder_handle: str = Field(max_length=96)
    root_fingerprint: str = Field(max_length=64)
    manifest_digest: str = Field(max_length=64)
    registration_state: str = Field(max_length=16)
    read_only: bool = Field(default=True)
    lock_version: int = Field(default=1, ge=1)
    created_at_epoch: float = Field()
    updated_at_epoch: float = Field()


class SourceControlWorkspaceRegistrationAuditDB(SQLModel, table=True):
    __tablename__ = "source_control_workspace_registration_audits"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('allow', 'deny')",
            name="ck_source_workspace_registration_audit_decision",
        ),
        Index(
            "ix_source_workspace_registration_audit_scope",
            "tenant_id",
            "project_id",
            "actor_id",
            "occurred_at_epoch",
        ),
    )

    audit_id: str = Field(primary_key=True, max_length=64)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    actor_id: str = Field(index=True, max_length=191)
    workspace_id_digest: str = Field(max_length=64)
    event_type: str = Field(max_length=64)
    decision: str = Field(max_length=16)
    reason_code: str = Field(max_length=128)
    occurred_at_epoch: float = Field()


__all__ = [
    "SourceControlWorkspaceRegistrationAuditDB",
    "SourceControlWorkspaceRegistrationDB",
    "SourceControlWorkspaceValidationDB",
]
