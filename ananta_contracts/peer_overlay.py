"""Closed contracts for Hub-authorized peer overlay routes."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class OverlayCapability(StrEnum):
    DIRECT = "direct"
    MESH_MEDIA = "mesh_media"
    DATA_RELAY = "data_relay"
    LAYER_SUPPORT = "layer_support"
    TURN = "turn"


class OverlayTrafficClass(StrEnum):
    CONTROL = "control"
    REKEY = "rekey"
    EVENT = "event"
    SEMANTIC = "semantic"
    BULK = "bulk"


class OverlayReleaseStage(StrEnum):
    DISABLED = "disabled"
    OBSERVE_ONLY = "observe_only"
    DATA_CANARY = "data_canary"
    MEDIA_INTERNAL = "media_internal"
    MEDIA_CANARY = "media_canary"
    LIMITED = "limited"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class OverlayEpochs:
    membership: int
    key: int
    route: int
    topology: int

    def __post_init__(self) -> None:
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in asdict(self).values()):
            raise ValueError("peer_overlay_epoch_invalid")

    def assert_successor(self, previous: "OverlayEpochs", *, change: str) -> None:
        expected = {
            "membership": OverlayEpochs(previous.membership + 1, previous.key + 1, previous.route, previous.topology),
            "route": OverlayEpochs(previous.membership, previous.key, previous.route + 1, previous.topology),
            "topology": OverlayEpochs(previous.membership, previous.key, previous.route + 1, previous.topology + 1),
        }.get(change)
        if expected is None or self != expected:
            raise ValueError("peer_overlay_epoch_transition_invalid")


@dataclass(frozen=True, slots=True)
class PeerRouteLease:
    version: int
    lease_id: str
    tenant_id: str
    room_id: str
    publication_id: str
    child_peer_id: str
    primary_parent_id: str
    backup_parent_id: str | None
    epochs: OverlayEpochs
    capabilities: tuple[OverlayCapability, ...]
    traffic_classes: tuple[OverlayTrafficClass, ...]
    max_hops: int
    issued_at: str
    expires_at: str
    nonce: str
    hub_key_id: str
    signature: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "epochs", _epochs(self.epochs))
        object.__setattr__(self, "capabilities", tuple(OverlayCapability(str(item)) for item in self.capabilities))
        object.__setattr__(
            self, "traffic_classes", tuple(OverlayTrafficClass(str(item)) for item in self.traffic_classes)
        )
        if self.version != 1:
            raise ValueError("peer_overlay_lease_version_unsupported")
        for field in (
            "lease_id",
            "tenant_id",
            "room_id",
            "publication_id",
            "child_peer_id",
            "primary_parent_id",
            "nonce",
            "hub_key_id",
        ):
            require_overlay_id(getattr(self, field), field)
        if self.backup_parent_id is not None:
            require_overlay_id(self.backup_parent_id, "backup_parent_id")
        if self.child_peer_id in {self.primary_parent_id, self.backup_parent_id}:
            raise ValueError("peer_overlay_lease_self_parent")
        if not self.capabilities or len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("peer_overlay_lease_capabilities_invalid")
        if not self.traffic_classes or len(set(self.traffic_classes)) != len(self.traffic_classes):
            raise ValueError("peer_overlay_lease_traffic_classes_invalid")
        if not 1 <= self.max_hops <= 8:
            raise ValueError("peer_overlay_lease_hop_limit_invalid")
        _parse_time(self.issued_at)
        _parse_time(self.expires_at)

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature", None)
        value["capabilities"] = [item.value for item in self.capabilities]
        value["traffic_classes"] = [item.value for item in self.traffic_classes]
        return value

    def sign(self, key: bytes) -> "PeerRouteLease":
        return replace(self, signature=_signature(key, self.unsigned()))

    def verify(
        self,
        key: bytes,
        *,
        now: str,
        tenant_id: str,
        room_id: str,
        publication_id: str,
        child_peer_id: str,
        minimum_epochs: OverlayEpochs,
    ) -> None:
        if not hmac.compare_digest(_signature(key, self.unsigned()), self.signature):
            raise ValueError("peer_overlay_lease_signature_invalid")
        if (self.tenant_id, self.room_id, self.publication_id, self.child_peer_id) != (
            tenant_id,
            room_id,
            publication_id,
            child_peer_id,
        ):
            raise ValueError("peer_overlay_lease_scope_mismatch")
        if _parse_time(now) < _parse_time(self.issued_at) or _parse_time(now) >= _parse_time(self.expires_at):
            raise ValueError("peer_overlay_lease_expired")
        if any(
            current < minimum
            for current, minimum in zip(asdict(self.epochs).values(), asdict(minimum_epochs).values(), strict=True)
        ):
            raise ValueError("peer_overlay_lease_epoch_stale")


@dataclass(frozen=True, slots=True)
class PeerLinkTicket:
    version: int
    ticket_id: str
    lease_id: str
    tenant_id: str
    room_id: str
    publication_id: str
    initiator_peer_id: str
    responder_peer_id: str
    route_epoch: int
    ice_policy: str
    nonce: str
    issued_at: str
    expires_at: str
    signature: str = ""

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ValueError("peer_overlay_ticket_version_unsupported")
        for field in (
            "ticket_id",
            "lease_id",
            "tenant_id",
            "room_id",
            "publication_id",
            "initiator_peer_id",
            "responder_peer_id",
            "nonce",
        ):
            require_overlay_id(getattr(self, field), field)
        if self.initiator_peer_id == self.responder_peer_id:
            raise ValueError("peer_overlay_ticket_self_link")
        if self.ice_policy not in {"all", "relay"}:
            raise ValueError("peer_overlay_ticket_ice_policy_invalid")
        if self.route_epoch < 1:
            raise ValueError("peer_overlay_ticket_epoch_invalid")

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature", None)
        return value

    def sign(self, key: bytes) -> "PeerLinkTicket":
        return replace(self, signature=_signature(key, self.unsigned()))

    def verify(self, key: bytes, *, now: str, expected_lease: PeerRouteLease) -> None:
        if not hmac.compare_digest(_signature(key, self.unsigned()), self.signature):
            raise ValueError("peer_overlay_ticket_signature_invalid")
        if self.lease_id != expected_lease.lease_id or self.route_epoch != expected_lease.epochs.route:
            raise ValueError("peer_overlay_ticket_lease_mismatch")
        peers = {self.initiator_peer_id, self.responder_peer_id}
        allowed_edges = [{expected_lease.child_peer_id, expected_lease.primary_parent_id}]
        if expected_lease.backup_parent_id:
            allowed_edges.append({expected_lease.child_peer_id, expected_lease.backup_parent_id})
        if peers not in allowed_edges:
            raise ValueError("peer_overlay_ticket_edge_mismatch")
        if _parse_time(now) < _parse_time(self.issued_at) or _parse_time(now) >= _parse_time(self.expires_at):
            raise ValueError("peer_overlay_ticket_expired")


@dataclass(frozen=True, slots=True)
class MembershipEventV1:
    version: int
    event_id: str
    tenant_id: str
    room_id: str
    sequence: int
    previous_digest: str | None
    action: str
    subject_peer_id: str
    member_ids: tuple[str, ...]
    epochs: OverlayEpochs
    issued_at: str
    expires_at: str
    hub_key_id: str
    replacement_peer_id: str | None = None
    signature: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "epochs", _epochs(self.epochs))
        object.__setattr__(self, "member_ids", tuple(str(item) for item in self.member_ids))
        if self.version != 1:
            raise ValueError("peer_overlay_membership_version_unsupported")
        for field in ("event_id", "tenant_id", "room_id", "subject_peer_id", "hub_key_id"):
            require_overlay_id(getattr(self, field), field)
        if self.action not in {"join", "leave", "revoke", "device_replace", "snapshot"}:
            raise ValueError("peer_overlay_membership_action_invalid")
        if self.action == "device_replace":
            require_overlay_id(self.replacement_peer_id, "replacement_peer_id")
            if self.replacement_peer_id == self.subject_peer_id:
                raise ValueError("peer_overlay_membership_replacement_invalid")
        elif self.replacement_peer_id is not None:
            raise ValueError("peer_overlay_membership_replacement_unexpected")
        if self.sequence < 1 or len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("peer_overlay_membership_sequence_invalid")
        for member_id in self.member_ids:
            require_overlay_id(member_id, "member_id")
        if self.previous_digest is not None:
            require_digest(self.previous_digest, "previous_digest")

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature", None)
        value["member_ids"] = list(self.member_ids)
        return value

    @property
    def event_digest(self) -> str:
        return canonical_overlay_digest(self.unsigned())

    def sign(self, key: bytes) -> "MembershipEventV1":
        return replace(self, signature=_signature(key, self.unsigned()))

    def verify(
        self,
        key: bytes,
        *,
        expected_hub_key_id: str,
        now: str,
        tenant_id: str,
        room_id: str,
        expected_sequence: int,
        expected_previous_digest: str | None,
    ) -> None:
        if self.hub_key_id != require_overlay_id(expected_hub_key_id, "hub_key_id"):
            raise ValueError("peer_overlay_membership_hub_key_unknown")
        if not hmac.compare_digest(_signature(key, self.unsigned()), self.signature):
            raise ValueError("peer_overlay_membership_signature_invalid")
        if (self.tenant_id, self.room_id) != (tenant_id, room_id):
            raise ValueError("peer_overlay_membership_scope_mismatch")
        if self.sequence != expected_sequence or self.previous_digest != expected_previous_digest:
            raise ValueError("peer_overlay_membership_fork_or_gap")
        if _parse_time(now) < _parse_time(self.issued_at) or _parse_time(now) >= _parse_time(self.expires_at):
            raise ValueError("peer_overlay_membership_event_expired")


def canonical_overlay_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def require_overlay_id(value: object, field: str) -> str:
    candidate = str(value or "").strip()
    if not _ID.fullmatch(candidate):
        raise ValueError(f"peer_overlay_{field}_invalid")
    return candidate


def require_digest(value: object, field: str) -> str:
    candidate = str(value or "").strip()
    if not _DIGEST.fullmatch(candidate):
        raise ValueError(f"peer_overlay_{field}_invalid")
    return candidate


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _signature(key: bytes, value: Mapping[str, Any]) -> str:
    if len(key) < 32:
        raise ValueError("peer_overlay_signing_key_too_short")
    return hmac.new(key, canonical_overlay_digest(value).encode(), hashlib.sha256).hexdigest()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("peer_overlay_time_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("peer_overlay_time_invalid")
    return parsed.astimezone(timezone.utc)


def _epochs(value: OverlayEpochs | Mapping[str, Any]) -> OverlayEpochs:
    if isinstance(value, OverlayEpochs):
        return value
    return OverlayEpochs(**dict(value))


__all__ = [
    "OverlayCapability",
    "OverlayEpochs",
    "OverlayReleaseStage",
    "OverlayTrafficClass",
    "MembershipEventV1",
    "PeerLinkTicket",
    "PeerRouteLease",
    "canonical_overlay_digest",
    "require_digest",
    "require_overlay_id",
    "utc_now",
]
