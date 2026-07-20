"""Canonical security negotiation contract used before WebRTC activation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Mapping

SECURITY_MODES = ("transport_only", "opportunistic_e2ee", "strict_e2ee")
SECURITY_MODE_STRENGTH = {name: index for index, name in enumerate(SECURITY_MODES)}
ALLOWED_ALGORITHMS = frozenset({"AES-256-GCM", "ECDH-P256-HKDF-SHA256"})
ALLOWED_PAYLOAD_CLASSES = frozenset({"control", "media", "semantic", "bulk"})


class SecurityNegotiationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SecurityProposal:
    version: int
    negotiation_id: str
    scope_kind: str
    scope_id: str
    sender_id: str
    recipient_id: str
    minimum_mode: str
    selected_mode: str
    algorithms: tuple[str, ...]
    key_epoch: int
    payload_classes: tuple[str, ...]
    expires_at_ms: int

    def canonical_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FinalSecurityContract:
    offer: SecurityProposal
    answer: SecurityProposal
    digest: str
    signature: str


def parse_security_proposal(raw: Mapping[str, object]) -> SecurityProposal:
    expected = {
        "version",
        "negotiation_id",
        "scope_kind",
        "scope_id",
        "sender_id",
        "recipient_id",
        "minimum_mode",
        "selected_mode",
        "algorithms",
        "key_epoch",
        "payload_classes",
        "expires_at_ms",
    }
    if set(raw) != expected:
        raise SecurityNegotiationError("negotiation_fields_invalid")
    if raw.get("version") != 1 or isinstance(raw.get("version"), bool):
        raise SecurityNegotiationError("negotiation_version_invalid")
    for field in ("negotiation_id", "scope_id", "sender_id", "recipient_id"):
        if not _id(raw.get(field)):
            raise SecurityNegotiationError("negotiation_identity_invalid")
    if raw.get("scope_kind") not in {"session", "room"}:
        raise SecurityNegotiationError("negotiation_scope_invalid")
    minimum = raw.get("minimum_mode")
    selected = raw.get("selected_mode")
    if minimum not in SECURITY_MODE_STRENGTH or selected not in SECURITY_MODE_STRENGTH:
        raise SecurityNegotiationError("security_mode_invalid")
    if SECURITY_MODE_STRENGTH[str(selected)] < SECURITY_MODE_STRENGTH[str(minimum)]:
        raise SecurityNegotiationError("security_mode_below_minimum")
    algorithms = _closed_string_tuple(raw.get("algorithms"), ALLOWED_ALGORITHMS, "algorithm_invalid")
    payload_classes = _closed_string_tuple(raw.get("payload_classes"), ALLOWED_PAYLOAD_CLASSES, "payload_class_invalid")
    epoch = raw.get("key_epoch")
    expiry = raw.get("expires_at_ms")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or not 1 <= epoch <= 2**31 - 1:
        raise SecurityNegotiationError("epoch_invalid")
    if not isinstance(expiry, int) or isinstance(expiry, bool) or expiry < 1:
        raise SecurityNegotiationError("negotiation_expiry_invalid")
    return SecurityProposal(
        version=1,
        negotiation_id=str(raw["negotiation_id"]),
        scope_kind=str(raw["scope_kind"]),
        scope_id=str(raw["scope_id"]),
        sender_id=str(raw["sender_id"]),
        recipient_id=str(raw["recipient_id"]),
        minimum_mode=str(minimum),
        selected_mode=str(selected),
        algorithms=algorithms,
        key_epoch=epoch,
        payload_classes=payload_classes,
        expires_at_ms=expiry,
    )


def security_contract_digest(offer: SecurityProposal, answer: SecurityProposal) -> str:
    raw = json.dumps(
        {
            "domain": "ananta.webrtc.security-negotiation.v1",
            "offer": offer.canonical_dict(),
            "answer": answer.canonical_dict(),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _closed_string_tuple(value: object, allowed: frozenset[str], reason: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or len(value) > len(allowed):
        raise SecurityNegotiationError(reason)
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise SecurityNegotiationError(reason)
    if len(set(value)) != len(value) or list(value) != sorted(value):
        raise SecurityNegotiationError("canonicalization_invalid")
    return tuple(value)


def _id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value[0].isalnum()
        and all(char.isalnum() or char in "._:-" for char in value)
    )


__all__ = [
    "ALLOWED_ALGORITHMS",
    "ALLOWED_PAYLOAD_CLASSES",
    "FinalSecurityContract",
    "SECURITY_MODE_STRENGTH",
    "SecurityNegotiationError",
    "SecurityProposal",
    "parse_security_proposal",
    "security_contract_digest",
]
