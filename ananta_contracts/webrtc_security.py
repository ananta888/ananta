"""Closed secure-envelope contract shared by Hub-side consumers.

Validation intentionally does not depend on application payload schemas.  The
envelope authenticates routing/security metadata while each payload type keeps
its own schema and size budget.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SECURE_ENVELOPE_VERSION = 1
MAX_CIPHERTEXT_BYTES = 256 * 1024 + 16
MAX_FUTURE_WINDOW_MS = 10 * 60 * 1000
MAX_CLOCK_SKEW_MS = 30_000
MAX_SEQUENCE = 9_007_199_254_740_991
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PAYLOAD_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")


class SecureEnvelopeError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class EnvelopeScope:
    kind: str
    id: str


@dataclass(frozen=True)
class EnvelopeRecipient:
    kind: str
    id: str


@dataclass(frozen=True)
class AuthenticatedMetadata:
    traffic_class: str
    content_encoding: str
    contract_digest: str


@dataclass(frozen=True)
class SecureEnvelopeV1:
    version: int
    scope: EnvelopeScope
    sender_id: str
    recipient: EnvelopeRecipient
    epoch: int
    sequence: int
    key_id: str
    payload_type: str
    expires_at_ms: int
    nonce_b64: str
    aad: AuthenticatedMetadata
    ciphertext_b64: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def aad_bytes(self) -> bytes:
        payload = self.to_dict()
        payload.pop("ciphertext_b64")
        return canonical_security_json({"domain": "ananta.webrtc.secure-envelope.v1", "envelope": payload})


def canonical_security_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SecureEnvelopeError("non_finite_or_unserializable") from exc


def validate_secure_envelope(
    raw: Mapping[str, Any], *, now_ms: int | None = None, check_time: bool = True
) -> SecureEnvelopeV1:
    if not isinstance(raw, Mapping):
        raise SecureEnvelopeError("envelope_invalid_type")
    expected = {
        "version",
        "scope",
        "sender_id",
        "recipient",
        "epoch",
        "sequence",
        "key_id",
        "payload_type",
        "expires_at_ms",
        "nonce_b64",
        "aad",
        "ciphertext_b64",
    }
    _closed(raw, expected)
    if _exact_int(raw.get("version")) != SECURE_ENVELOPE_VERSION:
        raise SecureEnvelopeError("version_unsupported")

    scope_raw = _mapping(raw.get("scope"), "scope_invalid")
    _closed(scope_raw, {"kind", "id"})
    if scope_raw.get("kind") not in {"session", "room"}:
        raise SecureEnvelopeError("scope_invalid")
    scope = EnvelopeScope(str(scope_raw["kind"]), _identifier(scope_raw.get("id"), "scope_invalid"))

    recipient_raw = _mapping(raw.get("recipient"), "recipient_invalid")
    _closed(recipient_raw, {"kind", "id"})
    if recipient_raw.get("kind") not in {"peer", "group"}:
        raise SecureEnvelopeError("recipient_invalid")
    recipient = EnvelopeRecipient(str(recipient_raw["kind"]), _identifier(recipient_raw.get("id"), "recipient_invalid"))

    aad_raw = _mapping(raw.get("aad"), "aad_invalid")
    _closed(aad_raw, {"traffic_class", "content_encoding", "contract_digest"})
    if aad_raw.get("traffic_class") not in {"control", "media", "semantic", "bulk"}:
        raise SecureEnvelopeError("aad_invalid")
    if aad_raw.get("content_encoding") not in {"json", "binary"}:
        raise SecureEnvelopeError("aad_invalid")
    if not isinstance(aad_raw.get("contract_digest"), str) or not _DIGEST_RE.fullmatch(aad_raw["contract_digest"]):
        raise SecureEnvelopeError("aad_invalid")
    aad = AuthenticatedMetadata(
        str(aad_raw["traffic_class"]),
        str(aad_raw["content_encoding"]),
        str(aad_raw["contract_digest"]),
    )

    epoch = _bounded_int(raw.get("epoch"), 1, 2**31 - 1, "epoch_invalid")
    sequence = _bounded_int(raw.get("sequence"), 1, MAX_SEQUENCE, "sequence_invalid")
    expires_at_ms = _bounded_int(raw.get("expires_at_ms"), 1, MAX_SEQUENCE, "expiry_invalid")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if check_time and expires_at_ms < now - MAX_CLOCK_SKEW_MS:
        raise SecureEnvelopeError("expired")
    if check_time and expires_at_ms > now + MAX_FUTURE_WINDOW_MS:
        raise SecureEnvelopeError("expiry_too_far")

    payload_type = raw.get("payload_type")
    if not isinstance(payload_type, str) or not _PAYLOAD_RE.fullmatch(payload_type):
        raise SecureEnvelopeError("payload_type_invalid")
    nonce = _decode_b64(raw.get("nonce_b64"), "nonce_invalid", exact_bytes=12)
    ciphertext = _decode_b64(raw.get("ciphertext_b64"), "ciphertext_invalid")
    if not 16 <= len(ciphertext) <= MAX_CIPHERTEXT_BYTES:
        raise SecureEnvelopeError(
            "ciphertext_oversize" if len(ciphertext) > MAX_CIPHERTEXT_BYTES else "ciphertext_invalid"
        )

    return SecureEnvelopeV1(
        version=SECURE_ENVELOPE_VERSION,
        scope=scope,
        sender_id=_identifier(raw.get("sender_id"), "sender_invalid"),
        recipient=recipient,
        epoch=epoch,
        sequence=sequence,
        key_id=_identifier(raw.get("key_id"), "key_id_invalid"),
        payload_type=payload_type,
        expires_at_ms=expires_at_ms,
        nonce_b64=base64.b64encode(nonce).decode("ascii"),
        aad=aad,
        ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
    )


def seal_secure_envelope(*, key: bytes, plaintext: bytes, envelope: SecureEnvelopeV1) -> SecureEnvelopeV1:
    if len(key) != 32:
        raise SecureEnvelopeError("key_invalid")
    if envelope.ciphertext_b64:
        raise SecureEnvelopeError("ciphertext_must_be_empty_before_seal")
    nonce = _decode_b64(envelope.nonce_b64, "nonce_invalid", exact_bytes=12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, envelope.aad_bytes())
    if len(ciphertext) > MAX_CIPHERTEXT_BYTES:
        raise SecureEnvelopeError("ciphertext_oversize")
    payload = envelope.to_dict()
    payload["ciphertext_b64"] = base64.b64encode(ciphertext).decode("ascii")
    return validate_secure_envelope(payload, check_time=False)


def open_secure_envelope(*, key: bytes, envelope: SecureEnvelopeV1) -> bytes:
    if len(key) != 32:
        raise SecureEnvelopeError("key_invalid")
    try:
        return AESGCM(key).decrypt(
            base64.b64decode(envelope.nonce_b64, validate=True),
            base64.b64decode(envelope.ciphertext_b64, validate=True),
            envelope.aad_bytes(),
        )
    except (InvalidTag, ValueError, binascii.Error) as exc:
        raise SecureEnvelopeError("authentication_failed") from exc


def _mapping(value: Any, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SecureEnvelopeError(reason)
    return value


def _closed(value: Mapping[str, Any], expected: set[str]) -> None:
    keys = set(value)
    if keys - expected:
        raise SecureEnvelopeError("unknown_field")
    if expected - keys:
        raise SecureEnvelopeError("required_field_missing")


def _identifier(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise SecureEnvelopeError(reason)
    return value


def _exact_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bounded_int(value: Any, low: int, high: int, reason: str) -> int:
    number = _exact_int(value)
    if number is None or not low <= number <= high or not math.isfinite(float(number)):
        raise SecureEnvelopeError(reason)
    return number


def _decode_b64(value: Any, reason: str, *, exact_bytes: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 400_000:
        raise SecureEnvelopeError(reason)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SecureEnvelopeError(reason) from exc
    if exact_bytes is not None and len(decoded) != exact_bytes:
        raise SecureEnvelopeError(reason)
    return decoded


__all__ = [
    "AuthenticatedMetadata",
    "EnvelopeRecipient",
    "EnvelopeScope",
    "SecureEnvelopeError",
    "SecureEnvelopeV1",
    "canonical_security_json",
    "open_secure_envelope",
    "seal_secure_envelope",
    "validate_secure_envelope",
]
