"""Closed, signed protocol contracts for bilateral speech-evidence sync.

The protocol is intentionally independent from Hub task orchestration.  A
validated peer message is evidence only; it can never create a task, dataset or
training job without a separate Hub admission decision.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

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


def validate_payload(
    message_type: str,
    payload: Mapping[str, Any],
    *,
    protocol_version: str = PROTOCOL_VERSION,
) -> Mapping[str, Any]:
    validator = _PAYLOAD_VALIDATORS.get(message_type)
    if validator is None:
        raise SpeechEvidenceProtocolError("speech_evidence_type_unsupported")
    if message_type == "offer":
        if protocol_version != OFFER_PROTOCOL_VERSION:
            raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_required")
        _offer(payload)
    else:
        validator(payload)
    return dict(payload)


def _inventory(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "inventory_id",
                "root_digest",
                "leaf_count",
                "total_bytes",
                "scope_digest",
                "retention_until_ms",
                "cursor_digest",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("inventory_id"), "speech_evidence_inventory_id_invalid")
    for name in ("root_digest", "scope_digest", "cursor_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    _integer(payload.get("leaf_count"), 0, 100_000, "speech_evidence_leaf_count_invalid")
    _integer(payload.get("total_bytes"), 0, MAX_TOTAL_BYTES, "speech_evidence_total_bytes_invalid")
    _integer(payload.get("retention_until_ms"), 1, MAX_SEQUENCE, "speech_evidence_retention_invalid")


def _diff(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "base_root_digest",
                "target_root_digest",
                "missing_group_ids",
                "changed_group_ids",
                "cursor_digest",
                "complete",
                "total_groups",
            }
        ),
    )
    _control(payload)
    for name in ("base_root_digest", "target_root_digest", "cursor_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    missing = _identifiers(payload.get("missing_group_ids"), MAX_GROUPS, "speech_evidence_groups_invalid")
    changed = _identifiers(payload.get("changed_group_ids"), MAX_GROUPS, "speech_evidence_groups_invalid")
    if set(missing) & set(changed):
        raise SpeechEvidenceProtocolError("speech_evidence_diff_inconsistent")
    if payload.get("complete") is not True and payload.get("complete") is not False:
        raise SpeechEvidenceProtocolError("speech_evidence_diff_complete_invalid")
    total = _integer(payload.get("total_groups"), 0, 100_000, "speech_evidence_group_count_invalid")
    if total < len(missing) + len(changed):
        raise SpeechEvidenceProtocolError("speech_evidence_diff_inconsistent")


def _offer(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "offer_id",
                "stage",
                "inventory_root_digest",
                "direction",
                "purpose",
                "data_classes",
                "fields",
                "retention_seconds",
                "trainer_class",
                "group_ids",
                "group_previews",
                "total_bytes",
                "sender_consent_digest",
                "recipient_consent_digest",
                "scope_digest",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("offer_id"), "speech_evidence_offer_id_invalid")
    if payload.get("stage") not in {"proposal", "acceptance"}:
        raise SpeechEvidenceProtocolError("speech_evidence_offer_stage_invalid")
    if payload.get("direction") not in {"sender_to_receiver", "receiver_to_sender"}:
        raise SpeechEvidenceProtocolError("speech_evidence_direction_invalid")
    if payload.get("purpose") not in {"peer_reconciliation", "speech_dataset_curation"}:
        raise SpeechEvidenceProtocolError("speech_evidence_purpose_invalid")
    _identifiers(payload.get("data_classes"), 8, "speech_evidence_data_classes_invalid")
    _identifiers(payload.get("fields"), 16, "speech_evidence_fields_invalid")
    group_ids = _identifiers(payload.get("group_ids"), MAX_GROUPS, "speech_evidence_groups_invalid")
    previews = _group_previews(payload.get("group_previews"))
    preview_ids = [str(row["group_id"]) for row in previews]
    if set(preview_ids) != set(group_ids) or len(preview_ids) != len(group_ids):
        raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_groups_mismatch")
    if payload.get("trainer_class") not in {"none", "speech_adaptation"}:
        raise SpeechEvidenceProtocolError("speech_evidence_trainer_class_invalid")
    _integer(payload.get("retention_seconds"), 1, MAX_RETENTION_SECONDS, "speech_evidence_retention_invalid")
    total_bytes = _integer(
        payload.get("total_bytes"), 1, MAX_TOTAL_BYTES, "speech_evidence_total_bytes_invalid"
    )
    if sum(int(row["size_bytes"]) for row in previews) != total_bytes:
        raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_size_mismatch")
    for name in (
        "inventory_root_digest",
        "sender_consent_digest",
        "recipient_consent_digest",
        "scope_digest",
    ):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")


def _group_previews(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_GROUPS:
        raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_invalid")
    previews: list[Mapping[str, Any]] = []
    group_ids: set[str] = set()
    source_digests: set[str] = set()
    expected = frozenset(
        {
            "preview_version",
            "group_id",
            "source_group_digest",
            "speaker_scope_digest",
            "quality_basis",
            "quality_digest",
            "resolution_digest",
            "original_candidates",
            "resolution_state",
            "selected_candidate_digest",
            "unresolved_region_digests",
            "comparison_digest",
            "revision",
            "size_bytes",
        }
    )
    for raw in value:
        row = _mapping(raw, "speech_evidence_offer_preview_invalid")
        _closed(row, expected)
        if row.get("preview_version") != GROUP_PREVIEW_VERSION:
            raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_version_invalid")
        group_id = _identifier(row.get("group_id"), "speech_evidence_group_id_invalid")
        source_digest = _digest(
            row.get("source_group_digest"), "speech_evidence_source_group_digest_invalid"
        )
        for name in (
            "speaker_scope_digest",
            "quality_digest",
            "resolution_digest",
        ):
            _digest(row.get(name), f"speech_evidence_{name}_invalid")
        if row.get("quality_basis") not in {"decision", "policy"}:
            raise SpeechEvidenceProtocolError("speech_evidence_quality_basis_invalid")
        revision = _integer(
            row.get("revision"), 1, 2**31 - 1, "speech_evidence_preview_revision_invalid"
        )
        _integer(row.get("size_bytes"), 1, MAX_TOTAL_BYTES, "speech_evidence_preview_size_invalid")
        raw_candidates = row.get("original_candidates")
        if not isinstance(raw_candidates, list) or not 1 <= len(raw_candidates) <= MAX_CANDIDATES:
            raise SpeechEvidenceProtocolError("speech_evidence_candidate_projection_invalid")
        candidates: list[dict[str, Any]] = []
        candidate_digests: set[str] = set()
        for index, raw_candidate in enumerate(raw_candidates):
            candidate = _mapping(raw_candidate, "speech_evidence_candidate_projection_invalid")
            _closed(
                candidate,
                frozenset({"ordinal", "candidate_digest", "authority_digest", "revision"}),
            )
            ordinal = _integer(
                candidate.get("ordinal"), 1, MAX_CANDIDATES, "speech_evidence_candidate_projection_invalid"
            )
            if ordinal != index + 1:
                raise SpeechEvidenceProtocolError("speech_evidence_candidate_projection_invalid")
            candidate_digest = _digest(
                candidate.get("candidate_digest"), "speech_evidence_candidate_projection_invalid"
            )
            _digest(candidate.get("authority_digest"), "speech_evidence_candidate_projection_invalid")
            candidate_revision = _integer(
                candidate.get("revision"), 1, 2**31 - 1, "speech_evidence_candidate_projection_invalid"
            )
            if candidate_revision > revision:
                raise SpeechEvidenceProtocolError("speech_evidence_candidate_projection_invalid")
            if candidate_digest in candidate_digests:
                raise SpeechEvidenceProtocolError("speech_evidence_candidate_projection_invalid")
            candidate_digests.add(candidate_digest)
            candidates.append(dict(candidate))
        state = row.get("resolution_state")
        if state not in {"resolved", "unresolved"}:
            raise SpeechEvidenceProtocolError("speech_evidence_comparison_resolution_invalid")
        selected = row.get("selected_candidate_digest")
        if selected is not None:
            selected = _digest(selected, "speech_evidence_selected_candidate_digest_invalid")
        raw_regions = row.get("unresolved_region_digests")
        if not isinstance(raw_regions, list) or len(raw_regions) > 1024:
            raise SpeechEvidenceProtocolError("speech_evidence_unresolved_regions_invalid")
        regions = [
            _digest(item, "speech_evidence_unresolved_regions_invalid") for item in raw_regions
        ]
        if regions != sorted(set(regions)):
            raise SpeechEvidenceProtocolError("speech_evidence_unresolved_regions_invalid")
        if (
            (state == "resolved" and (selected not in candidate_digests or regions))
            or (state == "unresolved" and (selected is not None or not regions))
        ):
            raise SpeechEvidenceProtocolError("speech_evidence_comparison_resolution_invalid")
        comparison_digest = _digest(
            row.get("comparison_digest"), "speech_evidence_comparison_digest_invalid"
        )
        if comparison_digest != group_preview_comparison_digest(
            source_group_digest=source_digest,
            revision=revision,
            original_candidates=candidates,
            resolution_state=str(state),
            selected_candidate_digest=selected,
            unresolved_region_digests=regions,
        ):
            raise SpeechEvidenceProtocolError("speech_evidence_comparison_digest_mismatch")
        if group_id != group_preview_group_id(source_digest, revision):
            raise SpeechEvidenceProtocolError("speech_evidence_source_group_mismatch")
        if row.get("resolution_digest") != group_preview_resolution_digest(source_digest, revision):
            raise SpeechEvidenceProtocolError("speech_evidence_resolution_digest_mismatch")
        if group_id in group_ids or source_digest in source_digests:
            raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_duplicate")
        group_ids.add(group_id)
        source_digests.add(source_digest)
        previews.append(dict(row))
    return tuple(previews)


def group_preview_group_id(source_group_digest: str, revision: int) -> str:
    """Derive a content-free group identifier from source lineage and revision."""

    _digest(source_group_digest, "speech_evidence_source_group_digest_invalid")
    _integer(revision, 1, 2**31 - 1, "speech_evidence_preview_revision_invalid")
    digest = canonical_sha256(
        {
            "domain": "ananta.speech-evidence-source-group.v1",
            "revision": revision,
            "source_group_digest": source_group_digest,
        }
    )
    return f"speech-group-{digest[:40]}"


def group_preview_resolution_digest(source_group_digest: str, revision: int) -> str:
    """Bind the advertised resolution scope without exposing resolution content."""

    _digest(source_group_digest, "speech_evidence_source_group_digest_invalid")
    _integer(revision, 1, 2**31 - 1, "speech_evidence_preview_revision_invalid")
    return canonical_sha256(
        {
            "domain": "ananta.speech-evidence-resolution-scope.v1",
            "revision": revision,
            "source_group_digest": source_group_digest,
        }
    )


def group_preview_comparison_digest(
    *,
    source_group_digest: str,
    revision: int,
    original_candidates: list[Mapping[str, Any]],
    resolution_state: str,
    selected_candidate_digest: str | None,
    unresolved_region_digests: list[str],
) -> str:
    """Bind the content-free candidate/resolution projection carried by an offer."""

    return canonical_sha256(
        {
            "domain": "ananta.speech-evidence-comparison-preview.v1",
            "source_group_digest": source_group_digest,
            "revision": revision,
            "original_candidates": [dict(value) for value in original_candidates],
            "resolution_state": resolution_state,
            "selected_candidate_digest": selected_candidate_digest,
            "unresolved_region_digests": list(unresolved_region_digests),
        }
    )


def _chunk(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "offer_id",
                "group_id",
                "chunk_index",
                "chunk_count",
                "plaintext_bytes",
                "plaintext_digest",
                "ciphertext_digest",
                "nonce_b64",
                "ciphertext_b64",
            }
        ),
    )
    if payload.get("traffic_class") != "evidence_bulk":
        raise SpeechEvidenceProtocolError("speech_evidence_traffic_class_invalid")
    _identifier(payload.get("offer_id"), "speech_evidence_offer_id_invalid")
    _identifier(payload.get("group_id"), "speech_evidence_group_id_invalid")
    count = _integer(payload.get("chunk_count"), 1, MAX_GROUPS, "speech_evidence_chunk_count_invalid")
    index = _integer(payload.get("chunk_index"), 0, count - 1, "speech_evidence_chunk_index_invalid")
    del index
    plain_size = _integer(
        payload.get("plaintext_bytes"), 1, MAX_CHUNK_PLAINTEXT_BYTES, "speech_evidence_chunk_oversized"
    )
    for name in ("plaintext_digest", "ciphertext_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    _b64(payload.get("nonce_b64"), "speech_evidence_nonce_invalid", exact_bytes=12)
    ciphertext = _b64(payload.get("ciphertext_b64"), "speech_evidence_ciphertext_invalid")
    if not 17 <= len(ciphertext) <= MAX_CHUNK_CIPHERTEXT_BYTES or len(ciphertext) != plain_size + 16:
        raise SpeechEvidenceProtocolError("speech_evidence_chunk_oversized")
    if hashlib.sha256(ciphertext).hexdigest() != payload.get("ciphertext_digest"):
        raise SpeechEvidenceProtocolError("speech_evidence_ciphertext_digest_mismatch")


def _chunk_ack(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "offer_id",
                "group_id",
                "acknowledged_indices",
                "first_missing_index",
                "received_bytes",
                "complete",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("offer_id"), "speech_evidence_offer_id_invalid")
    _identifier(payload.get("group_id"), "speech_evidence_group_id_invalid")
    indices = _integers(payload.get("acknowledged_indices"), MAX_GROUPS, 0, MAX_GROUPS - 1)
    if len(indices) != len(set(indices)) or indices != sorted(indices):
        raise SpeechEvidenceProtocolError("speech_evidence_ack_inconsistent")
    first_missing = _integer(
        payload.get("first_missing_index"), 0, MAX_GROUPS, "speech_evidence_ack_cursor_invalid"
    )
    if any(index >= first_missing for index in indices[:first_missing]) or set(range(first_missing)) - set(indices):
        raise SpeechEvidenceProtocolError("speech_evidence_ack_cursor_invalid")
    _integer(payload.get("received_bytes"), 0, MAX_TOTAL_BYTES, "speech_evidence_total_bytes_invalid")
    if payload.get("complete") not in {True, False}:
        raise SpeechEvidenceProtocolError("speech_evidence_ack_inconsistent")


def _resolution(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "resolution_id",
                "policy_version",
                "graph_digest",
                "candidate_ids",
                "accepted_candidate_ids",
                "unresolved_region_ids",
                "result_digest",
                "candidates",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("resolution_id"), "speech_evidence_resolution_id_invalid")
    _identifier(payload.get("policy_version"), "speech_evidence_policy_version_invalid")
    candidate_ids = _identifiers(payload.get("candidate_ids"), MAX_CANDIDATES, "speech_evidence_candidates_invalid")
    accepted = _identifiers(
        payload.get("accepted_candidate_ids"), MAX_CANDIDATES, "speech_evidence_candidates_invalid"
    )
    if not set(accepted) <= set(candidate_ids):
        raise SpeechEvidenceProtocolError("speech_evidence_resolution_inconsistent")
    _identifiers(payload.get("unresolved_region_ids"), 1024, "speech_evidence_regions_invalid")
    for name in ("graph_digest", "result_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise SpeechEvidenceProtocolError("speech_evidence_candidates_invalid")
    parsed_ids: list[str] = []
    for raw in candidates:
        item = _mapping(raw, "speech_evidence_candidate_invalid")
        _closed(
            item,
            frozenset(
                {
                    "candidate_id",
                    "source_id",
                    "contributor_digest",
                    "revision",
                    "lineage_digest",
                    "text",
                    "confidence",
                    "start_ms",
                    "end_ms",
                }
            ),
        )
        parsed_ids.append(_identifier(item.get("candidate_id"), "speech_evidence_candidate_invalid"))
        _identifier(item.get("source_id"), "speech_evidence_source_invalid")
        _digest(item.get("contributor_digest"), "speech_evidence_contributor_invalid")
        _digest(item.get("lineage_digest"), "speech_evidence_lineage_invalid")
        _integer(item.get("revision"), 1, 2**31 - 1, "speech_evidence_revision_invalid")
        text = item.get("text")
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT_CHARS:
            raise SpeechEvidenceProtocolError("speech_evidence_text_invalid")
        _finite(item.get("confidence"), 0.0, 1.0, "speech_evidence_confidence_invalid")
        start = _integer(item.get("start_ms"), 0, MAX_SEQUENCE, "speech_evidence_timing_invalid")
        end = _integer(item.get("end_ms"), 0, MAX_SEQUENCE, "speech_evidence_timing_invalid")
        if end < start:
            raise SpeechEvidenceProtocolError("speech_evidence_timing_invalid")
    if set(parsed_ids) != set(candidate_ids) or len(parsed_ids) != len(set(parsed_ids)):
        raise SpeechEvidenceProtocolError("speech_evidence_resolution_inconsistent")


def _receipt(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "receipt_id",
                "offer_id",
                "inventory_root_digest",
                "resolution_digest",
                "accepted_group_ids",
                "rejected_group_ids",
                "quarantined_group_ids",
                "consent_digest",
                "policy_digest",
                "result_digest",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("receipt_id"), "speech_evidence_receipt_id_invalid")
    _identifier(payload.get("offer_id"), "speech_evidence_offer_id_invalid")
    groups = []
    for name in ("accepted_group_ids", "rejected_group_ids", "quarantined_group_ids"):
        values = _identifiers(payload.get(name), MAX_GROUPS, "speech_evidence_groups_invalid")
        groups.extend(values)
    if len(groups) != len(set(groups)):
        raise SpeechEvidenceProtocolError("speech_evidence_receipt_inconsistent")
    for name in (
        "inventory_root_digest",
        "resolution_digest",
        "consent_digest",
        "policy_digest",
        "result_digest",
    ):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")


def _revocation(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "revocation_id",
                "group_ids",
                "scope_digest",
                "reason_code",
                "revocation_epoch",
                "deadline_at_ms",
                "requested_action",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("revocation_id"), "speech_evidence_revocation_id_invalid")
    _identifiers(payload.get("group_ids"), MAX_GROUPS, "speech_evidence_groups_invalid")
    _digest(payload.get("scope_digest"), "speech_evidence_scope_digest_invalid")
    _identifier(payload.get("reason_code"), "speech_evidence_reason_invalid")
    _integer(payload.get("revocation_epoch"), 1, 2**31 - 1, "speech_evidence_revocation_epoch_invalid")
    _integer(payload.get("deadline_at_ms"), 1, MAX_SEQUENCE, "speech_evidence_deadline_invalid")
    if payload.get("requested_action") not in {"delete", "stop_use"}:
        raise SpeechEvidenceProtocolError("speech_evidence_revocation_action_invalid")


def _revocation_ack(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "revocation_id",
                "scope_digest",
                "revocation_epoch",
                "impact_digest",
                "group_results",
                "decision",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("revocation_id"), "speech_evidence_revocation_id_invalid")
    for name in ("scope_digest", "impact_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    _integer(payload.get("revocation_epoch"), 1, 2**31 - 1, "speech_evidence_revocation_epoch_invalid")
    results = payload.get("group_results")
    if not isinstance(results, list) or not results or len(results) > MAX_GROUPS:
        raise SpeechEvidenceProtocolError("speech_evidence_revocation_ack_invalid")
    seen: set[str] = set()
    for raw in results:
        item = _mapping(raw, "speech_evidence_revocation_ack_invalid")
        _closed(item, frozenset({"group_id", "state", "reason_code"}))
        group_id = _identifier(item.get("group_id"), "speech_evidence_group_id_invalid")
        if group_id in seen or item.get("state") not in {"deleted", "use_stopped", "not_found", "unresolved"}:
            raise SpeechEvidenceProtocolError("speech_evidence_revocation_ack_invalid")
        seen.add(group_id)
        _identifier(item.get("reason_code"), "speech_evidence_reason_invalid")
    if payload.get("decision") not in {"complete", "partial", "unresolved"}:
        raise SpeechEvidenceProtocolError("speech_evidence_revocation_ack_invalid")


_PAYLOAD_VALIDATORS = {
    "inventory": _inventory,
    "diff": _diff,
    "offer": _offer,
    "chunk": _chunk,
    "chunk_ack": _chunk_ack,
    "resolution": _resolution,
    "receipt": _receipt,
    "revocation": _revocation,
    "revocation_ack": _revocation_ack,
}


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
]
