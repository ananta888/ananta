"""Stable data contracts for Hub-owned SFU group-key persistence."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class GroupKeyEpochAuthorization:
    version: int
    authorization_id: str
    tenant_id: str
    room_id: str
    publication_id: str
    epoch: int
    previous_epoch: int
    member_set_digest: str
    member_ids: tuple[str, ...]
    key_package_refs: dict[str, str]
    valid_from_ms: int
    expires_at_ms: int
    rekey_deadline_ms: int
    reason: str
    hub_key_id: str
    membership_epoch: int | None = None
    signature_b64: str = ""

    def unsigned_dict(self) -> dict[str, object]:
        raw = asdict(self)
        raw["member_ids"] = list(self.member_ids)
        raw.pop("signature_b64")
        if raw["membership_epoch"] is None:
            raw.pop("membership_epoch")
        return raw


@dataclass(frozen=True, slots=True)
class SfuGroupKeyEpochState:
    authorization: GroupKeyEpochAuthorization
    session_id: str
    publisher_digest: str
    distribution_mode: Literal["bounded_rewrap"] = "bounded_rewrap"
    status: Literal["active", "revoked", "expired", "tombstoned"] = "active"
    package_count: int = 0
    total_package_bytes: int = 0
    delivered_member_ids: tuple[str, ...] = ()
    acknowledged_member_ids: tuple[str, ...] = ()
    fencing_token: int = 1
    version: int = 1


@dataclass(frozen=True, slots=True)
class SfuGroupKeyPackageWrite:
    recipient_id: str
    recipient_digest: str
    package_ref: str
    opaque_package: bytes
    package_digest: str
    expires_at_ms: int

    def __repr__(self) -> str:
        return (
            "SfuGroupKeyPackageWrite("
            f"recipient_digest={self.recipient_digest!r}, package_ref={self.package_ref!r}, "
            "opaque_package=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class SfuGroupKeyPackageDelivery:
    authorization: GroupKeyEpochAuthorization
    publisher_id: str
    package_ref: str
    opaque_package: bytes
    package_digest: str
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class SfuGroupKeyReceipt:
    tenant_id: str
    actor_digest: str
    operation: Literal["prepare", "deliver"]
    idempotency_key_digest: str
    request_digest: str
    result: dict
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class SfuGroupKeyMutationResult:
    status: Literal["saved", "conflict", "stale_epoch", "expired", "not_found"]
    state: SfuGroupKeyEpochState | None = None
    replayed: bool = False
    reason_code: str | None = None

    @property
    def committed(self) -> bool:
        return self.status == "saved"


@dataclass(frozen=True, slots=True)
class SfuGroupKeyDeliveryPage:
    items: tuple[SfuGroupKeyPackageDelivery, ...]
    next_cursor: str


class SfuHubSecretEnvelopeError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuHubSealedSecret:
    key_id: str
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)

    def __init__(self, key_id: str, nonce: bytes, ciphertext: bytes) -> None:
        object.__setattr__(self, "key_id", key_id)
        object.__setattr__(self, "nonce", bytes(nonce))
        object.__setattr__(self, "ciphertext", bytes(ciphertext))
        if _KEY_ID.fullmatch(key_id) is None or len(nonce) != 12 or len(ciphertext) < 17:
            raise SfuHubSecretEnvelopeError("sfu_secret_envelope_invalid")

    def __repr__(self) -> str:
        return f"SfuHubSealedSecret(key_id={self.key_id!r}, redacted=True)"


@dataclass(frozen=True, slots=True)
class SfuHubBlindIndex:
    key_id: str
    digest: str


__all__ = [
    "GroupKeyEpochAuthorization",
    "SfuGroupKeyDeliveryPage",
    "SfuGroupKeyEpochState",
    "SfuGroupKeyMutationResult",
    "SfuGroupKeyPackageDelivery",
    "SfuGroupKeyPackageWrite",
    "SfuGroupKeyReceipt",
    "SfuHubBlindIndex",
    "SfuHubSealedSecret",
    "SfuHubSecretEnvelopeError",
]
