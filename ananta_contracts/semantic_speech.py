"""Closed contracts for transcript-first semantic speech negotiation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

SEMANTIC_SPEECH_CONTRACT_VERSION = 1
MAX_TRANSCRIPT_CHARS = 16_384
MAX_SEMANTIC_FEATURES = 32
MAX_RESIDUAL_FEATURES = 128
MAX_SEMANTIC_FRAME_BYTES = 16 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")

SpeechMode = Literal[
    "ordinary_audio",
    "transcript_live",
    "semantic_reconstruction",
    "delayed_correction",
    "segment_only",
    "fallback",
]
TranscriptAuthority = Literal["provisional", "final", "corrected", "correction_failed", "missing_source"]
SemanticSpeechPayloadKind = Literal["revoke", "transcript_revision", "semantic_frame", "correction", "source_audio"]


class SemanticSpeechContractError(ValueError):
    def __init__(self, reason_code: str, *, field: str | None = None) -> None:
        self.reason_code = reason_code
        self.field = field
        super().__init__(reason_code if field is None else f"{reason_code}:{field}")


@dataclass(frozen=True, slots=True)
class SpeechContractContext:
    session_id: str
    epoch: int
    turn_id: str
    revision: int
    sender_id: str
    audience_id: str
    consent_version: int
    expires_at_ms: int
    contract_digest: str
    source_digest: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptRevision:
    context: SpeechContractContext
    authority: TranscriptAuthority
    text: str
    final: bool
    supersedes_revision: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SpeechSemanticFrame:
    context: SpeechContractContext
    frame_id: str
    algorithm_version: str
    start_ms: int
    end_ms: int
    confidence: float
    prosody: tuple[float, ...]
    residual: tuple[float, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_context(raw: Mapping[str, Any], *, now_ms: int | None = None) -> SpeechContractContext:
    expected = {
        "session_id",
        "epoch",
        "turn_id",
        "revision",
        "sender_id",
        "audience_id",
        "consent_version",
        "expires_at_ms",
        "contract_digest",
        "source_digest",
    }
    _closed(raw, expected)
    context = SpeechContractContext(
        session_id=_identifier(raw.get("session_id"), "session_id"),
        epoch=_integer(raw.get("epoch"), 1, 2**31 - 1, "epoch"),
        turn_id=_identifier(raw.get("turn_id"), "turn_id"),
        revision=_integer(raw.get("revision"), 1, 2**31 - 1, "revision"),
        sender_id=_identifier(raw.get("sender_id"), "sender_id"),
        audience_id=_identifier(raw.get("audience_id"), "audience_id"),
        consent_version=_integer(raw.get("consent_version"), 1, 2**31 - 1, "consent_version"),
        expires_at_ms=_integer(raw.get("expires_at_ms"), 1, 9_007_199_254_740_991, "expires_at_ms"),
        contract_digest=_digest(raw.get("contract_digest"), "contract_digest"),
        source_digest=_optional_digest(raw.get("source_digest"), "source_digest"),
    )
    if now_ms is not None and context.expires_at_ms <= now_ms:
        raise SemanticSpeechContractError("speech_contract_expired")
    return context


def validate_transcript_revision(raw: Mapping[str, Any], *, now_ms: int | None = None) -> TranscriptRevision:
    expected = {
        "context",
        "authority",
        "text",
        "final",
        "supersedes_revision",
        "start_ms",
        "end_ms",
    }
    _closed(raw, expected)
    context_raw = raw.get("context")
    if not isinstance(context_raw, Mapping):
        raise SemanticSpeechContractError("speech_context_invalid")
    authority = raw.get("authority")
    if authority not in {"provisional", "final", "corrected", "correction_failed", "missing_source"}:
        raise SemanticSpeechContractError("speech_authority_invalid")
    text = raw.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TRANSCRIPT_CHARS:
        raise SemanticSpeechContractError("speech_text_invalid")
    final = raw.get("final")
    if type(final) is not bool:
        raise SemanticSpeechContractError("speech_final_invalid")
    if authority == "provisional" and final:
        raise SemanticSpeechContractError("speech_authority_conflict")
    if authority != "provisional" and not final:
        raise SemanticSpeechContractError("speech_authority_conflict")
    start = _optional_integer(raw.get("start_ms"), 0, 2**31 - 1, "start_ms")
    end = _optional_integer(raw.get("end_ms"), 0, 2**31 - 1, "end_ms")
    if start is not None and end is not None and end < start:
        raise SemanticSpeechContractError("speech_timing_invalid")
    return TranscriptRevision(
        context=validate_context(context_raw, now_ms=now_ms),
        authority=authority,
        text=text,
        final=final,
        supersedes_revision=_optional_integer(raw.get("supersedes_revision"), 1, 2**31 - 1, "supersedes_revision"),
        start_ms=start,
        end_ms=end,
    )


def validate_semantic_frame(raw: Mapping[str, Any], *, now_ms: int | None = None) -> SpeechSemanticFrame:
    expected = {
        "context",
        "frame_id",
        "algorithm_version",
        "start_ms",
        "end_ms",
        "confidence",
        "prosody",
        "residual",
    }
    _closed(raw, expected)
    context_raw = raw.get("context")
    if not isinstance(context_raw, Mapping):
        raise SemanticSpeechContractError("speech_context_invalid")
    prosody = _finite_vector(raw.get("prosody"), MAX_SEMANTIC_FEATURES, "prosody")
    residual = _finite_vector(raw.get("residual"), MAX_RESIDUAL_FEATURES, "residual")
    start = _integer(raw.get("start_ms"), 0, 2**31 - 1, "start_ms")
    end = _integer(raw.get("end_ms"), 0, 2**31 - 1, "end_ms")
    if end < start:
        raise SemanticSpeechContractError("speech_timing_invalid")
    frame = SpeechSemanticFrame(
        context=validate_context(context_raw, now_ms=now_ms),
        frame_id=_identifier(raw.get("frame_id"), "frame_id"),
        algorithm_version=_identifier(raw.get("algorithm_version"), "algorithm_version"),
        start_ms=start,
        end_ms=end,
        confidence=_finite(raw.get("confidence"), 0.0, 1.0, "confidence"),
        prosody=prosody,
        residual=residual,
    )
    encoded = json.dumps(frame.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_SEMANTIC_FRAME_BYTES:
        raise SemanticSpeechContractError("speech_frame_too_large")
    return frame


def speech_contract_digest(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise SemanticSpeechContractError("speech_contract_unserializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def validate_semantic_speech_transport_payload(
    raw: Mapping[str, Any],
    *,
    session_id: str,
    epoch: int,
    local_peer_id: str,
    remote_peer_id: str,
    consent_version: int,
    contract_digest: str,
    now_ms: int,
) -> dict[str, Any]:
    """Validate the flat browser transport payload with cross-runtime parity."""

    allowed = {
        "version",
        "kind",
        "session_id",
        "epoch",
        "turn_id",
        "revision",
        "sender_id",
        "audience_id",
        "consent_version",
        "expires_at_ms",
        "contract_digest",
        "source_digest",
        "authority",
        "text",
        "features",
        "audio_ciphertext",
        "reason_code",
    }
    required = {
        "version",
        "kind",
        "session_id",
        "epoch",
        "turn_id",
        "revision",
        "sender_id",
        "audience_id",
        "consent_version",
        "expires_at_ms",
        "contract_digest",
        "source_digest",
    }
    if set(raw) - allowed:
        raise SemanticSpeechContractError("semantic_speech_unknown_field")
    if required - set(raw):
        raise SemanticSpeechContractError("semantic_speech_required_field_missing")
    kind = raw.get("kind")
    kinds = {"revoke", "transcript_revision", "semantic_frame", "correction", "source_audio"}
    if kind not in kinds:
        raise SemanticSpeechContractError("semantic_speech_kind_invalid")
    if (
        raw.get("version") != "ananta.semantic-speech.v1"
        or raw.get("session_id") != session_id
        or raw.get("epoch") != epoch
        or raw.get("consent_version") != consent_version
        or raw.get("contract_digest") != contract_digest
        or raw.get("sender_id") not in {local_peer_id, remote_peer_id}
        or raw.get("audience_id") not in {local_peer_id, remote_peer_id}
    ):
        raise SemanticSpeechContractError("semantic_speech_context_mismatch")
    _identifier(raw.get("turn_id"), "turn_id")
    _identifier(raw.get("sender_id"), "sender_id")
    _identifier(raw.get("audience_id"), "audience_id")
    revision = raw.get("revision")
    expires = raw.get("expires_at_ms")
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or revision > 2**31 - 1
        or not isinstance(expires, int)
        or isinstance(expires, bool)
        or expires > 9_007_199_254_740_991
        or expires <= now_ms
        or expires > now_ms + 600_000
    ):
        raise SemanticSpeechContractError("semantic_speech_context_mismatch")
    source_digest = raw.get("source_digest")
    if source_digest is not None and (not isinstance(source_digest, str) or not _DIGEST.fullmatch(source_digest)):
        raise SemanticSpeechContractError("semantic_speech_source_digest_invalid")
    authority = raw.get("authority")
    authorities = {"provisional", "final", "corrected", "correction_failed", "missing_source"}
    if authority is not None and authority not in authorities:
        raise SemanticSpeechContractError("semantic_speech_authority_invalid")
    text = raw.get("text")
    if text is not None and (not isinstance(text, str) or len(text.encode()) > MAX_TRANSCRIPT_CHARS):
        raise SemanticSpeechContractError("semantic_speech_text_invalid")
    features = raw.get("features")
    if features is not None:
        _finite_vector(features, MAX_SEMANTIC_FEATURES + MAX_RESIDUAL_FEATURES, "features")
    reason_code = raw.get("reason_code")
    if reason_code is not None:
        _identifier(reason_code, "reason_code")
    if kind == "transcript_revision" and (authority not in {"provisional", "final"} or not isinstance(text, str)):
        raise SemanticSpeechContractError("semantic_speech_transcript_invalid")
    if kind == "correction" and (
        authority not in {"corrected", "correction_failed", "missing_source"} or not isinstance(text, str)
    ):
        raise SemanticSpeechContractError("semantic_speech_correction_invalid")
    if kind == "semantic_frame" and not isinstance(features, (list, tuple)):
        raise SemanticSpeechContractError("semantic_speech_features_invalid")
    if kind == "source_audio":
        ciphertext = raw.get("audio_ciphertext")
        if (
            source_digest is None
            or not isinstance(ciphertext, str)
            or not ciphertext
            or len(ciphertext) > 350_000
            or re.fullmatch(r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?", ciphertext) is None
        ):
            raise SemanticSpeechContractError("semantic_speech_source_audio_invalid")
    if kind == "revoke" and not isinstance(reason_code, str):
        raise SemanticSpeechContractError("semantic_speech_revoke_invalid")
    return dict(raw)


def _closed(raw: Mapping[str, Any], expected: set[str]) -> None:
    if set(raw) - expected:
        raise SemanticSpeechContractError("speech_unknown_field")
    if expected - set(raw):
        raise SemanticSpeechContractError("speech_required_field_missing")


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise SemanticSpeechContractError("speech_identifier_invalid", field=field)
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise SemanticSpeechContractError("speech_digest_invalid", field=field)
    return value


def _optional_digest(value: object, field: str) -> str | None:
    return None if value is None else _digest(value, field)


def _integer(value: object, low: int, high: int, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise SemanticSpeechContractError("speech_integer_invalid", field=field)
    return value


def _optional_integer(value: object, low: int, high: int, field: str) -> int | None:
    return None if value is None else _integer(value, low, high, field)


def _finite(value: object, low: float, high: float, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SemanticSpeechContractError("speech_number_invalid", field=field)
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise SemanticSpeechContractError("speech_number_invalid", field=field)
    return number


def _finite_vector(value: object, maximum: int, field: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise SemanticSpeechContractError("speech_vector_invalid", field=field)
    return tuple(_finite(item, -1.0, 1.0, field) for item in value)


__all__ = [
    "MAX_RESIDUAL_FEATURES",
    "MAX_SEMANTIC_FEATURES",
    "MAX_SEMANTIC_FRAME_BYTES",
    "MAX_TRANSCRIPT_CHARS",
    "SEMANTIC_SPEECH_CONTRACT_VERSION",
    "SemanticSpeechContractError",
    "SpeechContractContext",
    "SpeechMode",
    "SpeechSemanticFrame",
    "TranscriptAuthority",
    "TranscriptRevision",
    "speech_contract_digest",
    "validate_context",
    "validate_semantic_frame",
    "validate_semantic_speech_transport_payload",
    "validate_transcript_revision",
]
