"""Secret-free persistence models for scoped public Git remotes."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class _RedactedPublicRemoteMixin:
    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"provider={getattr(self, 'provider', None)!r}, "
            "endpoint=<redacted>)"
        )

    __str__ = __repr__


class SourceControlPublicRemoteValidationDB(
    _RedactedPublicRemoteMixin,
    SQLModel,
    table=True,
):
    """One short-lived capability; the bearer value itself is never stored."""

    __tablename__ = "source_control_public_remote_validations"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('github_public', 'https_git')",
            name="ck_source_public_remote_validation_provider",
        ),
        Index(
            "ix_source_public_remote_validation_scope",
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
    provider: str = Field(max_length=32)
    host: str = Field(max_length=253)
    repository_path: str = Field(max_length=512)
    requested_ref: str = Field(max_length=240)
    commit_sha: str = Field(max_length=64)
    policy_digest: str = Field(max_length=64)
    binding_digest: str = Field(max_length=64)
    expires_at_epoch: float = Field(index=True)
    consumed_at_epoch: float | None = Field(default=None)
    remote_id: str | None = Field(default=None, max_length=96)
    created_at_epoch: float = Field()


class SourceControlPublicRemoteDB(
    _RedactedPublicRemoteMixin,
    SQLModel,
    table=True,
):
    """Durable structured endpoint selected only through an opaque ID."""

    __tablename__ = "source_control_public_remotes"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('github_public', 'https_git')",
            name="ck_source_public_remote_provider",
        ),
        UniqueConstraint(
            "handle_digest",
            name="uq_source_public_remote_handle",
        ),
        Index(
            "ix_source_public_remote_scope",
            "tenant_id",
            "project_id",
            "owner_id",
            "remote_id",
        ),
    )

    remote_id: str = Field(primary_key=True, max_length=96)
    handle_digest: str = Field(index=True, max_length=64)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    owner_id: str = Field(index=True, max_length=191)
    provider: str = Field(max_length=32)
    host: str = Field(max_length=253)
    repository_path: str = Field(max_length=512)
    requested_ref: str = Field(max_length=240)
    validated_commit_sha: str = Field(max_length=64)
    policy_digest: str = Field(max_length=64)
    binding_digest: str = Field(max_length=64)
    created_at_epoch: float = Field()


class SourceControlPublicRemoteAuditDB(SQLModel, table=True):
    """Content-free allow/deny trail without endpoints, URLs, or credentials."""

    __tablename__ = "source_control_public_remote_audits"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('allow', 'deny')",
            name="ck_source_public_remote_audit_decision",
        ),
        Index(
            "ix_source_public_remote_audit_scope",
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
    event_type: str = Field(max_length=64)
    decision: str = Field(max_length=16)
    reason_code: str = Field(max_length=128)
    binding_digest: str = Field(max_length=64)
    occurred_at_epoch: float = Field()


__all__ = [
    "SourceControlPublicRemoteAuditDB",
    "SourceControlPublicRemoteDB",
    "SourceControlPublicRemoteValidationDB",
]
