"""Closed, dependency-light contracts for bounded WebRTC DataChannel traffic."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

CONTRACT_VERSION = "ananta.webrtc-datachannel.v1"
CHUNK_VERSION = "ananta.webrtc-bounded-chunk.v1"
MAX_WIRE_BYTES = 1_500_000
WIRE_MAGIC = b"ANANTA-DC1"
MAX_WIRE_HEADER_BYTES = 96
TRAFFIC_CLASS_LIMITS: dict[str, int] = {
    "control": 16_384,
    "transcript": 65_536,
    "audio_recovery": 262_144,
    "visual_semantic": 524_288,
    "evidence_bulk": 1_048_576,
    "diagnostic": 8_192,
}
TRAFFIC_CLASS_WIRE_LIMITS: dict[str, int] = {
    traffic_class: min(MAX_WIRE_BYTES, ((payload_limit + 2) // 3) * 4 + 4_096)
    for traffic_class, payload_limit in TRAFFIC_CLASS_LIMITS.items()
}
ALLOWED_COMPRESSIONS = frozenset({"none"})
MAX_CHUNKS = 256
MAX_CHUNK_BYTES = 262_144
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class DataChannelContractError(ValueError):
    def __init__(self, reason_code: str, field: str = "", *, status_code: int = 422) -> None:
        super().__init__(f"{reason_code}:{field}" if field else reason_code)
        self.reason_code = reason_code
        self.field = field
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ValidatedDataChannelMessage:
    version: str
    traffic_class: str
    message_id: str
    session_id: str
    epoch: int
    sender_id: str
    audience_id: str
    sequence: int
    expires_at_ms: int
    compression: str
    security: Mapping[str, str]
    ciphertext: bytes
    payload_digest: str


@dataclass(frozen=True, slots=True)
class ValidatedChunk:
    version: str
    chunk_id: str
    message_id: str
    session_id: str
    epoch: int
    sender_id: str
    traffic_class: str
    index: int
    total: int
    chunk_bytes: int
    total_bytes: int
    expires_at_ms: int
    payload_digest: str
    data: bytes


def _closed(value: Any, allowed: frozenset[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise DataChannelContractError("invalid_object", field)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DataChannelContractError("unknown_field", f"{field}.{unknown[0]}")
    return value


def _identifier(value: Any, field: str) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not _ID.fullmatch(result):
        raise DataChannelContractError("invalid_identifier", field)
    return result


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise DataChannelContractError("invalid_integer", field)
    return value


def _digest(value: Any, field: str) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not _DIGEST.fullmatch(result):
        raise DataChannelContractError("invalid_digest", field)
    return result


def _decode_base64(value: Any, field: str, *, maximum: int) -> bytes:
    if not isinstance(value, str) or len(value) > ((maximum + 2) // 3) * 4 + 4:
        raise DataChannelContractError("payload_too_large", field, status_code=413)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise DataChannelContractError("invalid_base64", field) from exc
    if len(decoded) > maximum:
        raise DataChannelContractError("payload_too_large", field, status_code=413)
    return decoded


def _load_raw(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > MAX_WIRE_BYTES:
        raise DataChannelContractError("wire_message_too_large", status_code=413)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataChannelContractError("invalid_json") from exc
    if not isinstance(value, Mapping):
        raise DataChannelContractError("invalid_object", "message")
    return value


def validate_message(value: Mapping[str, Any]) -> ValidatedDataChannelMessage:
    data = _closed(
        value,
        frozenset(
            {
                "version",
                "traffic_class",
                "message_id",
                "session_id",
                "epoch",
                "sender_id",
                "audience_id",
                "sequence",
                "expires_at_ms",
                "compression",
                "security",
                "payload_bytes",
                "payload_digest",
                "ciphertext",
            }
        ),
        "message",
    )
    if data.get("version") != CONTRACT_VERSION:
        raise DataChannelContractError("unsupported_version", "version")
    traffic_class = str(data.get("traffic_class") or "")
    maximum = TRAFFIC_CLASS_LIMITS.get(traffic_class)
    if maximum is None:
        raise DataChannelContractError("unknown_traffic_class", "traffic_class")
    if data.get("compression") not in ALLOWED_COMPRESSIONS:
        raise DataChannelContractError("unsupported_compression", "compression")
    security = _closed(data.get("security"), frozenset({"algorithm", "key_id"}), "security")
    if security.get("algorithm") != "AES-GCM-256":
        raise DataChannelContractError("unsupported_security_algorithm", "security.algorithm")
    _identifier(security.get("key_id"), "security.key_id")
    raw_declared_bytes = data.get("payload_bytes")
    if (
        isinstance(raw_declared_bytes, int)
        and not isinstance(raw_declared_bytes, bool)
        and raw_declared_bytes > maximum
    ):
        raise DataChannelContractError("payload_too_large", "payload_bytes", status_code=413)
    declared_bytes = _integer(raw_declared_bytes, "payload_bytes", 0, maximum)
    ciphertext = _decode_base64(data.get("ciphertext"), "ciphertext", maximum=maximum)
    if len(ciphertext) != declared_bytes:
        raise DataChannelContractError("payload_size_mismatch", "payload_bytes")
    digest = _digest(data.get("payload_digest"), "payload_digest")
    if hashlib.sha256(ciphertext).hexdigest() != digest:
        raise DataChannelContractError("payload_digest_mismatch", "payload_digest")
    return ValidatedDataChannelMessage(
        version=CONTRACT_VERSION,
        traffic_class=traffic_class,
        message_id=_identifier(data.get("message_id"), "message_id"),
        session_id=_identifier(data.get("session_id"), "session_id"),
        epoch=_integer(data.get("epoch"), "epoch", 1, 2**53 - 1),
        sender_id=_identifier(data.get("sender_id"), "sender_id"),
        audience_id=_identifier(data.get("audience_id"), "audience_id"),
        sequence=_integer(data.get("sequence"), "sequence", 1, 2**53 - 1),
        expires_at_ms=_integer(data.get("expires_at_ms"), "expires_at_ms", 1, 2**53 - 1),
        compression="none",
        security={"algorithm": "AES-GCM-256", "key_id": str(security["key_id"])},
        ciphertext=ciphertext,
        payload_digest=digest,
    )


def parse_message(raw: bytes) -> ValidatedDataChannelMessage:
    return validate_message(_load_raw(raw))


def encode_wire_message(value: Mapping[str, Any]) -> bytes:
    """Encode a message with a bounded header validated before JSON parsing.

    The header duplicates only the dispatch class and declared ciphertext byte
    count.  Receivers reject impossible allocations before parsing the body and
    then require both values to match the closed JSON contract.
    """

    message = validate_message(value)
    body = json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    header = b" ".join(
        (
            WIRE_MAGIC,
            message.traffic_class.encode("ascii"),
            str(len(message.ciphertext)).encode("ascii"),
            str(len(body)).encode("ascii"),
        )
    )
    framed = header + b"\n" + body
    if len(framed) > MAX_WIRE_BYTES:
        raise DataChannelContractError("wire_message_too_large", status_code=413)
    return framed


def parse_wire_message(raw: bytes) -> ValidatedDataChannelMessage:
    if not isinstance(raw, bytes) or len(raw) > MAX_WIRE_BYTES:
        raise DataChannelContractError("wire_message_too_large", status_code=413)
    separator = raw.find(b"\n", 0, MAX_WIRE_HEADER_BYTES + 1)
    if separator < 1 or separator > MAX_WIRE_HEADER_BYTES:
        raise DataChannelContractError("wire_header_invalid")
    try:
        magic, traffic_raw, payload_raw, body_raw = raw[:separator].split(b" ")
        traffic_class = traffic_raw.decode("ascii")
        payload_bytes = _wire_integer(payload_raw, "wire_payload_bytes_invalid")
        body_bytes = _wire_integer(body_raw, "wire_body_bytes_invalid")
    except (ValueError, UnicodeDecodeError) as exc:
        raise DataChannelContractError("wire_header_invalid") from exc
    if magic != WIRE_MAGIC:
        raise DataChannelContractError("wire_header_invalid")
    class_limit = TRAFFIC_CLASS_LIMITS.get(traffic_class)
    if class_limit is None:
        raise DataChannelContractError("unknown_traffic_class", "wire.traffic_class")
    if payload_bytes > class_limit:
        raise DataChannelContractError("payload_too_large", "wire.payload_bytes", status_code=413)
    if body_bytes > MAX_WIRE_BYTES:
        raise DataChannelContractError("wire_message_too_large", status_code=413)
    body = raw[separator + 1 :]
    if len(body) != body_bytes:
        raise DataChannelContractError("wire_body_size_mismatch")
    message = parse_message(body)
    if message.traffic_class != traffic_class or len(message.ciphertext) != payload_bytes:
        raise DataChannelContractError("wire_header_mismatch")
    return message


def _wire_integer(raw: bytes, reason_code: str) -> int:
    if not re.fullmatch(rb"(?:0|[1-9][0-9]{0,15})", raw):
        raise DataChannelContractError(reason_code)
    value = int(raw)
    if value > 2**53 - 1:
        raise DataChannelContractError(reason_code)
    return value


def bound_chunk_id(*, session_id: str, epoch: int, sender_id: str, payload_digest: str) -> str:
    canonical = f"{session_id}\n{epoch}\n{sender_id}\n{payload_digest}".encode()
    return hashlib.sha256(canonical).hexdigest()


def validate_chunk(value: Mapping[str, Any]) -> ValidatedChunk:
    data = _closed(
        value,
        frozenset(
            {
                "version",
                "chunk_id",
                "message_id",
                "session_id",
                "epoch",
                "sender_id",
                "traffic_class",
                "index",
                "total",
                "chunk_bytes",
                "total_bytes",
                "expires_at_ms",
                "payload_digest",
                "data",
            }
        ),
        "chunk",
    )
    if data.get("version") != CHUNK_VERSION:
        raise DataChannelContractError("unsupported_version", "version")
    traffic_class = str(data.get("traffic_class") or "")
    class_limit = TRAFFIC_CLASS_LIMITS.get(traffic_class)
    if class_limit is None:
        raise DataChannelContractError("unknown_traffic_class", "traffic_class")
    total = _integer(data.get("total"), "total", 1, MAX_CHUNKS)
    index = _integer(data.get("index"), "index", 0, MAX_CHUNKS - 1)
    if index >= total:
        raise DataChannelContractError("chunk_index_out_of_range", "index")
    wire_limit = TRAFFIC_CLASS_WIRE_LIMITS[traffic_class]
    total_bytes = _integer(data.get("total_bytes"), "total_bytes", 0, wire_limit)
    declared_chunk_bytes = _integer(data.get("chunk_bytes"), "chunk_bytes", 0, min(wire_limit, MAX_CHUNK_BYTES))
    chunk_data = _decode_base64(data.get("data"), "data", maximum=min(wire_limit, MAX_CHUNK_BYTES))
    if len(chunk_data) != declared_chunk_bytes or declared_chunk_bytes > total_bytes:
        raise DataChannelContractError("chunk_size_mismatch", "chunk_bytes")
    session_id = _identifier(data.get("session_id"), "session_id")
    sender_id = _identifier(data.get("sender_id"), "sender_id")
    epoch = _integer(data.get("epoch"), "epoch", 1, 2**53 - 1)
    payload_digest = _digest(data.get("payload_digest"), "payload_digest")
    expected_chunk_id = bound_chunk_id(
        session_id=session_id,
        epoch=epoch,
        sender_id=sender_id,
        payload_digest=payload_digest,
    )
    if data.get("chunk_id") != expected_chunk_id:
        raise DataChannelContractError("chunk_context_mismatch", "chunk_id")
    return ValidatedChunk(
        version=CHUNK_VERSION,
        chunk_id=expected_chunk_id,
        message_id=_identifier(data.get("message_id"), "message_id"),
        session_id=session_id,
        epoch=epoch,
        sender_id=sender_id,
        traffic_class=traffic_class,
        index=index,
        total=total,
        chunk_bytes=declared_chunk_bytes,
        total_bytes=total_bytes,
        expires_at_ms=_integer(data.get("expires_at_ms"), "expires_at_ms", 1, 2**53 - 1),
        payload_digest=payload_digest,
        data=chunk_data,
    )


def finite_non_negative(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


__all__ = [
    "ALLOWED_COMPRESSIONS",
    "CHUNK_VERSION",
    "CONTRACT_VERSION",
    "DataChannelContractError",
    "MAX_CHUNKS",
    "MAX_CHUNK_BYTES",
    "MAX_WIRE_BYTES",
    "MAX_WIRE_HEADER_BYTES",
    "TRAFFIC_CLASS_LIMITS",
    "TRAFFIC_CLASS_WIRE_LIMITS",
    "ValidatedChunk",
    "ValidatedDataChannelMessage",
    "WIRE_MAGIC",
    "bound_chunk_id",
    "parse_message",
    "parse_wire_message",
    "encode_wire_message",
    "validate_chunk",
    "validate_message",
]
