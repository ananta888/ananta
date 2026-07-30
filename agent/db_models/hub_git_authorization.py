"""Persistent, scope-bound Git connector authorization registrations."""

from __future__ import annotations

from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class _RedactedGitRegistrationMixin:
    """Keep private endpoints and credential references out of diagnostics."""

    def __repr__(self) -> str:
        registration_id = getattr(self, "registration_id", None)
        authorization_state = getattr(self, "authorization_state", None)
        return (
            f"{type(self).__name__}("
            f"registration_id={registration_id!r}, "
            f"authorization_state={authorization_state!r}, "
            "remote_url=<redacted>, credential_ref=<opaque>)"
        )

    __str__ = __repr__


class HubGitRemoteRegistrationDB(
    _RedactedGitRegistrationMixin,
    SQLModel,
    table=True,
):
    """Current optimistic-locking head for one scoped Git registration."""

    __tablename__ = "hub_git_remote_registrations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "owner_id",
            "connection_ref",
            "repository_key",
            name="uq_hub_git_remote_registration_scope",
        ),
        Index(
            "ix_hub_git_remote_registration_lookup",
            "tenant_id",
            "project_id",
            "owner_id",
            "connection_ref",
            "repository_key",
        ),
    )

    registration_id: str = Field(primary_key=True, max_length=64)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    owner_id: str = Field(index=True, max_length=191)
    connection_ref: str = Field(max_length=192)
    repository_key: str = Field(default="", max_length=201)
    authorization_kind: str = Field(max_length=32)
    remote_url: str = Field(sa_column=Column(Text, nullable=False))
    credential_ref: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    credential_username: str | None = Field(default=None, max_length=256)
    authorization_state: str = Field(max_length=32)
    granted_scopes_json: str = Field(
        default="[]",
        sa_column=Column(Text, nullable=False),
    )
    current_revision: int = Field(default=1, ge=1)
    lock_version: int = Field(default=1, ge=1)
    created_at_epoch: int = Field(ge=0)
    updated_at_epoch: int = Field(ge=0)


class HubGitRemoteRegistrationRevisionDB(
    _RedactedGitRegistrationMixin,
    SQLModel,
    table=True,
):
    """Append-only snapshot of a registration revision."""

    __tablename__ = "hub_git_remote_registration_revisions"
    __table_args__ = (
        UniqueConstraint(
            "registration_id",
            "revision",
            name="uq_hub_git_remote_registration_revision",
        ),
        Index(
            "ix_hub_git_remote_registration_revision_history",
            "registration_id",
            "revision",
        ),
    )

    revision_id: str = Field(primary_key=True, max_length=64)
    registration_id: str = Field(
        foreign_key="hub_git_remote_registrations.registration_id",
        max_length=64,
    )
    revision: int = Field(ge=1)
    tenant_id: str = Field(max_length=191)
    project_id: str = Field(max_length=191)
    owner_id: str = Field(max_length=191)
    connection_ref: str = Field(max_length=192)
    repository_key: str = Field(default="", max_length=201)
    authorization_kind: str = Field(max_length=32)
    remote_url: str = Field(sa_column=Column(Text, nullable=False))
    credential_ref: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    credential_username: str | None = Field(default=None, max_length=256)
    authorization_state: str = Field(max_length=32)
    granted_scopes_json: str = Field(
        default="[]",
        sa_column=Column(Text, nullable=False),
    )
    snapshot_digest: str = Field(max_length=64)
    actor_id: str = Field(max_length=192)
    reason_code: str = Field(max_length=192)
    created_at_epoch: int = Field(ge=0)


class HubGitRemoteRegistrationAuditDB(SQLModel, table=True):
    """Content-free mutation audit; intentionally stores no endpoint or secret ref."""

    __tablename__ = "hub_git_remote_registration_audits"
    __table_args__ = (
        Index(
            "ix_hub_git_remote_registration_audit_scope",
            "tenant_id",
            "project_id",
            "owner_id",
            "occurred_at_epoch",
        ),
        Index(
            "ix_hub_git_remote_registration_audit_registration",
            "registration_id",
            "revision",
        ),
    )

    audit_id: str = Field(primary_key=True, max_length=64)
    registration_id: str = Field(
        foreign_key="hub_git_remote_registrations.registration_id",
        max_length=64,
    )
    tenant_id: str = Field(max_length=191)
    project_id: str = Field(max_length=191)
    owner_id: str = Field(max_length=191)
    revision: int = Field(ge=1)
    action: str = Field(max_length=64)
    previous_authorization_state: str | None = Field(
        default=None,
        max_length=32,
    )
    authorization_state: str = Field(max_length=32)
    reason_code: str = Field(max_length=192)
    actor_id: str = Field(max_length=192)
    registration_digest: str = Field(max_length=64)
    occurred_at_epoch: int = Field(ge=0)


__all__ = [
    "HubGitRemoteRegistrationAuditDB",
    "HubGitRemoteRegistrationDB",
    "HubGitRemoteRegistrationRevisionDB",
]
