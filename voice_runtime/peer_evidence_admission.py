"""Recipient-owned encrypted quarantine and content-free pre-admission."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, replace
from typing import Mapping, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class PeerEvidenceAdmissionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class RecipientQuarantineKeyPort(Protocol):
    def resolve(self, *, pair_id: str, key_id: str, epoch: int) -> bytes | None: ...

    def destroy(self, *, record_id: str, key_id: str) -> None: ...


@dataclass(frozen=True)
class LocalEvidenceValidation:
    schema_valid: bool
    signature_valid: bool
    consent_valid: bool
    speaker_scope_valid: bool
    resolution_valid: bool
    quality_valid: bool
    source_group_valid: bool
    content_digest: str
    feature_digest: str
    reason_codes: tuple[str, ...]


class LocalEvidenceValidatorPort(Protocol):
    def validate(
        self,
        plaintext: memoryview,
        *,
        pair_id: str,
        group_id: str,
        offer_id: str,
        expected_digest: str,
    ) -> LocalEvidenceValidation: ...


@dataclass(frozen=True)
class QuarantinedEvidenceRecord:
    record_id: str
    pair_id: str
    offer_id: str
    group_id: str
    sender_id: str
    speaker_digest: str
    source_group_digest: str
    consent_digest: str
    resolution_digest: str
    payload_digest: str
    ciphertext_digest: str
    ciphertext_b64: str
    nonce_b64: str
    aad_b64: str
    key_id: str
    epoch: int
    retention_until_ms: int
    received_at_ms: int
    size_bytes: int
    state: str = "quarantined"
    decision_digest: str | None = None
    reason_codes: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "pair_id": self.pair_id,
            "offer_id": self.offer_id,
            "group_id": self.group_id,
            "sender_id_digest": hashlib.sha256(self.sender_id.encode()).hexdigest(),
            "speaker_digest": self.speaker_digest,
            "source_group_digest": self.source_group_digest,
            "consent_digest": self.consent_digest,
            "resolution_digest": self.resolution_digest,
            "payload_digest": self.payload_digest,
            "ciphertext_digest": self.ciphertext_digest,
            "key_id_digest": hashlib.sha256(self.key_id.encode()).hexdigest(),
            "epoch": self.epoch,
            "retention_until_ms": self.retention_until_ms,
            "received_at_ms": self.received_at_ms,
            "size_bytes": self.size_bytes,
            "state": self.state,
            "decision_digest": self.decision_digest,
            "reason_codes": list(self.reason_codes),
        }


class RecipientQuarantineRepositoryPort(Protocol):
    def find_by_digest(self, *, pair_id: str, payload_digest: str) -> QuarantinedEvidenceRecord | None: ...

    def get(self, record_id: str) -> QuarantinedEvidenceRecord | None: ...

    def put_if_absent(self, record: QuarantinedEvidenceRecord) -> QuarantinedEvidenceRecord: ...

    def compare_and_set(
        self,
        record_id: str,
        *,
        expected_state: str,
        replacement: QuarantinedEvidenceRecord | None,
    ) -> QuarantinedEvidenceRecord | None: ...


class InMemoryRecipientQuarantineRepository:
    def __init__(self) -> None:
        self._records: dict[str, QuarantinedEvidenceRecord] = {}
        self._lock = threading.RLock()

    def find_by_digest(self, *, pair_id: str, payload_digest: str) -> QuarantinedEvidenceRecord | None:
        with self._lock:
            return next(
                (
                    record
                    for record in self._records.values()
                    if record.pair_id == pair_id and record.payload_digest == payload_digest
                ),
                None,
            )

    def get(self, record_id: str) -> QuarantinedEvidenceRecord | None:
        with self._lock:
            return self._records.get(record_id)

    def put_if_absent(self, record: QuarantinedEvidenceRecord) -> QuarantinedEvidenceRecord:
        with self._lock:
            return self._records.setdefault(record.record_id, record)

    def compare_and_set(
        self,
        record_id: str,
        *,
        expected_state: str,
        replacement: QuarantinedEvidenceRecord | None,
    ) -> QuarantinedEvidenceRecord | None:
        with self._lock:
            current = self._records.get(record_id)
            if current is None:
                raise PeerEvidenceAdmissionError("speech_evidence_quarantine_not_found")
            if current.state != expected_state:
                if replacement is not None and current == replacement:
                    return current
                raise PeerEvidenceAdmissionError("speech_evidence_quarantine_state_conflict")
            if replacement is None:
                self._records.pop(record_id, None)
                return None
            self._records[record_id] = replacement
            return replacement


class RecipientPeerEvidenceQuarantine:
    def __init__(
        self,
        *,
        keys: RecipientQuarantineKeyPort,
        validator: LocalEvidenceValidatorPort,
        repository: RecipientQuarantineRepositoryPort,
        maximum_records: int = 10_000,
        maximum_ciphertext_bytes: int = 1024 * 1024,
        clock_ms=lambda: time.time_ns() // 1_000_000,
    ) -> None:
        if not 1 <= maximum_records <= 100_000 or not 1 <= maximum_ciphertext_bytes <= 16 * 1024 * 1024:
            raise ValueError("speech_evidence_quarantine_policy_invalid")
        self._keys = keys
        self._validator = validator
        self._repository = repository
        self._maximum_records = maximum_records
        self._maximum_bytes = maximum_ciphertext_bytes
        self._clock_ms = clock_ms
        self._lock = threading.RLock()
        self._count = 0

    def quarantine_encrypted(
        self,
        *,
        pair_id: str,
        offer_id: str,
        group_id: str,
        sender_id: str,
        speaker_digest: str,
        source_group_digest: str,
        consent_digest: str,
        resolution_digest: str,
        payload_digest: str,
        ciphertext: bytes,
        nonce: bytes,
        aad: bytes,
        key_id: str,
        epoch: int,
        retention_until_ms: int,
    ) -> QuarantinedEvidenceRecord:
        now = int(self._clock_ms())
        if not 17 <= len(ciphertext) <= self._maximum_bytes or len(nonce) != 12:
            raise PeerEvidenceAdmissionError("speech_evidence_quarantine_ciphertext_invalid")
        if retention_until_ms <= now or epoch < 1:
            raise PeerEvidenceAdmissionError("speech_evidence_quarantine_scope_stale")
        for digest in (
            speaker_digest,
            source_group_digest,
            consent_digest,
            resolution_digest,
            payload_digest,
        ):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise PeerEvidenceAdmissionError("speech_evidence_quarantine_binding_invalid")
        existing = self._repository.find_by_digest(pair_id=pair_id, payload_digest=payload_digest)
        ciphertext_digest = hashlib.sha256(ciphertext).hexdigest()
        if existing is not None:
            if existing.ciphertext_digest != ciphertext_digest or existing.group_id != group_id:
                raise PeerEvidenceAdmissionError("speech_evidence_quarantine_digest_conflict")
            return existing
        with self._lock:
            if self._count >= self._maximum_records:
                raise PeerEvidenceAdmissionError("speech_evidence_quarantine_quota_exceeded")
            record_id = hashlib.sha256(
                f"{pair_id}\0{offer_id}\0{group_id}\0{payload_digest}".encode()
            ).hexdigest()
            record = QuarantinedEvidenceRecord(
                record_id=record_id,
                pair_id=pair_id,
                offer_id=offer_id,
                group_id=group_id,
                sender_id=sender_id,
                speaker_digest=speaker_digest,
                source_group_digest=source_group_digest,
                consent_digest=consent_digest,
                resolution_digest=resolution_digest,
                payload_digest=payload_digest,
                ciphertext_digest=ciphertext_digest,
                ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
                nonce_b64=base64.b64encode(nonce).decode("ascii"),
                aad_b64=base64.b64encode(aad).decode("ascii"),
                key_id=key_id,
                epoch=epoch,
                retention_until_ms=retention_until_ms,
                received_at_ms=now,
                size_bytes=len(ciphertext),
            )
            stored = self._repository.put_if_absent(record)
            if stored == record:
                self._count += 1
            return stored

    def pre_admit(self, record_id: str) -> tuple[QuarantinedEvidenceRecord, LocalEvidenceValidation]:
        record = self._require(record_id)
        if record.state != "quarantined":
            raise PeerEvidenceAdmissionError("speech_evidence_quarantine_state_conflict")
        if record.retention_until_ms <= int(self._clock_ms()):
            raise PeerEvidenceAdmissionError("speech_evidence_quarantine_expired")
        key = self._keys.resolve(pair_id=record.pair_id, key_id=record.key_id, epoch=record.epoch)
        if key is None or len(key) != 32:
            raise PeerEvidenceAdmissionError("speech_evidence_quarantine_key_unavailable")
        try:
            clear = bytearray(
                AESGCM(key).decrypt(
                    base64.b64decode(record.nonce_b64, validate=True),
                    base64.b64decode(record.ciphertext_b64, validate=True),
                    base64.b64decode(record.aad_b64, validate=True),
                )
            )
        except (InvalidTag, ValueError) as exc:
            raise PeerEvidenceAdmissionError("speech_evidence_quarantine_authentication_failed") from exc
        try:
            if not secrets.compare_digest(hashlib.sha256(clear).hexdigest(), record.payload_digest):
                raise PeerEvidenceAdmissionError("speech_evidence_quarantine_digest_mismatch")
            validation = self._validator.validate(
                memoryview(clear),
                pair_id=record.pair_id,
                group_id=record.group_id,
                offer_id=record.offer_id,
                expected_digest=record.payload_digest,
            )
        finally:
            clear[:] = b"\x00" * len(clear)
        reason_binding = "\0".join(validation.reason_codes)
        decision_digest = hashlib.sha256(
            (
                "ananta.speech-evidence-pre-admission.v1\0"
                f"{record.record_id}\0{validation.content_digest}\0{validation.feature_digest}\0"
                f"{reason_binding}"
            ).encode()
        ).hexdigest()
        updated = replace(record, decision_digest=decision_digest, reason_codes=validation.reason_codes)
        stored = self._repository.compare_and_set(
            record_id,
            expected_state="quarantined",
            replacement=updated,
        )
        assert stored is not None
        return stored, validation

    def transition(
        self,
        record_id: str,
        *,
        target: str,
        expected_state: str = "quarantined",
        decision_digest: str,
        reason_codes: tuple[str, ...],
    ) -> QuarantinedEvidenceRecord:
        if target not in {"accepted", "rejected", "quarantined"}:
            raise PeerEvidenceAdmissionError("speech_evidence_quarantine_transition_invalid")
        current = self._require(record_id)
        if current.state == target and current.decision_digest == decision_digest:
            return current
        replacement = replace(
            current,
            state=target,
            decision_digest=decision_digest,
            reason_codes=tuple(reason_codes),
        )
        stored = self._repository.compare_and_set(
            record_id,
            expected_state=expected_state,
            replacement=replacement,
        )
        assert stored is not None
        return stored

    def delete(self, record_id: str, *, expected_state: str) -> None:
        current = self._require(record_id)
        self._repository.compare_and_set(record_id, expected_state=expected_state, replacement=None)
        self._keys.destroy(record_id=record_id, key_id=current.key_id)
        with self._lock:
            self._count = max(0, self._count - 1)

    def public(self, record_id: str) -> Mapping[str, object]:
        return self._require(record_id).public_dict()

    def _require(self, record_id: str) -> QuarantinedEvidenceRecord:
        record = self._repository.get(record_id)
        if record is None:
            raise PeerEvidenceAdmissionError("speech_evidence_quarantine_not_found")
        return record


__all__ = [
    "InMemoryRecipientQuarantineRepository",
    "LocalEvidenceValidation",
    "LocalEvidenceValidatorPort",
    "PeerEvidenceAdmissionError",
    "QuarantinedEvidenceRecord",
    "RecipientPeerEvidenceQuarantine",
    "RecipientQuarantineKeyPort",
    "RecipientQuarantineRepositoryPort",
]
