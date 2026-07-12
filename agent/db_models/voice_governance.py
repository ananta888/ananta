from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


class VoiceConsentDB(SQLModel, table=True):
    __tablename__ = "voice_consents"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "profile_id",
            name="uq_voice_consents_scope_profile",
        ),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    profile_id: str = Field(index=True)
    granted: bool = False
    categories: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    retention_days: int = 365
    version: int = 1
    granted_at: float | None = None
    revoked_at: float | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class VoiceReviewDB(SQLModel, table=True):
    __tablename__ = "voice_reviews"
    __table_args__ = (
        sa.Index(
            "ix_voice_reviews_scope_profile",
            "tenant_id",
            "owner_subject",
            "profile_id",
        ),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    profile_id: str = Field(index=True)
    session_id: str | None = Field(default=None, index=True)
    result_ref: str = Field(index=True)
    candidate_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    state: str = Field(default="pending", index=True)
    selected_candidate_id: str | None = None
    correction_ciphertext: str | None = None
    decision_artifact_id: str | None = Field(default=None, index=True)
    version: int = 1
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class VoicePersonalizationProfileDB(SQLModel, table=True):
    __tablename__ = "voice_personalization_profiles"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "profile_id",
            name="uq_voice_personalization_scope_profile",
        ),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    profile_id: str = Field(index=True)
    version: int = 1
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class VoiceFeedbackDB(SQLModel, table=True):
    __tablename__ = "voice_feedback"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "profile_id",
            "source_review_id",
            "kind",
            name="uq_voice_feedback_scope_source_kind",
        ),
        sa.Index(
            "ix_voice_feedback_scope_profile",
            "tenant_id",
            "owner_subject",
            "profile_id",
        ),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    profile_id: str = Field(index=True)
    consent_id: str = Field(index=True)
    consent_version: int
    source_review_id: str = Field(index=True)
    kind: str = Field(index=True)
    source_ciphertext: str | None = None
    target_ciphertext: str | None = None
    feedback_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    active: bool = True
    expires_at: float = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class VoiceGovernanceIdempotencyDB(SQLModel, table=True):
    __tablename__ = "voice_governance_idempotency"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "operation",
            "idempotency_key",
            name="uq_voice_governance_idempotency_scope",
        ),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    operation: str = Field(index=True)
    idempotency_key: str
    request_hash: str
    state: str = Field(default="pending", index=True)
    lease_expires_at: float = Field(default_factory=lambda: time.time() + 300, index=True)
    expires_at: float = Field(default_factory=lambda: time.time() + 86_400, index=True)
    result_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class VoiceRuntimeCleanupDB(SQLModel, table=True):
    """Durable, content-free outbox for deleting runtime stream capabilities."""

    __tablename__ = "voice_runtime_cleanups"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "profile_id",
            "source_session_id",
            name="uq_voice_runtime_cleanup_scope_session",
        ),
        sa.Index(
            "ix_voice_runtime_cleanups_scope_profile",
            "tenant_id",
            "owner_subject",
            "profile_id",
        ),
    )

    id: str = Field(default_factory=lambda: f"voice-runtime-cleanup-{uuid.uuid4()}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    profile_id: str = Field(index=True)
    source_session_id: str = Field(index=True)
    operation: str = Field(index=True)
    cleanup_kind: str = Field(default="runtime_stream_delete", index=True)
    runtime_session_ciphertext: str | None = Field(default=None, repr=False)
    target_digest: str | None = None
    state: str = Field(default="pending", index=True)
    attempt_count: int = 0
    failure_reason_code: str | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class VoiceDeletionTombstoneDB(SQLModel, table=True):
    """Permanent scoped marker preventing deleted Voice data resurrection."""

    __tablename__ = "voice_deletion_tombstones"
    __table_args__ = (
        sa.UniqueConstraint(
            "scope_digest",
            name="uq_voice_deletion_tombstone_scope_digest",
        ),
    )

    id: str = Field(default_factory=lambda: f"voice-deletion-tombstone-{uuid.uuid4()}", primary_key=True)
    scope_digest: str = Field(index=True)
    key_version: str = "hub-hmac-sha256-v1"
    idempotency_key_digests: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    deleted_at: float = Field(default_factory=time.time, index=True)
    reconciliation_count: int = 0
    created_at: float = Field(default_factory=time.time)
    last_reconciled_at: float | None = None


class VoiceResultArtifactDB(SQLModel, table=True):
    __tablename__ = "voice_result_artifacts"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "request_hash",
            "artifact_kind",
            name="uq_voice_result_artifact_scope_request_kind",
        ),
        sa.Index(
            "ix_voice_result_artifacts_scope",
            "tenant_id",
            "owner_subject",
            "created_at",
        ),
    )

    id: str = Field(default_factory=lambda: f"voice-result-{uuid.uuid4()}", primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    profile_id: str = Field(default="default", index=True)
    artifact_kind: str = Field(default="result_envelope", index=True)
    parent_artifact_id: str | None = Field(default=None, index=True)
    request_hash: str = Field(index=True)
    payload_ciphertext: str
    payload_digest: str
    candidate_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    expires_at: float = Field(index=True)
    created_at: float = Field(default_factory=time.time)
