"""Durable, bounded SFU broadcast user-intent state and content-free audit."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SfuBroadcastUserIntentDB(SQLModel, table=True):
    __tablename__ = "sfu_broadcast_user_intents"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "room_id", name="uq_sfu_user_intent_tenant_room"
        ),
        sa.CheckConstraint(
            "state IN ('inactive','active')", name="ck_sfu_user_intent_state"
        ),
        sa.CheckConstraint(
            "requested_action IN ('start','stop','set_preferences','data_saver','audio_only','quality_preference')",
            name="ck_sfu_user_intent_action",
        ),
        sa.CheckConstraint(
            "quality_preference IS NULL OR quality_preference IN ('auto','low','medium','high')",
            name="ck_sfu_user_intent_quality",
        ),
        sa.CheckConstraint("version >= 1", name="ck_sfu_user_intent_version"),
        sa.Index("ix_sfu_user_intent_retention", "retain_until", "state"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    tenant_id: str = Field(index=True)
    room_id: str = Field(index=True)
    state: str
    requested_action: str
    data_saver: bool | None = None
    audio_only: bool | None = None
    quality_preference: str | None = None
    policy_version: int
    admission_epoch: int | None = None
    membership_epoch: int | None = None
    version: int
    last_operation_id: str = Field(repr=False)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    retain_until: datetime = Field(index=True)


class SfuBroadcastCommandAuditDB(SQLModel, table=True):
    __tablename__ = "sfu_broadcast_command_audits"
    __table_args__ = (
        sa.CheckConstraint(
            "action IN ('start','stop','set_preferences','data_saver','audio_only','quality_preference')",
            name="ck_sfu_command_audit_action",
        ),
        sa.CheckConstraint(
            "state IN ('inactive','active','denied','unknown')",
            name="ck_sfu_command_audit_state",
        ),
        sa.CheckConstraint(
            "quality_preference IS NULL OR quality_preference IN ('auto','low','medium','high')",
            name="ck_sfu_command_audit_quality",
        ),
        sa.Index(
            "ix_sfu_command_audit_tenant_retention",
            "tenant_diagnostic_ref",
            "retain_until",
        ),
    )

    operation_id: str = Field(primary_key=True)
    intent_id: str | None = Field(
        default=None,
        sa_column=sa.Column(
            sa.String(length=36),
            sa.ForeignKey("sfu_broadcast_user_intents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    tenant_diagnostic_ref: str = Field(repr=False, index=True)
    room_diagnostic_ref: str = Field(repr=False)
    actor_diagnostic_ref: str = Field(repr=False)
    actor_role: str
    action: str
    reason: str
    outcome: str
    reason_code: str
    request_digest: str = Field(repr=False)
    expected_version: int
    effective_version: int
    state: str
    data_saver: bool | None = None
    audio_only: bool | None = None
    quality_preference: str | None = None
    policy_version: int
    admission_epoch: int | None = None
    membership_epoch: int | None = None
    accepted: bool
    created_at: datetime = Field(default_factory=_utcnow)
    retain_until: datetime = Field(index=True)
