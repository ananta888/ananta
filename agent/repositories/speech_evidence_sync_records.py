"""Content-free value objects and the error type of the speech-evidence sync repositories."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from agent.models.speech_evidence_offer import SpeechEvidenceGroupPreview


class SpeechEvidenceSyncRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SpeechEvidencePeerKeyRecord:
    tenant_id: str
    session_id: str
    pair_id: str
    sender_id: str
    audience_id: str
    epoch: int
    key_id: str
    public_key_b64: str
    fingerprint: str
    membership_version: int
    consent_version: int
    expires_at_ms: int
    state: str
    version: int

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SpeechEvidenceTransferChunkBinding:
    chunk_index: int
    plaintext_bytes: int
    plaintext_digest: str


@dataclass(frozen=True, slots=True)
class SpeechEvidenceTransferCurationBinding:
    offer_id: str
    group_id: str
    session_id: str
    pair_id: str
    epoch: int
    sender_id: str
    recipient_id: str
    key_id: str
    received_bytes: int
    expires_at_ms: int
    preview: SpeechEvidenceGroupPreview
    offer_group_preview_digest: str
    chunks: tuple[SpeechEvidenceTransferChunkBinding, ...]


@dataclass(frozen=True, slots=True)
class SpeechEvidenceTransferRecord:
    offer_id: str
    group_id: str
    state: str
    chunk_count: int
    acknowledged_chunks: int
    first_missing_index: int
    received_bytes: int
    in_flight_bytes: int
    expires_at_ms: int
    reason_code: str | None
    version: int

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


__all__ = [
    "SpeechEvidencePeerKeyRecord",
    "SpeechEvidenceSyncRepositoryError",
    "SpeechEvidenceTransferChunkBinding",
    "SpeechEvidenceTransferCurationBinding",
    "SpeechEvidenceTransferRecord",
]
