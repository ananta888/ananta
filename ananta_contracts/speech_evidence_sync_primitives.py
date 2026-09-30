"""Protocol limits, canonical JSON digests and closed-field validators of speech-evidence sync."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from typing import Any, Mapping


PROTOCOL_VERSION = "ananta.speech-evidence-sync.v1"
OFFER_PROTOCOL_VERSION = "ananta.speech-evidence-sync.v2"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({PROTOCOL_VERSION, OFFER_PROTOCOL_VERSION})
GROUP_PREVIEW_VERSION = "ananta.speech-evidence-group-preview.v1"
SIGNATURE_DOMAIN = "ananta.speech-evidence-signature.v1"
SIGNATURE_ALGORITHM = "Ed25519"
MAX_CHUNK_PLAINTEXT_BYTES = 64 * 1024
MAX_CHUNK_CIPHERTEXT_BYTES = MAX_CHUNK_PLAINTEXT_BYTES + 16
MAX_MESSAGE_BYTES = 192 * 1024
MAX_GROUPS = 4096
MAX_CANDIDATES = 32
MAX_TEXT_CHARS = 32_768
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_RETENTION_SECONDS = 365 * 24 * 60 * 60
MAX_SEQUENCE = 9_007_199_254_740_991
MAX_CLOCK_SKEW_MS = 30_000
MAX_MESSAGE_TTL_MS = 10 * 60 * 1000


MESSAGE_TYPES = frozenset(
    {
        "inventory",
        "diff",
        "offer",
        "chunk",
        "chunk_ack",
        "resolution",
        "receipt",
        "revocation",
        "revocation_ack",
    }
)
CONTROL_TYPES = frozenset(MESSAGE_TYPES - {"chunk"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "protocol_version",
        "message_type",
        "message_id",
        "session_id",
        "pair_id",
        "sender_id",
        "audience_id",
        "epoch",
        "sequence",
        "consent_version",
        "key_id",
        "issued_at_ms",
        "expires_at_ms",
        "payload_digest",
        "payload",
        "signature_algorithm",
        "signature_b64",
    }
)


class SpeechEvidenceProtocolError(ValueError):
    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        # UTF-8 canonical text is shared with the browser implementation. Escaping
        # non-ASCII here would produce different signed bytes in Python and JS.
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _control(payload: Mapping[str, Any]) -> None:
    if payload.get("traffic_class") != "control":
        raise SpeechEvidenceProtocolError("speech_evidence_traffic_class_invalid")


def _closed(value: Mapping[str, Any], expected: frozenset[str]) -> None:
    actual = set(value)
    if actual - expected:
        raise SpeechEvidenceProtocolError("speech_evidence_unknown_field")
    if expected - actual:
        raise SpeechEvidenceProtocolError("speech_evidence_required_field_missing")


def _mapping(value: Any, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SpeechEvidenceProtocolError(reason)
    return value


def _identifier(value: Any, reason: str) -> str:
    if isinstance(value, str) and (
        value.startswith(("/", "file:", "data:", "~")) or ".." in value or "\\" in value
    ):
        raise SpeechEvidenceProtocolError("speech_evidence_private_path_forbidden")
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise SpeechEvidenceProtocolError(reason)
    return value


def _digest(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise SpeechEvidenceProtocolError(reason)
    return value


def _integer(value: Any, low: int, high: int, reason: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise SpeechEvidenceProtocolError(reason)
    return value


def _finite(value: Any, low: float, high: float, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeechEvidenceProtocolError(reason)
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise SpeechEvidenceProtocolError(reason)
    return number


def _identifiers(value: Any, maximum: int, reason: str) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise SpeechEvidenceProtocolError(reason)
    rows = [_identifier(item, reason) for item in value]
    if len(rows) != len(set(rows)):
        raise SpeechEvidenceProtocolError(reason)
    return rows


def _integers(value: Any, maximum: int, low: int, high: int) -> list[int]:
    if not isinstance(value, list) or len(value) > maximum:
        raise SpeechEvidenceProtocolError("speech_evidence_integer_array_invalid")
    return [_integer(item, low, high, "speech_evidence_integer_array_invalid") for item in value]


def _b64(value: Any, reason: str, *, exact_bytes: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 100_000:
        raise SpeechEvidenceProtocolError(reason)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SpeechEvidenceProtocolError(reason) from exc
    if exact_bytes is not None and len(decoded) != exact_bytes:
        raise SpeechEvidenceProtocolError(reason)
    return decoded


def _signature_bytes(value: str) -> bytes:
    decoded = _b64(value, "speech_evidence_signature_invalid", exact_bytes=64)
    return decoded


def _replay_key(value: tuple[str, str, str, int, str]) -> str:
    return "\0".join((value[0], value[1], value[2], str(value[3]), value[4]))


__all__ = [
    "CONTROL_TYPES",
    "GROUP_PREVIEW_VERSION",
    "MAX_CANDIDATES",
    "MAX_CHUNK_CIPHERTEXT_BYTES",
    "MAX_CHUNK_PLAINTEXT_BYTES",
    "MAX_CLOCK_SKEW_MS",
    "MAX_GROUPS",
    "MAX_MESSAGE_BYTES",
    "MAX_MESSAGE_TTL_MS",
    "MAX_RETENTION_SECONDS",
    "MAX_SEQUENCE",
    "MAX_TEXT_CHARS",
    "MAX_TOTAL_BYTES",
    "MESSAGE_TYPES",
    "OFFER_PROTOCOL_VERSION",
    "PROTOCOL_VERSION",
    "SIGNATURE_ALGORITHM",
    "SIGNATURE_DOMAIN",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "SpeechEvidenceProtocolError",
    "canonical_json",
    "canonical_sha256",
]
