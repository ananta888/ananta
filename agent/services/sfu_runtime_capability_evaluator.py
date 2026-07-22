"""Versioned, fail-closed evaluation of non-authoritative runtime observations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping


class SfuRuntimeCapabilityError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class SfuRuntimeObservationTrust:
    transport_authenticated: bool
    signature_verified: bool


@dataclass(frozen=True, slots=True)
class SfuRuntimeCapabilityPolicy:
    producer_mode: str
    config_digest: str
    image_digest: str
    allowed_capabilities: frozenset[str]
    receiver_limit_max: int
    room_limit_max: int
    egress_bps_max: int
    memory_bytes_max: int
    required_capabilities: frozenset[str] = frozenset()
    observation_ttl_ms_max: int = 30_000
    clock_skew_ms: int = 5_000
    cpu_stop_ratio: float = 0.9
    memory_stop_ratio: float = 0.85
    fd_stop_ratio: float = 0.85
    port_stop_ratio: float = 0.8
    packet_drop_stop_ratio: float = 0.02

    def __post_init__(self) -> None:
        if self.producer_mode not in {"livekit_control_api", "authenticated_runtime_extension"}:
            raise ValueError("sfu_runtime_observation_mode_invalid")
        for digest in (self.config_digest, self.image_digest):
            if not digest.startswith("sha256:") or len(digest) != 71:
                raise ValueError("sfu_runtime_observation_policy_digest_invalid")
        for value in (
            self.receiver_limit_max,
            self.room_limit_max,
            self.egress_bps_max,
            self.memory_bytes_max,
            self.observation_ttl_ms_max,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("sfu_runtime_observation_policy_limit_invalid")
        for value in (
            self.cpu_stop_ratio,
            self.memory_stop_ratio,
            self.fd_stop_ratio,
            self.port_stop_ratio,
            self.packet_drop_stop_ratio,
        ):
            if not 0 < value <= 1:
                raise ValueError("sfu_runtime_observation_policy_pressure_invalid")
        if not self.required_capabilities <= self.allowed_capabilities:
            raise ValueError("sfu_runtime_observation_required_capability_unapproved")


@dataclass(frozen=True, slots=True)
class SfuRuntimeCapabilityEvaluation:
    status: str
    admission_allowed: bool
    reason_codes: tuple[str, ...]
    capabilities: Mapping[str, str]
    effective_receiver_limit: int
    effective_room_limit: int
    effective_egress_bps: int
    fresh_until_ms: int
    observed_node_id: str | None
    observed_node_authoritative: bool


class SfuRuntimeCapabilityEvaluator:
    """Intersects observed claims with Hub policy; observations never add rights."""

    _MODES = frozenset({"livekit_control_api", "authenticated_runtime_extension"})
    _TOP_FIELDS = frozenset(
        {
            "schema_version",
            "producer_mode",
            "scope",
            "producer_id",
            "producer_fencing_token",
            "boot_id",
            "sequence",
            "measured_at_ms",
            "valid_until_ms",
            "config_digest",
            "image_digest",
            "capability_digest",
            "capabilities",
            "health",
            "capacity",
            "pressure",
            "labels",
            "proof",
        }
    )

    def __init__(self, policy: SfuRuntimeCapabilityPolicy) -> None:
        if policy.producer_mode not in self._MODES:
            raise ValueError("sfu_runtime_observation_mode_invalid")
        self._policy = policy

    def evaluate(
        self,
        document: Mapping[str, object],
        trust: SfuRuntimeObservationTrust,
        *,
        now_ms: int,
    ) -> SfuRuntimeCapabilityEvaluation:
        self._validate_shape(document)
        reasons: list[str] = []
        mode = str(document["producer_mode"])
        if mode != self._policy.producer_mode:
            reasons.append("sfu_runtime_observation_mode_mismatch")
        if not trust.transport_authenticated:
            reasons.append("sfu_runtime_observation_transport_untrusted")
        if mode == "authenticated_runtime_extension":
            if not trust.signature_verified or document["proof"] is None:
                reasons.append("sfu_runtime_observation_signature_unverified")
        elif document["proof"] is not None:
            reasons.append("sfu_runtime_observation_signature_claim_invalid")

        measured_at = int(document["measured_at_ms"])
        declared_until = int(document["valid_until_ms"])
        fresh_until = min(declared_until, measured_at + self._policy.observation_ttl_ms_max)
        if measured_at > now_ms + self._policy.clock_skew_ms:
            reasons.append("sfu_runtime_observation_clock_skew")
        if now_ms >= fresh_until:
            reasons.append("sfu_runtime_observation_stale")
        if document["config_digest"] != self._policy.config_digest:
            reasons.append("sfu_runtime_observation_config_mismatch")
        if document["image_digest"] != self._policy.image_digest:
            reasons.append("sfu_runtime_observation_image_mismatch")

        capability_claims = document["capabilities"]
        canonical = json.dumps(
            capability_claims,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        expected_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
        if document["capability_digest"] != expected_digest:
            reasons.append("sfu_runtime_observation_capability_digest_mismatch")
        capabilities: dict[str, str] = {}
        for claim in capability_claims:
            name = str(claim["name"])
            state = str(claim["state"])
            if name not in self._policy.allowed_capabilities:
                capabilities[name] = "unknown"
                reasons.append("sfu_runtime_observation_capability_unapproved")
            else:
                capabilities[name] = state
        if any(capabilities.get(name) != "supported" for name in self._policy.required_capabilities):
            reasons.append("sfu_runtime_observation_required_capability_missing")

        health = document["health"]
        for field in ("liveness", "control_ready", "media_ready", "admission_ready"):
            if health.get(field) is not True:
                reasons.append(
                    "sfu_runtime_observation_health_unknown"
                    if health.get(field) is None
                    else "sfu_runtime_observation_health_failed"
                )
        pressure = document["pressure"]
        pressure_limits = {
            "cpu_ratio": self._policy.cpu_stop_ratio,
            "memory_ratio": self._policy.memory_stop_ratio,
            "fd_ratio": self._policy.fd_stop_ratio,
            "udp_port_ratio": self._policy.port_stop_ratio,
            "packet_drop_ratio": self._policy.packet_drop_stop_ratio,
        }
        for name, maximum in pressure_limits.items():
            value = pressure.get(name)
            if value is None:
                reasons.append("sfu_runtime_observation_pressure_unknown")
            elif float(value) >= maximum:
                reasons.append("sfu_runtime_observation_pressure_exceeded")

        capacity = document["capacity"]
        receiver_limit = self._bounded_cap(capacity.get("receiver_limit"), self._policy.receiver_limit_max)
        room_limit = self._bounded_cap(capacity.get("room_limit"), self._policy.room_limit_max)
        egress_limit = self._bounded_cap(capacity.get("egress_bps"), self._policy.egress_bps_max)
        if 0 in {receiver_limit, room_limit, egress_limit}:
            reasons.append("sfu_runtime_observation_capacity_unknown")
        memory = capacity.get("memory_bytes_limit")
        if memory is None or int(memory) <= 0 or int(memory) > self._policy.memory_bytes_max:
            reasons.append("sfu_runtime_observation_memory_limit_invalid")

        scope = document["scope"]
        observed_node = scope.get("observed_node_id")
        # LiveKit-native node binding is diagnostic only; neither mode grants
        # the observation authority to choose a Hub placement.
        observed_node_authoritative = False
        blocking = bool(reasons)
        if blocking:
            receiver_limit = room_limit = egress_limit = 0
            capabilities = {name: "unknown" for name in capabilities}
        return SfuRuntimeCapabilityEvaluation(
            status="healthy" if not blocking else "unknown",
            admission_allowed=not blocking,
            reason_codes=tuple(dict.fromkeys(reasons)),
            capabilities=capabilities,
            effective_receiver_limit=receiver_limit,
            effective_room_limit=room_limit,
            effective_egress_bps=egress_limit,
            fresh_until_ms=fresh_until,
            observed_node_id=None if observed_node is None else str(observed_node),
            observed_node_authoritative=observed_node_authoritative,
        )

    def _validate_shape(self, document: Mapping[str, object]) -> None:
        if set(document) != self._TOP_FIELDS:
            raise SfuRuntimeCapabilityError("sfu_runtime_observation_fields_invalid")
        if document["schema_version"] != "sfu_runtime_observation.v2":
            raise SfuRuntimeCapabilityError("sfu_runtime_observation_schema_unsupported")
        if document["producer_mode"] not in self._MODES:
            raise SfuRuntimeCapabilityError("sfu_runtime_observation_mode_invalid")
        for field in ("producer_fencing_token", "sequence", "measured_at_ms", "valid_until_ms"):
            value = document[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < (1 if field == "producer_fencing_token" else 0):
                raise SfuRuntimeCapabilityError("sfu_runtime_observation_number_invalid")
        for field in ("scope", "health", "capacity", "pressure", "labels"):
            if not isinstance(document[field], Mapping):
                raise SfuRuntimeCapabilityError("sfu_runtime_observation_object_invalid")
        scope = document["scope"]
        if set(scope) not in (
            {"tenant_id", "cluster_id", "region", "observed_node_id", "node_binding_authority"},
            {"tenant_id", "cluster_id", "region", "runtime_id", "observed_node_id", "node_binding_authority"},
        ) or scope.get("node_binding_authority") != "non_authoritative_observation":
            raise SfuRuntimeCapabilityError("sfu_runtime_observation_scope_invalid")
        claims = document["capabilities"]
        if not isinstance(claims, list) or len(claims) > 32:
            raise SfuRuntimeCapabilityError("sfu_runtime_observation_capabilities_invalid")
        for claim in claims:
            if not isinstance(claim, Mapping) or set(claim) != {"name", "state"}:
                raise SfuRuntimeCapabilityError("sfu_runtime_observation_capability_invalid")
            if claim["state"] not in {"supported", "unsupported", "unknown"}:
                raise SfuRuntimeCapabilityError("sfu_runtime_observation_capability_invalid")
        names = [str(claim["name"]) for claim in claims]
        if len(set(names)) != len(names):
            raise SfuRuntimeCapabilityError("sfu_runtime_observation_capability_duplicate")
        if len(document["labels"]) > 16:
            raise SfuRuntimeCapabilityError("sfu_runtime_observation_labels_exceeded")

    @staticmethod
    def _bounded_cap(value: object, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return min(value, maximum)


__all__ = [
    "SfuRuntimeCapabilityError",
    "SfuRuntimeCapabilityEvaluation",
    "SfuRuntimeCapabilityEvaluator",
    "SfuRuntimeCapabilityPolicy",
    "SfuRuntimeObservationTrust",
]
