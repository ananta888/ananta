"""Hub-owned persistence models for governed speech evidence.

All columns are content-free except authenticated ciphertext.  Public read
models must never expose the ciphertext, wrapped DEKs or wrapping nonces.
"""

from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


class SpeechEvidenceConsentDB(SQLModel, table=True):
    __tablename__ = "speech_evidence_consents"
    __table_args__ = (
        sa.Index("ix_speech_evidence_consents_scope", "tenant_id", "owner_subject", "pair_id", "session_id"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    speaker_id: str = Field(index=True)
    recipient_id: str = Field(index=True)
    pair_id: str = Field(index=True)
    session_id: str = Field(index=True)
    session_epoch: int = Field(index=True)
    direction: str = Field(index=True)
    purpose: str = Field(index=True)
    scope_digest: str = Field(index=True)
    consent_digest: str = Field(index=True)
    scope_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    required_signers: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    signature_digests: dict = Field(default_factory=dict, sa_column=Column(JSON))
    state: str = Field(default="active", index=True)
    consent_version: int = 1
    revocation_epoch: int = 0
    issued_at_ms: int
    expires_at_ms: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SpeechEvidenceKeyDB(SQLModel, table=True):
    __tablename__ = "speech_evidence_keys"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "artifact_ref", name="uq_speech_evidence_key_artifact"),
        sa.Index("ix_speech_evidence_keys_scope", "tenant_id", "pair_id", "purpose", "key_epoch"),
    )

    id: str = Field(default_factory=lambda: _id("speech-key"), primary_key=True)
    tenant_id: str = Field(index=True)
    pair_id: str = Field(index=True)
    purpose: str = Field(index=True)
    artifact_class: str = Field(index=True)
    artifact_ref: str = Field(index=True)
    key_epoch: int = Field(index=True)
    wrapping_epoch: int = Field(index=True)
    wrapping_algorithm: str = "AES-256-GCM+HKDF-SHA256-v1"
    wrapped_dek: bytes | None = Field(default=None, sa_column=Column(sa.LargeBinary, nullable=True), repr=False)
    wrapping_nonce: bytes | None = Field(default=None, sa_column=Column(sa.LargeBinary, nullable=True), repr=False)
    destroyed_at_ms: int | None = Field(default=None, index=True)
    created_at_ms: int
    rotated_at_ms: int | None = None


class SpeechEvidenceDB(SQLModel, table=True):
    __tablename__ = "speech_evidence"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "pair_id",
            "session_id",
            "evidence_class",
            "content_digest",
            name="uq_speech_evidence_scoped_digest",
        ),
        sa.Index("ix_speech_evidence_expiry", "state", "expires_at_ms"),
        sa.Index("ix_speech_evidence_scope", "tenant_id", "owner_subject", "pair_id", "session_id"),
    )

    id: str = Field(default_factory=lambda: _id("speech-evidence"), primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    pair_id: str = Field(index=True)
    session_id: str = Field(index=True)
    session_epoch: int = Field(index=True)
    speaker_scope_digest: str = Field(index=True)
    utterance_family_id: str = Field(index=True)
    evidence_class: str = Field(index=True)
    purpose: str = Field(index=True)
    consent_id: str = Field(index=True)
    consent_version: int
    revocation_epoch: int
    content_digest: str = Field(index=True)
    cipher_content_digest: str
    source_digest: str = Field(index=True)
    provenance_digest: str = Field(index=True)
    key_id: str = Field(index=True)
    nonce: bytes = Field(sa_column=Column(sa.LargeBinary, nullable=False), repr=False)
    ciphertext: bytes = Field(sa_column=Column(sa.LargeBinary, nullable=False), repr=False)
    byte_count: int
    retention_seconds: int
    state: str = Field(default="quarantined", index=True)
    admission_digest: str | None = Field(default=None, index=True)
    version: int = 1
    expires_at_ms: int = Field(index=True)
    created_at_ms: int
    updated_at_ms: int


class SpeechEvidenceAdmissionDB(SQLModel, table=True):
    __tablename__ = "speech_evidence_admissions"
    __table_args__ = (sa.UniqueConstraint("evidence_id", "policy_version", name="uq_speech_admission_evidence_policy"),)

    id: str = Field(default_factory=lambda: _id("speech-admission"), primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    evidence_id: str = Field(index=True)
    evidence_digest: str = Field(index=True)
    admission_digest: str = Field(index=True, unique=True)
    policy_version: str
    decision: str = Field(index=True)
    reason_codes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))
    consent_version: int
    revocation_epoch: int
    created_at_ms: int


class SpeechCurationTaskDB(SQLModel, table=True):
    __tablename__ = "speech_curation_tasks"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "admission_digest", name="uq_speech_curation_admission"),)

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    parent_task_id: str = Field(index=True)
    admission_digest: str = Field(index=True)
    evidence_refs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    consent_id: str = Field(index=True)
    consent_version: int
    revocation_epoch: int
    fencing_token: int = 1
    task_binding: dict = Field(default_factory=dict, sa_column=Column(JSON))
    state: str = Field(default="queued", index=True)
    result_artifact_ref: str | None = None
    result_artifact_digest: str | None = None
    executor_id: str | None = Field(default=None, index=True)
    executor_url: str | None = None
    deadline_epoch_ms: int = Field(index=True)
    created_at_ms: int
    updated_at_ms: int


class SpeechPeerEvidenceCurationDB(SQLModel, table=True):
    """Content-free projection linking one peer offer to Hub curation.

    The transferred transcript remains in ``SpeechEvidenceDB`` as authenticated
    ciphertext.  This projection intentionally stores only digests, consent
    fences, the Hub receipt and immutable dataset bindings.
    """

    __tablename__ = "speech_peer_evidence_curations"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "owner_subject", "offer_id", name="uq_speech_peer_curation_offer"),
        sa.UniqueConstraint(
            "tenant_id", "owner_subject", "admission_digest", name="uq_speech_peer_curation_admission"
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    offer_id: str = Field(index=True)
    pair_id: str = Field(index=True)
    session_id: str = Field(index=True)
    session_epoch: int = Field(index=True)
    evidence_id: str = Field(index=True)
    admission_digest: str = Field(index=True)
    source_binding_digest: str = Field(index=True)
    contributor_digest: str = Field(index=True)
    data_class: str = Field(index=True)
    direction: str = Field(index=True)
    receipt_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    curation_task_id: str | None = Field(default=None, index=True)
    consent_id: str = Field(index=True)
    consent_version: int
    revocation_epoch: int
    dataset_id: str = Field(index=True)
    dataset_parent_digest: str | None = Field(default=None, index=True)
    dataset_manifest_digest: str | None = Field(default=None, index=True)
    state: str = Field(default="admitted", index=True)
    created_at_ms: int
    updated_at_ms: int


class SpeechPeerCurationArtifactDB(SQLModel, table=True):
    """Content-free worker artifact staged before fenced result admission."""

    __tablename__ = "speech_peer_curation_artifacts"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "task_id", name="uq_speech_peer_curation_artifact_task"),
        sa.UniqueConstraint("tenant_id", "artifact_digest", name="uq_speech_peer_curation_artifact_digest"),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    task_id: str = Field(index=True)
    admission_digest: str = Field(index=True)
    artifact_ref: str = Field(index=True)
    artifact_digest: str = Field(index=True)
    artifact_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    consent_version: int
    revocation_epoch: int
    fencing_token: int
    state: str = Field(default="quarantined", index=True)
    created_at_ms: int
    updated_at_ms: int


class SpeechDatasetManifestDB(SQLModel, table=True):
    __tablename__ = "speech_dataset_manifests"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "owner_subject", "dataset_id", "version", name="uq_speech_dataset_manifest_version"
        ),
        sa.UniqueConstraint("tenant_id", "owner_subject", "manifest_digest", name="uq_speech_dataset_manifest_digest"),
    )

    id: str = Field(default_factory=lambda: _id("speech-manifest"), primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    dataset_id: str = Field(index=True)
    version: str = Field(index=True)
    parent_digest: str | None = Field(default=None, index=True)
    manifest_digest: str = Field(index=True)
    manifest_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    record_count: int
    consent_refs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    revocation_epoch: int
    status: str = Field(default="active", index=True)
    created_at_ms: int


class SpeechLineageNodeDB(SQLModel, table=True):
    __tablename__ = "speech_lineage_nodes"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "owner_subject", "digest", "kind", name="uq_speech_lineage_node"),
        sa.Index("ix_speech_lineage_node_scope", "tenant_id", "owner_subject", "status"),
    )

    id: str = Field(default_factory=lambda: _id("speech-lineage-node"), primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    kind: str = Field(index=True)
    digest: str = Field(index=True)
    status: str = Field(default="active", index=True)
    consent_id: str | None = Field(default=None, index=True)
    revocation_epoch: int = 0
    created_at_ms: int
    updated_at_ms: int


class SpeechLineageEdgeDB(SQLModel, table=True):
    __tablename__ = "speech_lineage_edges"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "source_id", "target_id", "relation", name="uq_speech_lineage_edge"),
        sa.Index("ix_speech_lineage_edge_forward", "tenant_id", "source_id"),
        sa.Index("ix_speech_lineage_edge_backward", "tenant_id", "target_id"),
    )

    id: str = Field(default_factory=lambda: _id("speech-lineage-edge"), primary_key=True)
    tenant_id: str = Field(index=True)
    source_id: str = Field(index=True)
    target_id: str = Field(index=True)
    relation: str = Field(index=True)
    created_at_ms: int


class SpeechLineageOutboxDB(SQLModel, table=True):
    __tablename__ = "speech_lineage_outbox"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "owner_subject", "event_digest", name="uq_speech_lineage_outbox_event"),
    )

    id: str = Field(default_factory=lambda: _id("speech-lineage-outbox"), primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    event_digest: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    state: str = Field(default="pending", index=True)
    attempt_count: int = 0
    created_at_ms: int
    updated_at_ms: int


class SpeechEvidenceRevocationDB(SQLModel, table=True):
    __tablename__ = "speech_evidence_revocations"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "evidence_digest", name="uq_speech_revocation_evidence"),)

    id: str = Field(default_factory=lambda: _id("speech-revocation"), primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    evidence_digest: str = Field(index=True)
    consent_id: str = Field(index=True)
    revocation_epoch: int
    reason_code: str
    impact_digest: str
    remote_state: str = Field(default="not_requested", index=True)
    remote_request_digest: str | None = None
    remote_ack_digest: str | None = None
    created_at_ms: int
    updated_at_ms: int


class SpeechPrivacyLifecycleDB(SQLModel, table=True):
    """Content-free completion marker for one fully fenced privacy lifecycle.

    The transitive evidence revocation ledger records the domain impact.  This
    separate projection is intentionally written only after phase fencing and
    cryptographic erasure both succeeded, so a restart never mistakes a
    partially completed revocation for a safe terminal state.
    """

    __tablename__ = "speech_privacy_lifecycles"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "evidence_digest",
            name="uq_speech_privacy_lifecycle_evidence",
        ),
    )

    id: str = Field(default_factory=lambda: _id("speech-privacy-lifecycle"), primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    scope_digest: str = Field(index=True)
    evidence_digest: str = Field(index=True)
    phase: str = Field(index=True)
    revocation_epoch: int
    safe_state: str = Field(index=True)
    local_fenced: bool
    key_destroyed: bool
    remote_state: str = Field(index=True)
    remote_request_digest: str | None = Field(default=None, index=True)
    remote_ack_digest: str | None = Field(default=None, index=True)
    created_at_ms: int
    updated_at_ms: int


class SpeechEvidenceCleanupDB(SQLModel, table=True):
    __tablename__ = "speech_evidence_cleanups"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "evidence_id", name="uq_speech_cleanup_evidence"),)

    id: str = Field(default_factory=lambda: _id("speech-cleanup"), primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    evidence_id: str = Field(index=True)
    evidence_digest: str = Field(index=True)
    consent_id: str = Field(index=True)
    revocation_epoch: int
    impact_decision_digest: str
    state: str = Field(default="pending", index=True)
    artifact_cleaned: bool = False
    key_destroyed: bool = False
    ciphertext_deleted: bool = False
    attempt_count: int = 0
    last_reason_code: str | None = None
    created_at_ms: int
    updated_at_ms: int


__all__ = [
    "SpeechCurationTaskDB",
    "SpeechDatasetManifestDB",
    "SpeechEvidenceAdmissionDB",
    "SpeechEvidenceCleanupDB",
    "SpeechEvidenceConsentDB",
    "SpeechEvidenceDB",
    "SpeechEvidenceKeyDB",
    "SpeechEvidenceRevocationDB",
    "SpeechPrivacyLifecycleDB",
    "SpeechPeerCurationArtifactDB",
    "SpeechPeerEvidenceCurationDB",
    "SpeechLineageEdgeDB",
    "SpeechLineageNodeDB",
    "SpeechLineageOutboxDB",
]
