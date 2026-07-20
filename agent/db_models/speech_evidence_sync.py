"""Content-free Hub control-plane state for bilateral speech-evidence sync."""

from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class SpeechEvidencePeerKeyDB(SQLModel, table=True):
    """An Ed25519 verification key bound to one current pair/audience epoch."""

    __tablename__ = "speech_evidence_peer_keys"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "pair_id",
            "sender_id",
            "audience_id",
            "epoch",
            "key_id",
            name="uq_speech_evidence_peer_key_scope",
        ),
        sa.Index(
            "ix_speech_evidence_peer_key_discovery",
            "tenant_id",
            "session_id",
            "sender_id",
            "audience_id",
            "epoch",
            "state",
        ),
    )

    id: str = Field(default_factory=lambda: f"speech-evidence-peer-key-{uuid.uuid4()}", primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    pair_id: str = Field(index=True)
    sender_id: str = Field(index=True)
    audience_id: str = Field(index=True)
    epoch: int = Field(index=True)
    key_id: str = Field(index=True)
    public_key_b64: str = Field(repr=False)
    fingerprint: str = Field(index=True, repr=False)
    membership_version: int
    consent_version: int
    state: str = Field(default="active", index=True)
    expires_at_ms: int = Field(index=True)
    version: int = 1
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)
    updated_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


class SpeechEvidenceReplayStateDB(SQLModel, table=True):
    """Restart-stable bounded replay bitmap for one signed traffic context."""

    __tablename__ = "speech_evidence_replay_states"
    __table_args__ = (
        sa.UniqueConstraint(
            "session_id",
            "pair_id",
            "sender_id",
            "epoch",
            "traffic_class",
            name="uq_speech_evidence_replay_context",
        ),
        sa.Index(
            "ix_speech_evidence_replay_expiry",
            "expires_at_ms",
            "updated_at_ms",
        ),
    )

    id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    pair_id: str = Field(index=True)
    sender_id: str = Field(index=True)
    epoch: int = Field(index=True)
    traffic_class: str = Field(index=True)
    highest_sequence: int
    bitmap_hex: str = Field(repr=False)
    width: int
    expires_at_ms: int = Field(index=True)
    version: int = 1
    updated_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


class SpeechEvidenceOfferDB(SQLModel, table=True):
    """Bilateral scope authorization; never stores evidence payload content."""

    __tablename__ = "speech_evidence_offers"
    __table_args__ = (
        sa.Index(
            "ix_speech_evidence_offer_scope",
            "tenant_id",
            "session_id",
            "pair_id",
            "state",
            "expires_at_ms",
        ),
    )

    offer_id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    proposal_verification_digest: str = Field(index=True, repr=False)
    acceptance_verification_digest: str | None = Field(default=None, index=True, repr=False)
    session_id: str = Field(index=True)
    pair_id: str = Field(index=True)
    epoch: int = Field(index=True)
    sender_id: str = Field(index=True)
    recipient_id: str = Field(index=True)
    inventory_root_digest: str = Field(repr=False)
    direction: str
    purpose: str
    data_classes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    fields: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    retention_seconds: int
    trainer_class: str
    group_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON), repr=False)
    group_previews: list[dict[str, object]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        repr=False,
    )
    group_preview_digest: str = Field(default="", index=True, repr=False)
    total_bytes: int
    sender_consent_digest: str = Field(repr=False)
    recipient_consent_digest: str = Field(repr=False)
    scope_digest: str = Field(index=True, repr=False)
    expires_at_ms: int = Field(index=True)
    state: str = Field(default="proposed", index=True)
    transfer_started: bool = False
    invalidation_reason: str | None = None
    protocol_version: str = Field(default="ananta.speech-evidence-sync.v1", index=True)
    version: int = 1
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)
    updated_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


class SpeechEvidenceTransferDB(SQLModel, table=True):
    """Monotone ACK/resume cursor for one offer group."""

    __tablename__ = "speech_evidence_transfers"
    __table_args__ = (
        sa.UniqueConstraint("offer_id", "group_id", name="uq_speech_evidence_transfer_group"),
        sa.Index(
            "ix_speech_evidence_transfer_scope",
            "tenant_id",
            "session_id",
            "sender_id",
            "recipient_id",
            "state",
        ),
    )

    id: str = Field(default_factory=lambda: f"speech-evidence-transfer-{uuid.uuid4()}", primary_key=True)
    tenant_id: str = Field(index=True)
    offer_id: str = Field(foreign_key="speech_evidence_offers.offer_id", index=True)
    group_id: str = Field(index=True)
    session_id: str = Field(index=True)
    pair_id: str = Field(index=True)
    epoch: int = Field(index=True)
    sender_id: str = Field(index=True)
    recipient_id: str = Field(index=True)
    key_id: str = Field(index=True)
    chunk_count: int
    first_missing_index: int = 0
    acknowledged_indices: list[int] = Field(default_factory=list, sa_column=Column(JSON), repr=False)
    received_bytes: int = 0
    in_flight_bytes: int = 0
    state: str = Field(default="active", index=True)
    reason_code: str | None = None
    expires_at_ms: int = Field(index=True)
    version: int = 1
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)
    updated_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


class SpeechEvidenceTransferChunkDB(SQLModel, table=True):
    """Content-free chunk binding; ciphertext lives only in the opaque relay."""

    __tablename__ = "speech_evidence_transfer_chunks"
    __table_args__ = (
        sa.UniqueConstraint("transfer_id", "chunk_index", name="uq_speech_evidence_transfer_chunk_index"),
        sa.UniqueConstraint("transfer_id", "message_id", name="uq_speech_evidence_transfer_chunk_message"),
        sa.UniqueConstraint(
            "nonce_scope_digest",
            name="uq_speech_evidence_transfer_nonce",
        ),
    )

    id: str = Field(default_factory=lambda: f"speech-evidence-chunk-{uuid.uuid4()}", primary_key=True)
    transfer_id: str = Field(foreign_key="speech_evidence_transfers.id", index=True)
    message_id: str = Field(index=True)
    chunk_index: int = Field(index=True)
    plaintext_bytes: int
    plaintext_digest: str = Field(repr=False)
    ciphertext_digest: str = Field(repr=False)
    nonce_digest: str = Field(index=True, repr=False)
    nonce_scope_digest: str = Field(index=True, repr=False)
    key_id: str = Field(index=True)
    epoch: int = Field(index=True)
    direction: str = Field(index=True)
    acknowledged: bool = Field(default=False, index=True)
    created_at_ms: int = Field(default_factory=lambda: time.time_ns() // 1_000_000)


__all__ = [
    "SpeechEvidenceOfferDB",
    "SpeechEvidencePeerKeyDB",
    "SpeechEvidenceReplayStateDB",
    "SpeechEvidenceTransferChunkDB",
    "SpeechEvidenceTransferDB",
]
