"""Hub composition for one bounded semantic-speech source correction.

The Hub validates the authenticated segment context and delegates source ASR
through the existing voice task path.  This service then calls the one
``voice_runtime.source_correction`` port, which in turn uses the canonical
``voice_runtime.fusion.alignment`` implementation.  It owns no queue, retry
loop, audio store, or alternative alignment algorithm.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from voice_runtime.backends.base import TranscriptionCandidate
from voice_runtime.schemas import transcription_result_from_dict
from voice_runtime.source_correction import (
    SourceCorrectionPort,
    SourceCorrectionRequest,
    SourceCorrectionService,
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_MAX_FINAL_TEXT_BYTES = 65_536
_MAX_DEADLINE_MS = 30_000


def semantic_speech_security_contract_digest(session_id: str, epoch: int) -> str:
    contract = {
        "algorithms": ["AES-256-GCM", "ECDH-P256-HKDF-SHA256"],
        "domain": "ananta.webrtc.security-contract.v1",
        "epoch": epoch,
        "minimum_mode": "strict_e2ee",
        "payload_classes": ["bulk", "control", "media", "semantic"],
        "scope_id": session_id,
        "version": 1,
    }
    return hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class SemanticSpeechSourceCorrectionError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SemanticSpeechSourceCorrectionCommand:
    session_id: str
    epoch: int
    turn_id: str
    final_revision: int
    consent_id: str
    consent_version: int
    consent_digest: str
    consent_revocation_epoch: int
    contract_digest: str
    source_digest: str
    source_expires_at_ms: int
    deadline_at_ms: int
    requested_at_ms: int
    final_text: str
    consent_granted: bool

    @property
    def idempotency_payload(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "epoch": self.epoch,
            "turn_id": self.turn_id,
            "final_revision": self.final_revision,
            "consent_id": self.consent_id,
            "consent_version": self.consent_version,
            "consent_digest": self.consent_digest,
            "consent_revocation_epoch": self.consent_revocation_epoch,
            "contract_digest": self.contract_digest,
            "source_digest": self.source_digest,
            "source_expires_at_ms": self.source_expires_at_ms,
            "deadline_at_ms": self.deadline_at_ms,
            "final_text_digest": hashlib.sha256(self.final_text.encode("utf-8")).hexdigest(),
        }


class SemanticSpeechSourceCorrectionService:
    """Validate and execute one canonical alignment after delegated source ASR."""

    def __init__(
        self,
        correction: SourceCorrectionPort | None = None,
    ) -> None:
        self._correction = correction or SourceCorrectionService()

    def command(
        self,
        raw: Mapping[str, Any],
        *,
        source_audio: bytes,
        requested_at_ms: int,
        consent_granted: bool,
    ) -> SemanticSpeechSourceCorrectionCommand:
        try:
            command = SemanticSpeechSourceCorrectionCommand(
                session_id=str(raw.get("session_id") or ""),
                epoch=self._integer(raw.get("epoch")),
                turn_id=str(raw.get("turn_id") or ""),
                final_revision=self._integer(raw.get("final_revision")),
                consent_id=str(raw.get("consent_id") or ""),
                consent_version=self._integer(raw.get("consent_version")),
                consent_digest=str(raw.get("consent_digest") or ""),
                consent_revocation_epoch=self._non_negative_integer(raw.get("consent_revocation_epoch")),
                contract_digest=str(raw.get("contract_digest") or ""),
                source_digest=str(raw.get("source_digest") or ""),
                source_expires_at_ms=self._integer(raw.get("source_expires_at_ms")),
                deadline_at_ms=self._integer(raw.get("deadline_at_ms")),
                requested_at_ms=requested_at_ms,
                final_text=str(raw.get("final_text") or ""),
                consent_granted=consent_granted,
            )
        except (TypeError, ValueError) as exc:
            raise SemanticSpeechSourceCorrectionError("source_correction_context_invalid") from exc
        self._validate(command, source_audio)
        return command

    def correct(
        self,
        command: SemanticSpeechSourceCorrectionCommand,
        source_result: Mapping[str, Any],
    ) -> dict[str, object]:
        try:
            parsed = transcription_result_from_dict(source_result)
        except (TypeError, ValueError) as exc:
            raise SemanticSpeechSourceCorrectionError("source_transcription_result_invalid", status_code=502) from exc
        stable = hashlib.sha256(
            f"{command.session_id}\0{command.epoch}\0{command.turn_id}\0{command.final_revision}".encode()
        ).hexdigest()[:24]
        provisional = TranscriptionCandidate(
            candidate_id=f"semantic-final-{stable}",
            backend="semantic-live",
            text=command.final_text,
            source_audio_digest=command.source_digest,
        )
        source = TranscriptionCandidate.from_result(
            candidate_id=f"source-asr-{stable}",
            backend=parsed.raw_backend or "source-asr",
            result=parsed,
            source_audio_digest=command.source_digest,
        )
        result = self._correction.correct(
            request=SourceCorrectionRequest(
                session_id=command.session_id,
                epoch=command.epoch,
                turn_id=command.turn_id,
                provisional_revision=command.final_revision,
                consent_version=command.consent_version,
                source_digest=command.source_digest,
                source_expires_at_ms=command.source_expires_at_ms,
                deadline_at_ms=command.deadline_at_ms,
                requested_at_ms=command.requested_at_ms,
                consent_granted=command.consent_granted,
            ),
            provisional=provisional,
            source=source,
        )
        return {
            "schema_version": "ananta.semantic-source-correction.v1",
            "session_id": result.session_id,
            "epoch": result.epoch,
            "turn_id": result.turn_id,
            "revision": result.revision,
            "supersedes_revision": result.supersedes_revision,
            "text": result.text,
            "authority": result.authority,
            "reason_code": result.reason_code,
            "source_digest": result.source_digest,
            "correction_attempted": result.correction_attempted,
            "operations": [
                {
                    "kind": item.kind,
                    "reference_text": item.reference_text,
                    "candidate_text": item.candidate_text,
                    "candidate_id": item.candidate_id,
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "confidence": item.confidence,
                    "alignment_method": item.alignment_method,
                }
                for item in result.operations
            ],
        }

    def _validate(self, command: SemanticSpeechSourceCorrectionCommand, source_audio: bytes) -> None:
        if (
            not _ID.fullmatch(command.session_id)
            or not _ID.fullmatch(command.turn_id)
            or not _ID.fullmatch(command.consent_id)
        ):
            raise SemanticSpeechSourceCorrectionError("source_correction_identity_invalid")
        if (
            not _DIGEST.fullmatch(command.contract_digest)
            or not _DIGEST.fullmatch(command.source_digest)
            or not _DIGEST.fullmatch(command.consent_digest)
        ):
            raise SemanticSpeechSourceCorrectionError("source_correction_digest_invalid")
        if command.contract_digest != semantic_speech_security_contract_digest(command.session_id, command.epoch):
            raise SemanticSpeechSourceCorrectionError("source_correction_contract_mismatch", status_code=403)
        if not source_audio or hashlib.sha256(source_audio).hexdigest() != command.source_digest:
            raise SemanticSpeechSourceCorrectionError("source_digest_mismatch")
        if not command.final_text or len(command.final_text.encode("utf-8")) > _MAX_FINAL_TEXT_BYTES:
            raise SemanticSpeechSourceCorrectionError("source_correction_final_text_invalid")
        if not command.consent_granted:
            raise SemanticSpeechSourceCorrectionError("source_correction_consent_required", status_code=403)
        if command.source_expires_at_ms <= command.requested_at_ms:
            raise SemanticSpeechSourceCorrectionError("source_missing_or_expired", status_code=410)
        if command.deadline_at_ms <= command.requested_at_ms:
            raise SemanticSpeechSourceCorrectionError("correction_deadline_elapsed", status_code=408)
        if command.deadline_at_ms > command.requested_at_ms + _MAX_DEADLINE_MS:
            raise SemanticSpeechSourceCorrectionError("source_correction_deadline_invalid")
        if command.deadline_at_ms > command.source_expires_at_ms:
            raise SemanticSpeechSourceCorrectionError("source_correction_deadline_invalid")

    @staticmethod
    def _integer(value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("boolean is not an integer")
        parsed = int(value)  # type: ignore[arg-type]
        if parsed < 1:
            raise ValueError("integer must be positive")
        return parsed

    @staticmethod
    def _non_negative_integer(value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("boolean is not an integer")
        parsed = int(value)  # type: ignore[arg-type]
        if parsed < 0:
            raise ValueError("integer must be non-negative")
        return parsed


_SERVICE = SemanticSpeechSourceCorrectionService()


def get_semantic_speech_source_correction_service() -> SemanticSpeechSourceCorrectionService:
    return _SERVICE


__all__ = [
    "SemanticSpeechSourceCorrectionCommand",
    "SemanticSpeechSourceCorrectionError",
    "SemanticSpeechSourceCorrectionService",
    "get_semantic_speech_source_correction_service",
    "semantic_speech_security_contract_digest",
]
