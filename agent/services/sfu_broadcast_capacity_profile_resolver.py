"""Fail-closed Hub authority for SFU broadcast participant capacity."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROFILE_PATH = _ROOT / "config" / "sfu_broadcast_capacity.default.json"
_DEFAULT_LIVEKIT_PATH = _ROOT / "config" / "livekit.semantic-media.yaml"
_PROFILE_SCHEMA = "ananta.webrtc.sfu-broadcast-capacity-profile.v1"
_RESOLVED_SCHEMA = "ananta.webrtc.sfu-broadcast-capacity-resolved.v1"
_SCHEMA_REF = "../schemas/webrtc/sfu_broadcast_capacity_profile.v1.json"
_LEGACY_PARTICIPANT_CAP = 8
_ADMINISTRATIVE_LIVEKIT_HARD_CAP = 250
_CAPACITY_TEST_TIERS = (10, 25, 50, 100, 250)
_CAPACITY_GATE_ID = "SFB-GATE-009"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROFILE_KEYS = frozenset(
    {
        "$schema",
        "schema",
        "schema_version",
        "profile_id",
        "profile_revision",
        "legacy_participant_cap",
        "livekit_hard_cap",
        "active_hub_room_cap",
        "room_admission_cap",
        "angular_display_cap",
        "test_tiers",
        "activation",
    }
)
_ACTIVATION_KEYS = frozenset(
    {
        "state",
        "gate_id",
        "gate_passed",
        "candidate_cap",
        "approval_id",
        "expected_previous_revision",
        "approved_revision",
    }
)


class SfuBroadcastCapacityProfileError(RuntimeError):
    """Configuration is absent, malformed, contradictory, or unapproved."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ResolvedSfuBroadcastCapacityProfile:
    profile_id: str
    schema_version: int
    profile_revision: int
    profile_digest: str
    livekit_hard_cap: int
    active_hub_room_cap: int
    room_admission_cap: int
    angular_display_cap: int
    activation_state: str
    gate_id: str
    gate_passed: bool
    approval_id: str | None

    @property
    def max_publication_recipients(self) -> int:
        return self.room_admission_cap - 1

    def allows_participant_count(self, count: object) -> bool:
        return _is_integer(count) and 0 <= count <= self.room_admission_cap

    def allows_receiver_count(self, count: object) -> bool:
        return _is_integer(count) and 0 <= count <= self.max_publication_recipients

    def public_contract(self, *, room_id: str) -> dict[str, Any]:
        if not _IDENTIFIER.fullmatch(room_id):
            raise SfuBroadcastCapacityProfileError("capacity_room_id_invalid")
        return {
            "schema": _RESOLVED_SCHEMA,
            "profile_id": self.profile_id,
            "schema_version": self.schema_version,
            "profile_revision": self.profile_revision,
            "profile_digest": self.profile_digest,
            "room_id": room_id,
            "livekit_hard_cap": self.livekit_hard_cap,
            "active_hub_room_cap": self.active_hub_room_cap,
            "room_admission_cap": self.room_admission_cap,
            "angular_display_cap": self.angular_display_cap,
            "activation_state": self.activation_state,
            "gate_id": self.gate_id,
            "gate_passed": self.gate_passed,
            "approval_id": self.approval_id,
        }


class SfuBroadcastCapacityProfilePort(Protocol):
    def resolve(self) -> ResolvedSfuBroadcastCapacityProfile: ...


class SfuBroadcastCapacityProfileResolver:
    """Load and audit one immutable profile against the deployed LiveKit cap."""

    def __init__(
        self,
        profile_path: str | Path = _DEFAULT_PROFILE_PATH,
        livekit_config_path: str | Path = _DEFAULT_LIVEKIT_PATH,
    ) -> None:
        self._profile_path = Path(profile_path)
        self._livekit_config_path = Path(livekit_config_path)
        self._lock = threading.Lock()
        self._resolved: ResolvedSfuBroadcastCapacityProfile | None = None

    def resolve(self) -> ResolvedSfuBroadcastCapacityProfile:
        resolved = self._resolved
        if resolved is not None:
            return resolved
        with self._lock:
            if self._resolved is None:
                self._resolved = self._load_and_audit()
            return self._resolved

    def _load_and_audit(self) -> ResolvedSfuBroadcastCapacityProfile:
        try:
            raw_bytes = self._profile_path.read_bytes()
            raw = json.loads(raw_bytes)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SfuBroadcastCapacityProfileError("capacity_profile_unavailable") from exc
        if not isinstance(raw, dict) or set(raw) != _PROFILE_KEYS:
            raise SfuBroadcastCapacityProfileError("capacity_profile_fields_invalid")
        if raw["$schema"] != _SCHEMA_REF or raw["schema"] != _PROFILE_SCHEMA or raw["schema_version"] != 1:
            raise SfuBroadcastCapacityProfileError("capacity_profile_schema_invalid")
        profile_id = raw["profile_id"]
        if not isinstance(profile_id, str) or not _IDENTIFIER.fullmatch(profile_id):
            raise SfuBroadcastCapacityProfileError("capacity_profile_id_invalid")
        revision = _positive_integer(raw["profile_revision"], "capacity_profile_revision_invalid")
        legacy_cap = _positive_integer(raw["legacy_participant_cap"], "capacity_legacy_cap_invalid")
        hard_cap = _positive_integer(raw["livekit_hard_cap"], "capacity_livekit_hard_cap_invalid")
        active_cap = _positive_integer(raw["active_hub_room_cap"], "capacity_active_cap_invalid")
        room_cap = _positive_integer(raw["room_admission_cap"], "capacity_room_cap_invalid")
        display_cap = _positive_integer(raw["angular_display_cap"], "capacity_display_cap_invalid")
        tiers = raw["test_tiers"]
        if legacy_cap != _LEGACY_PARTICIPANT_CAP or hard_cap != _ADMINISTRATIVE_LIVEKIT_HARD_CAP:
            raise SfuBroadcastCapacityProfileError("capacity_administrative_cap_drift")
        if not isinstance(tiers, list) or tuple(tiers) != _CAPACITY_TEST_TIERS:
            raise SfuBroadcastCapacityProfileError("capacity_test_tiers_invalid")
        if not display_cap <= room_cap <= active_cap <= hard_cap:
            raise SfuBroadcastCapacityProfileError("capacity_cap_order_invalid")
        activation = self._activation(raw["activation"], revision, legacy_cap, active_cap, tiers)
        deployed_hard_cap = _livekit_room_hard_cap(self._livekit_config_path)
        if deployed_hard_cap != hard_cap:
            raise SfuBroadcastCapacityProfileError("capacity_livekit_config_drift")
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return ResolvedSfuBroadcastCapacityProfile(
            profile_id=profile_id,
            schema_version=1,
            profile_revision=revision,
            profile_digest=hashlib.sha256(canonical).hexdigest(),
            livekit_hard_cap=hard_cap,
            active_hub_room_cap=active_cap,
            room_admission_cap=room_cap,
            angular_display_cap=display_cap,
            activation_state=activation["state"],
            gate_id=activation["gate_id"],
            gate_passed=activation["gate_passed"],
            approval_id=activation["approval_id"],
        )

    @staticmethod
    def _activation(
        value: object,
        revision: int,
        legacy_cap: int,
        active_cap: int,
        tiers: list[object],
    ) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _ACTIVATION_KEYS:
            raise SfuBroadcastCapacityProfileError("capacity_activation_fields_invalid")
        state = value["state"]
        gate_passed = value["gate_passed"]
        candidate = value["candidate_cap"]
        approval_id = value["approval_id"]
        expected_previous = value["expected_previous_revision"]
        approved_revision = value["approved_revision"]
        if value["gate_id"] != _CAPACITY_GATE_ID or type(gate_passed) is not bool:
            raise SfuBroadcastCapacityProfileError("capacity_gate_invalid")
        if state == "legacy":
            if gate_passed or active_cap != legacy_cap or any(
                item is not None for item in (candidate, approval_id, expected_previous, approved_revision)
            ):
                raise SfuBroadcastCapacityProfileError("capacity_legacy_activation_invalid")
        elif state == "candidate":
            if (
                gate_passed is not True
                or candidate not in tiers
                or not _is_integer(candidate)
                or candidate <= legacy_cap
                or active_cap != candidate
                or not isinstance(approval_id, str)
                or not _IDENTIFIER.fullmatch(approval_id)
                or expected_previous != revision - 1
                or approved_revision != revision
            ):
                raise SfuBroadcastCapacityProfileError("capacity_candidate_approval_invalid")
        elif state == "rollback":
            if (
                gate_passed is not True
                or active_cap != legacy_cap
                or candidate not in tiers
                or not _is_integer(candidate)
                or candidate <= active_cap
                or not isinstance(approval_id, str)
                or not _IDENTIFIER.fullmatch(approval_id)
                or expected_previous != revision - 1
                or not _is_integer(approved_revision)
                or not 1 <= approved_revision < revision
            ):
                raise SfuBroadcastCapacityProfileError("capacity_rollback_invalid")
        else:
            raise SfuBroadcastCapacityProfileError("capacity_activation_state_invalid")
        return value


def _livekit_room_hard_cap(path: Path) -> int:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SfuBroadcastCapacityProfileError("capacity_livekit_config_unavailable") from exc
    in_room = False
    values: list[int] = []
    for raw_line in lines:
        without_comment = raw_line.split("#", 1)[0].rstrip()
        if not without_comment.strip():
            continue
        indentation = len(without_comment) - len(without_comment.lstrip())
        stripped = without_comment.strip()
        if indentation == 0:
            in_room = stripped == "room:"
            continue
        if in_room and stripped.startswith("max_participants:"):
            raw_value = stripped.split(":", 1)[1].strip()
            if not raw_value.isdigit():
                raise SfuBroadcastCapacityProfileError("capacity_livekit_config_invalid")
            values.append(int(raw_value))
    if len(values) != 1 or values[0] < 1:
        raise SfuBroadcastCapacityProfileError("capacity_livekit_config_invalid")
    return values[0]


def _is_integer(value: object) -> bool:
    return type(value) is int


def _positive_integer(value: object, reason: str) -> int:
    if not _is_integer(value) or value < 1:
        raise SfuBroadcastCapacityProfileError(reason)
    return value


_RESOLVER: SfuBroadcastCapacityProfileResolver | None = None
_RESOLVER_LOCK = threading.Lock()


def get_sfu_broadcast_capacity_profile_resolver() -> SfuBroadcastCapacityProfileResolver:
    global _RESOLVER
    if _RESOLVER is None:
        with _RESOLVER_LOCK:
            if _RESOLVER is None:
                resolver = SfuBroadcastCapacityProfileResolver()
                resolver.resolve()
                _RESOLVER = resolver
    return _RESOLVER


__all__ = [
    "ResolvedSfuBroadcastCapacityProfile",
    "SfuBroadcastCapacityProfileError",
    "SfuBroadcastCapacityProfilePort",
    "SfuBroadcastCapacityProfileResolver",
    "get_sfu_broadcast_capacity_profile_resolver",
]
