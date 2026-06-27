from __future__ import annotations

import time
import uuid
from typing import List, Optional

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class UserDB(SQLModel, table=True):
    __tablename__ = "users"
    username: str = Field(primary_key=True)
    password_hash: str
    role: str = "user"
    mfa_secret: Optional[str] = None
    mfa_enabled: bool = False
    mfa_backup_codes: List[str] = Field(default=[], sa_column=Column(JSON))
    failed_login_attempts: int = Field(default=0)
    lockout_until: Optional[float] = Field(default=None)


class OidcIdentityLinkDB(SQLModel, table=True):
    """Explicit link between an external OIDC subject and a Hub account."""

    __tablename__ = "oidc_identity_links"
    __table_args__ = (
        sa.UniqueConstraint("issuer", "subject", name="uq_oidc_identity_links_issuer_subject"),
        sa.UniqueConstraint("username", "issuer", name="uq_oidc_identity_links_username_issuer"),
    )
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    username: str = Field(index=True, foreign_key="users.username")
    issuer: str = Field(index=True)
    subject: str = Field(index=True)
    created_at: float = Field(default_factory=time.time)


class UserInstructionProfileDB(SQLModel, table=True):
    __tablename__ = "user_instruction_profiles"
    __table_args__ = (sa.UniqueConstraint("owner_username", "name", name="uq_user_instruction_profiles_owner_name"),)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    owner_username: str = Field(index=True, foreign_key="users.username")
    name: str
    prompt_content: str
    profile_metadata: dict = Field(default={}, sa_column=Column(JSON))
    is_active: bool = True
    is_default: bool = False
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class InstructionOverlayDB(SQLModel, table=True):
    __tablename__ = "instruction_overlays"
    __table_args__ = (
        sa.UniqueConstraint("owner_username", "name", name="uq_instruction_overlays_owner_name"),
        sa.Index("ix_instruction_overlays_attachment", "owner_username", "attachment_kind", "attachment_id"),
    )
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    owner_username: str = Field(index=True, foreign_key="users.username")
    name: str
    prompt_content: str
    overlay_metadata: dict = Field(default={}, sa_column=Column(JSON))
    scope: str = "task"
    attachment_kind: Optional[str] = Field(default=None, index=True)
    attachment_id: Optional[str] = Field(default=None, index=True)
    is_active: bool = True
    expires_at: Optional[float] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class LoginAttemptDB(SQLModel, table=True):
    __tablename__ = "login_attempts"
    id: Optional[int] = Field(default=None, primary_key=True)
    ip: str = Field(index=True)
    timestamp: float = Field(default_factory=time.time)


class BannedIPDB(SQLModel, table=True):
    __tablename__ = "banned_ips"
    ip: str = Field(primary_key=True)
    banned_until: float
    reason: Optional[str] = None


class RefreshTokenDB(SQLModel, table=True):
    __tablename__ = "refresh_tokens"
    token: str = Field(primary_key=True)
    username: str = Field(foreign_key="users.username")
    expires_at: float


class PasswordHistoryDB(SQLModel, table=True):
    __tablename__ = "password_history"
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(foreign_key="users.username", index=True)
    password_hash: str
    created_at: float = Field(default_factory=time.time)
