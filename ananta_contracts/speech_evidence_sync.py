"""Closed, signed protocol contracts for bilateral speech-evidence sync.

The protocol is intentionally independent from Hub task orchestration.  A
validated peer message is evidence only; it can never create a task, dataset or
training job without a separate Hub admission decision.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ananta_contracts.speech_evidence_sync_primitives import (
    canonical_json,
    canonical_sha256,
    _closed,
    CONTROL_TYPES,
    _digest,
    GROUP_PREVIEW_VERSION,
    _identifier,
    _integer,
    _mapping,
    MAX_CANDIDATES,
    MAX_CHUNK_CIPHERTEXT_BYTES,
    MAX_CHUNK_PLAINTEXT_BYTES,
    MAX_CLOCK_SKEW_MS,
    MAX_GROUPS,
    MAX_MESSAGE_BYTES,
    MAX_MESSAGE_TTL_MS,
    MAX_RETENTION_SECONDS,
    MAX_SEQUENCE,
    MAX_TEXT_CHARS,
    MAX_TOTAL_BYTES,
    MESSAGE_TYPES,
    OFFER_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    _replay_key,
    SIGNATURE_ALGORITHM,
    _signature_bytes,
    SIGNATURE_DOMAIN,
    SpeechEvidenceProtocolError,
    SUPPORTED_PROTOCOL_VERSIONS,
    _TOP_LEVEL_FIELDS,
)
from ananta_contracts.speech_evidence_sync_payloads import (
    group_preview_comparison_digest,
    group_preview_group_id,
    group_preview_resolution_digest,
    validate_payload,
)


@dataclass(frozen=True)
class SpeechEvidenceHeader:
    protocol_version: str
    message_type: str
    message_id: str
    session_id: str
    pair_id: str
    sender_id: str
    audience_id: str
    epoch: int
    sequence: int
    consent_version: int
    key_id: str
    issued_at_ms: int
    expires_at_ms: int
    payload_digest: str
    signature_algorithm: str
    signature_b64: str


@dataclass(frozen=True)
class VerifiedSpeechEvidenceMessage:
    header: SpeechEvidenceHeader
    payload: Mapping[str, Any]
    verification_digest: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.header.protocol_version,
            "message_type": self.header.message_type,
            "message_id": self.header.message_id,
            "session_id": self.header.session_id,
            "pair_id": self.header.pair_id,
            "sender_id": self.header.sender_id,
            "audience_id": self.header.audience_id,
            "epoch": self.header.epoch,
            "sequence": self.header.sequence,
            "consent_version": self.header.consent_version,
            "key_id": self.header.key_id,
            "issued_at_ms": self.header.issued_at_ms,
            "expires_at_ms": self.header.expires_at_ms,
            "payload_digest": self.header.payload_digest,
            "payload": dict(self.payload),
            "signature_algorithm": self.header.signature_algorithm,
            "signature_b64": self.header.signature_b64,
        }


class SpeechEvidencePublicKeyPort(Protocol):
    """Resolve a current peer key only through Hub-authorized identity state."""

    def resolve(
        self,
        *,
        session_id: str,
        pair_id: str,
        sender_id: str,
        audience_id: str,
        epoch: int,
        key_id: str,
    ) -> Ed25519PublicKey | None: ...


class ReplayStatePort(Protocol):
    def load(self) -> Mapping[str, Any] | None: ...

    def save(self, value: Mapping[str, Any]) -> None: ...


@dataclass
class _ReplayEntry:
    highest: int
    bitmap: int


class SpeechEvidenceReplayWindow:
    """Bounded sliding replay window, isolated by peer/session/epoch/class."""

    def __init__(
        self,
        *,
        width: int = 256,
        maximum_contexts: int = 2048,
        state_port: ReplayStatePort | None = None,
    ) -> None:
        if not 32 <= width <= 4096 or not 1 <= maximum_contexts <= 100_000:
            raise ValueError("speech_replay_policy_invalid")
        self._width = width
        self._maximum = maximum_contexts
        self._state_port = state_port
        self._entries: OrderedDict[str, _ReplayEntry] = OrderedDict()
        if state_port is not None:
            self._restore(state_port.load())

    def check(self, key: tuple[str, str, str, int, str], sequence: int) -> str | None:
        encoded = _replay_key(key)
        entry = self._entries.get(encoded)
        if entry is None:
            return None
        if sequence > entry.highest:
            return None
        offset = entry.highest - sequence
        if offset >= self._width:
            return "speech_evidence_sequence_stale"
        if entry.bitmap & (1 << offset):
            return "speech_evidence_replayed"
        return None

    def commit(self, key: tuple[str, str, str, int, str], sequence: int) -> None:
        encoded = _replay_key(key)
        entry = self._entries.pop(encoded, None)
        if entry is None:
            entry = _ReplayEntry(highest=sequence, bitmap=1)
        elif sequence > entry.highest:
            shift = sequence - entry.highest
            entry.bitmap = ((entry.bitmap << shift) | 1) & ((1 << self._width) - 1)
            entry.highest = sequence
        else:
            entry.bitmap |= 1 << (entry.highest - sequence)
        self._entries[encoded] = entry
        while len(self._entries) > self._maximum:
            self._entries.popitem(last=False)
        self._persist()

    def advance_epoch(self, *, session_id: str, pair_id: str, minimum_epoch: int) -> None:
        """Drop only older epochs after the Hub has authorized a new epoch."""

        if minimum_epoch < 1:
            raise ValueError("speech_replay_epoch_invalid")
        retained: OrderedDict[str, _ReplayEntry] = OrderedDict()
        for encoded, entry in self._entries.items():
            raw_session, raw_pair, _sender, raw_epoch, _message_class = encoded.split("\0")
            if raw_session == session_id and raw_pair == pair_id and int(raw_epoch) < minimum_epoch:
                continue
            retained[encoded] = entry
        self._entries = retained
        self._persist()

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "width": self._width,
            "entries": {
                key: {"highest": entry.highest, "bitmap_hex": format(entry.bitmap, "x")}
                for key, entry in self._entries.items()
            },
        }

    def _persist(self) -> None:
        if self._state_port is not None:
            self._state_port.save(self.snapshot())

    def _restore(self, value: Mapping[str, Any] | None) -> None:
        if not isinstance(value, Mapping) or value.get("version") != 1 or value.get("width") != self._width:
            return
        entries = value.get("entries")
        if not isinstance(entries, Mapping) or len(entries) > self._maximum:
            return
        for key, raw in entries.items():
            if not isinstance(key, str) or not isinstance(raw, Mapping):
                continue
            try:
                highest = _integer(raw.get("highest"), 1, MAX_SEQUENCE, "speech_replay_state_invalid")
                bitmap = int(str(raw.get("bitmap_hex")), 16)
            except (SpeechEvidenceProtocolError, ValueError):
                continue
            if 0 < bitmap < 1 << self._width:
                self._entries[key] = _ReplayEntry(highest, bitmap)


class SpeechEvidenceMessageVerifier:
    """Authenticate routing metadata before validating the potentially larger payload."""

    def __init__(
        self,
        keys: SpeechEvidencePublicKeyPort,
        replay: SpeechEvidenceReplayWindow,
        *,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._keys = keys
        self._replay = replay
        self._clock_ms = clock_ms

    def verify(
        self,
        raw: Mapping[str, Any] | bytes,
        *,
        expected_session_id: str,
        expected_pair_id: str,
        expected_audience_id: str,
        expected_epoch: int,
        expected_consent_version: int,
    ) -> VerifiedSpeechEvidenceMessage:
        mapping = parse_bounded_message(raw)
        header = parse_header(mapping)
        now = int(self._clock_ms())
        if header.session_id != expected_session_id or header.pair_id != expected_pair_id:
            raise SpeechEvidenceProtocolError("speech_evidence_wrong_pair")
        if header.audience_id != expected_audience_id:
            raise SpeechEvidenceProtocolError("speech_evidence_wrong_audience")
        if header.epoch != expected_epoch:
            raise SpeechEvidenceProtocolError("speech_evidence_epoch_stale")
        if header.consent_version != expected_consent_version:
            raise SpeechEvidenceProtocolError("speech_evidence_consent_stale")
        if header.expires_at_ms <= now or header.issued_at_ms > now + MAX_CLOCK_SKEW_MS:
            raise SpeechEvidenceProtocolError("speech_evidence_expired")
        if header.expires_at_ms > header.issued_at_ms + MAX_MESSAGE_TTL_MS:
            raise SpeechEvidenceProtocolError("speech_evidence_ttl_invalid")

        replay_key = (
            header.session_id,
            header.pair_id,
            header.sender_id,
            header.epoch,
            "evidence_bulk" if header.message_type == "chunk" else "control",
        )
        replay_reason = self._replay.check(replay_key, header.sequence)
        if replay_reason:
            raise SpeechEvidenceProtocolError(replay_reason)
        public_key = self._keys.resolve(
            session_id=header.session_id,
            pair_id=header.pair_id,
            sender_id=header.sender_id,
            audience_id=header.audience_id,
            epoch=header.epoch,
            key_id=header.key_id,
        )
        if public_key is None:
            raise SpeechEvidenceProtocolError("speech_evidence_key_unknown")
        try:
            public_key.verify(_signature_bytes(header.signature_b64), canonical_signing_bytes(mapping))
        except (InvalidSignature, ValueError, binascii.Error) as exc:
            raise SpeechEvidenceProtocolError("speech_evidence_signature_invalid") from exc

        payload = _mapping(mapping.get("payload"), "speech_evidence_payload_invalid")
        actual_payload_digest = canonical_sha256(payload)
        if actual_payload_digest != header.payload_digest:
            raise SpeechEvidenceProtocolError("speech_evidence_payload_digest_mismatch")
        validated = validate_payload(
            header.message_type,
            payload,
            protocol_version=header.protocol_version,
        )
        self._replay.commit(replay_key, header.sequence)
        verification_digest = canonical_sha256(
            {
                "domain": "ananta.speech-evidence-verification.v1",
                "message_id": header.message_id,
                "payload_digest": header.payload_digest,
                "signature_b64": header.signature_b64,
            }
        )
        return VerifiedSpeechEvidenceMessage(header, validated, verification_digest)


def parse_bounded_message(raw: Mapping[str, Any] | bytes) -> Mapping[str, Any]:
    if isinstance(raw, bytes):
        if not raw or len(raw) > MAX_MESSAGE_BYTES:
            raise SpeechEvidenceProtocolError("speech_evidence_message_oversized")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SpeechEvidenceProtocolError("speech_evidence_json_invalid") from exc
    else:
        value = raw
        try:
            if len(canonical_json(value)) > MAX_MESSAGE_BYTES:
                raise SpeechEvidenceProtocolError("speech_evidence_message_oversized")
        except (TypeError, ValueError) as exc:
            raise SpeechEvidenceProtocolError("speech_evidence_non_finite") from exc
    return _mapping(value, "speech_evidence_message_invalid")


def parse_header(raw: Mapping[str, Any], *, now_ms: int | None = None) -> SpeechEvidenceHeader:
    _closed(raw, _TOP_LEVEL_FIELDS)
    protocol_version = str(raw.get("protocol_version") or "")
    if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise SpeechEvidenceProtocolError("speech_evidence_version_unsupported")
    message_type = str(raw.get("message_type") or "")
    if message_type not in MESSAGE_TYPES:
        raise SpeechEvidenceProtocolError("speech_evidence_type_unsupported")
    if raw.get("signature_algorithm") != SIGNATURE_ALGORITHM:
        raise SpeechEvidenceProtocolError("speech_evidence_signature_algorithm_unsupported")
    header = SpeechEvidenceHeader(
        protocol_version=protocol_version,
        message_type=message_type,
        message_id=_identifier(raw.get("message_id"), "speech_evidence_message_id_invalid"),
        session_id=_identifier(raw.get("session_id"), "speech_evidence_session_invalid"),
        pair_id=_identifier(raw.get("pair_id"), "speech_evidence_pair_invalid"),
        sender_id=_identifier(raw.get("sender_id"), "speech_evidence_sender_invalid"),
        audience_id=_identifier(raw.get("audience_id"), "speech_evidence_audience_invalid"),
        epoch=_integer(raw.get("epoch"), 1, 2**31 - 1, "speech_evidence_epoch_invalid"),
        sequence=_integer(raw.get("sequence"), 1, MAX_SEQUENCE, "speech_evidence_sequence_invalid"),
        consent_version=_integer(
            raw.get("consent_version"), 1, 2**31 - 1, "speech_evidence_consent_version_invalid"
        ),
        key_id=_identifier(raw.get("key_id"), "speech_evidence_key_id_invalid"),
        issued_at_ms=_integer(raw.get("issued_at_ms"), 1, MAX_SEQUENCE, "speech_evidence_issued_at_invalid"),
        expires_at_ms=_integer(raw.get("expires_at_ms"), 1, MAX_SEQUENCE, "speech_evidence_expiry_invalid"),
        payload_digest=_digest(raw.get("payload_digest"), "speech_evidence_payload_digest_invalid"),
        signature_algorithm=SIGNATURE_ALGORITHM,
        signature_b64=str(raw.get("signature_b64") or ""),
    )
    _signature_bytes(header.signature_b64)
    if header.sender_id == header.audience_id:
        raise SpeechEvidenceProtocolError("speech_evidence_reflection_detected")
    if now_ms is not None:
        if header.expires_at_ms <= now_ms or header.issued_at_ms > now_ms + MAX_CLOCK_SKEW_MS:
            raise SpeechEvidenceProtocolError("speech_evidence_expired")
        if header.expires_at_ms > header.issued_at_ms + MAX_MESSAGE_TTL_MS:
            raise SpeechEvidenceProtocolError("speech_evidence_ttl_invalid")
    expected_traffic = "evidence_bulk" if message_type == "chunk" else "control"
    payload = _mapping(raw.get("payload"), "speech_evidence_payload_invalid")
    if payload.get("traffic_class") != expected_traffic:
        raise SpeechEvidenceProtocolError("speech_evidence_traffic_class_invalid")
    return header


def canonical_signing_bytes(raw: Mapping[str, Any]) -> bytes:
    header = parse_header(raw)
    signed = {
        "domain": SIGNATURE_DOMAIN,
        "protocol_version": header.protocol_version,
        "message_type": header.message_type,
        "message_id": header.message_id,
        "session_id": header.session_id,
        "pair_id": header.pair_id,
        "sender_id": header.sender_id,
        "audience_id": header.audience_id,
        "epoch": header.epoch,
        "sequence": header.sequence,
        "consent_version": header.consent_version,
        "key_id": header.key_id,
        "issued_at_ms": header.issued_at_ms,
        "expires_at_ms": header.expires_at_ms,
        "payload_digest": header.payload_digest,
        "signature_algorithm": header.signature_algorithm,
    }
    return canonical_json(signed)


def sign_message(unsigned: Mapping[str, Any], private_key: Ed25519PrivateKey) -> dict[str, Any]:
    raw = dict(unsigned)
    raw["signature_b64"] = base64.b64encode(b"\x00" * 64).decode("ascii")
    header = parse_header(raw)
    payload = _mapping(raw.get("payload"), "speech_evidence_payload_invalid")
    if raw.get("payload_digest") != canonical_sha256(payload):
        raise SpeechEvidenceProtocolError("speech_evidence_payload_digest_mismatch")
    validate_payload(str(raw["message_type"]), payload, protocol_version=header.protocol_version)
    raw["signature_b64"] = base64.b64encode(private_key.sign(canonical_signing_bytes(raw))).decode("ascii")
    return raw


__all__ = [
    "CONTROL_TYPES",
    "MAX_CHUNK_CIPHERTEXT_BYTES",
    "MAX_CHUNK_PLAINTEXT_BYTES",
    "MAX_GROUPS",
    "MAX_MESSAGE_BYTES",
    "GROUP_PREVIEW_VERSION",
    "OFFER_PROTOCOL_VERSION",
    "PROTOCOL_VERSION",
    "SIGNATURE_ALGORITHM",
    "SpeechEvidenceHeader",
    "SpeechEvidenceMessageVerifier",
    "SpeechEvidenceProtocolError",
    "SpeechEvidencePublicKeyPort",
    "SpeechEvidenceReplayWindow",
    "VerifiedSpeechEvidenceMessage",
    "canonical_json",
    "canonical_sha256",
    "canonical_signing_bytes",
    "group_preview_group_id",
    "group_preview_comparison_digest",
    "group_preview_resolution_digest",
    "parse_bounded_message",
    "parse_header",
    "sign_message",
    "validate_payload",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "SIGNATURE_DOMAIN",
    "MAX_CANDIDATES",
    "MAX_TEXT_CHARS",
    "MAX_TOTAL_BYTES",
    "MAX_RETENTION_SECONDS",
    "MAX_SEQUENCE",
    "MAX_CLOCK_SKEW_MS",
    "MAX_MESSAGE_TTL_MS",
    "MESSAGE_TYPES",
    "ReplayStatePort",
]
