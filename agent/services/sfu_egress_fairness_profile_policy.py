"""Signed, fail-closed Hub policy for SFU egress fairness constraints."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _ROOT / "config" / "sfu_broadcast_fairness_profiles.json"
_ENFORCEMENT = frozenset({"browser_enforced", "runtime_enforced", "observation_only"})
_RUNTIME_RULE_CAPABILITY = {
    "egress_limits": "runtime_egress_scheduler",
    "max_starvation_window_ms": "runtime_starvation_window",
    "min_jain_fairness_index_basis_points": "runtime_fairness_index",
}


class SfuFairnessSignaturePort(Protocol):
    def verify(self, document: Mapping[str, object], signature: Mapping[str, object]) -> bool: ...


@dataclass(frozen=True, slots=True)
class SfuFairnessScope:
    tenant_ref: str
    room_ref: str
    route_epoch: int
    topology_epoch: int


@dataclass(frozen=True, slots=True)
class SfuFairnessRuntimeCapabilities:
    available: frozenset[str] = frozenset()

    def supports(self, capability: str) -> bool:
        return capability in self.available


@dataclass(frozen=True, slots=True)
class SfuFairnessWeights:
    receiver: int
    room: int
    tenant: int


@dataclass(frozen=True, slots=True)
class SfuFairnessHardLimits:
    receiver_egress_bps_max: int
    room_egress_bps_max: int
    tenant_egress_bps_max: int
    queue_bytes_max: int
    active_receivers_max: int


@dataclass(frozen=True, slots=True)
class SfuFairnessStages:
    downshift_utilization_basis_points: int
    disconnect_utilization_basis_points: int
    disconnect_grace_ms: int
    lowest_safe_spatial_layer: int


@dataclass(frozen=True, slots=True)
class SfuEgressFairnessDecision:
    accepted: bool
    reason_code: str
    profile_id: str
    profile_version: int
    scope: SfuFairnessScope
    weights: SfuFairnessWeights
    hard_limits: SfuFairnessHardLimits
    stages: SfuFairnessStages
    enforcement: Mapping[str, str]
    max_starvation_window_ms: int
    min_jain_fairness_index_basis_points: int
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class SfuAggregateBackpressureSample:
    node_egress_bps: int
    publication_egress_bps: int
    active_receivers: int
    sample_count: int
    current_spatial_layer_cap: int


@dataclass(frozen=True, slots=True)
class SfuPublisherBackpressureDecision:
    spatial_layer_cap: int
    reason_code: str
    admission_allowed: bool


class SfuEgressFairnessProfilePolicy:
    """Validates constraints only; intentionally exposes no execution hook."""

    def __init__(self, signatures: SfuFairnessSignaturePort, *,
                 profile_path: str | Path = _DEFAULT_PATH,
                 clock: Callable[[], float] = time.time) -> None:
        self._signatures = signatures
        self._clock = clock
        self._catalog = _load_catalog(Path(profile_path))

    def resolve(self, raw_document: bytes | str, *, scope: SfuFairnessScope,
                capabilities: SfuFairnessRuntimeCapabilities,
                parent_egress_bps_max: int,
                parent_receiver_cap: int) -> SfuEgressFairnessDecision:
        fallback = self._fallback(scope, "sfu_fairness_profile_unknown", parent_egress_bps_max, parent_receiver_cap)
        raw = raw_document.encode() if isinstance(raw_document, str) else raw_document
        if len(raw) > 8192:
            return self._replace_reason(fallback, "sfu_fairness_profile_bytes_exceeded")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._replace_reason(fallback, "sfu_fairness_profile_json_invalid")
        if not isinstance(document, dict) or set(document) != {
            "schema", "schema_version", "profile_id", "profile_version", "tenant_ref", "room_ref",
            "route_epoch", "topology_epoch", "issued_at_ms", "expires_at_ms", "weights",
            "hard_limits", "stages", "rules", "signature",
        }:
            return self._replace_reason(fallback, "sfu_fairness_profile_schema_invalid")
        unsigned = dict(document)
        signature = unsigned.pop("signature")
        if not isinstance(signature, dict) or not self._signatures.verify(unsigned, signature):
            return self._replace_reason(fallback, "sfu_fairness_profile_signature_invalid")
        now_ms = int(self._clock() * 1000)
        if document.get("schema") != "ananta.sfu-broadcast-fairness-profile.v1" or document.get("schema_version") != 1:
            return self._replace_reason(fallback, "sfu_fairness_profile_schema_invalid")
        if (
            document.get("tenant_ref") != scope.tenant_ref
            or document.get("room_ref") != scope.room_ref
            or document.get("route_epoch") != scope.route_epoch
            or document.get("topology_epoch") != scope.topology_epoch
        ):
            return self._replace_reason(fallback, "sfu_fairness_profile_scope_stale")
        issued = _integer(document.get("issued_at_ms"))
        expires = _integer(document.get("expires_at_ms"))
        if issued is None or expires is None or issued > now_ms + 5000 or expires <= now_ms \
                or expires - issued > int(self._catalog["ttl_seconds_max"]) * 1000:
            return self._replace_reason(fallback, "sfu_fairness_profile_stale")
        try:
            weights = _weights(document["weights"])
            limits = _limits(document["hard_limits"])
            stages = _stages(document["stages"])
            rules, enforcement = _rules(document["rules"])
            version = _required_positive(document.get("profile_version"))
        except (KeyError, TypeError, ValueError):
            return self._replace_reason(fallback, "sfu_fairness_profile_bounds_invalid")
        if not self._monotone(weights, limits, stages, parent_egress_bps_max, parent_receiver_cap):
            return self._replace_reason(fallback, "sfu_fairness_profile_widening_denied")
        for name, mode in enforcement.items():
            if mode == "runtime_enforced":
                capability = _RUNTIME_RULE_CAPABILITY.get(name)
                if capability is None or not capabilities.supports(capability):
                    return self._replace_reason(fallback, "sfu_fairness_runtime_capability_unsupported")
            if name in {"max_starvation_window_ms", "min_jain_fairness_index_basis_points"} \
                    and mode == "browser_enforced":
                return self._replace_reason(fallback, "sfu_fairness_enforcement_invalid")
        return SfuEgressFairnessDecision(
            True, "sfu_fairness_profile_accepted", str(document["profile_id"]), version,
            scope, weights, limits, stages, enforcement,
            rules["max_starvation_window_ms"],
            rules["min_jain_fairness_index_basis_points"], expires,
        )

    def derive_publisher_backpressure(
        self, profile: SfuEgressFairnessDecision,
        sample: SfuAggregateBackpressureSample,
    ) -> SfuPublisherBackpressureDecision:
        if sample.sample_count < 2 or sample.active_receivers < 1:
            return SfuPublisherBackpressureDecision(
                min(sample.current_spatial_layer_cap, profile.stages.lowest_safe_spatial_layer),
                "sfu_fairness_aggregate_unknown", False,
            )
        limit = min(profile.hard_limits.room_egress_bps_max, profile.hard_limits.tenant_egress_bps_max)
        utilization = max(sample.node_egress_bps, sample.publication_egress_bps) * 10_000 // max(1, limit)
        if utilization >= profile.stages.disconnect_utilization_basis_points:
            return SfuPublisherBackpressureDecision(
                profile.stages.lowest_safe_spatial_layer, "sfu_fairness_admission_denied", False,
            )
        if utilization >= profile.stages.downshift_utilization_basis_points:
            return SfuPublisherBackpressureDecision(
                min(sample.current_spatial_layer_cap, profile.stages.lowest_safe_spatial_layer),
                "sfu_fairness_layer_cap_lowered", True,
            )
        return SfuPublisherBackpressureDecision(
            sample.current_spatial_layer_cap, "sfu_fairness_within_corridor", True,
        )

    def _monotone(self, weights: SfuFairnessWeights, limits: SfuFairnessHardLimits,
                  stages: SfuFairnessStages, parent_egress: int, parent_receivers: int) -> bool:
        ceilings = self._catalog["weight_ceilings"]
        hard = self._catalog["hard_limits"]
        return (
            weights.receiver <= ceilings["receiver"] and weights.room <= ceilings["room"]
            and weights.tenant <= ceilings["tenant"]
            and limits.receiver_egress_bps_max <= hard["receiver_egress_bps_max"]
            and limits.room_egress_bps_max <= min(hard["room_egress_bps_max"], parent_egress)
            and limits.tenant_egress_bps_max <= hard["tenant_egress_bps_max"]
            and limits.queue_bytes_max <= hard["queue_bytes_max"]
            and limits.active_receivers_max <= min(hard["active_receivers_max"], parent_receivers)
            and stages.downshift_utilization_basis_points <= stages.disconnect_utilization_basis_points
            and stages.lowest_safe_spatial_layer == 0
        )

    def _fallback(self, scope: SfuFairnessScope, reason: str,
                  parent_egress: int, parent_receivers: int) -> SfuEgressFairnessDecision:
        hard = self._catalog["hard_limits"]
        stages = self._catalog["stages"]
        return SfuEgressFairnessDecision(
            False, reason, "strict-default", 0, scope, SfuFairnessWeights(1, 1, 1),
            SfuFairnessHardLimits(
                min(hard["receiver_egress_bps_max"], parent_egress),
                min(hard["room_egress_bps_max"], parent_egress),
                min(hard["tenant_egress_bps_max"], parent_egress),
                hard["queue_bytes_max"], min(hard["active_receivers_max"], parent_receivers),
            ),
            SfuFairnessStages(
                stages["downshift_utilization_basis_points"],
                stages["disconnect_utilization_basis_points"],
                stages["disconnect_grace_ms"], 0,
            ),
            {"queue_limits": "browser_enforced", "egress_limits": "observation_only",
             "max_starvation_window_ms": "observation_only",
             "min_jain_fairness_index_basis_points": "observation_only"},
            self._catalog["rules"]["max_starvation_window_ms"],
            self._catalog["rules"]["min_jain_fairness_index_basis_points"], 0,
        )

    @staticmethod
    def _replace_reason(value: SfuEgressFairnessDecision, reason: str) -> SfuEgressFairnessDecision:
        from dataclasses import replace
        return replace(value, reason_code=reason)


def _load_catalog(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("sfu_fairness_catalog_unavailable") from exc
    if not isinstance(value, dict) or value.get("schema") != "ananta.sfu-broadcast-fairness-profile-catalog.v1":
        raise RuntimeError("sfu_fairness_catalog_invalid")
    return value


def _weights(raw: object) -> SfuFairnessWeights:
    if not isinstance(raw, dict) or set(raw) != {"receiver", "room", "tenant"}:
        raise ValueError
    return SfuFairnessWeights(*(_required_positive(raw[key]) for key in ("receiver", "room", "tenant")))


def _limits(raw: object) -> SfuFairnessHardLimits:
    keys = ("receiver_egress_bps_max", "room_egress_bps_max", "tenant_egress_bps_max", "queue_bytes_max", "active_receivers_max")
    if not isinstance(raw, dict) or set(raw) != set(keys):
        raise ValueError
    return SfuFairnessHardLimits(*(_required_positive(raw[key]) for key in keys))


def _stages(raw: object) -> SfuFairnessStages:
    keys = ("downshift_utilization_basis_points", "disconnect_utilization_basis_points", "disconnect_grace_ms", "lowest_safe_spatial_layer")
    if not isinstance(raw, dict) or set(raw) != set(keys):
        raise ValueError
    values = tuple(_integer(raw[key]) for key in keys)
    if any(value is None for value in values) or not (0 <= values[0] <= 10_000 and 0 <= values[1] <= 10_000 \
            and 1000 <= values[2] <= 60_000 and 0 <= values[3] <= 3):
        raise ValueError
    return SfuFairnessStages(*values)


def _rules(raw: object) -> tuple[dict[str, int], dict[str, str]]:
    names = {"queue_limits", "egress_limits", "max_starvation_window_ms", "min_jain_fairness_index_basis_points"}
    if not isinstance(raw, dict) or set(raw) != names:
        raise ValueError
    values: dict[str, int] = {}
    enforcement: dict[str, str] = {}
    for name in names:
        rule = raw[name]
        if not isinstance(rule, dict) or set(rule) != {"value", "enforcement"} \
                or _integer(rule["value"]) is None or rule["enforcement"] not in _ENFORCEMENT:
            raise ValueError
        values[name] = int(rule["value"])
        enforcement[name] = str(rule["enforcement"])
    if not 250 <= values["max_starvation_window_ms"] <= 30_000 \
            or not 0 <= values["min_jain_fairness_index_basis_points"] <= 10_000:
        raise ValueError
    if values["queue_limits"] > 16_777_216 or values["egress_limits"] > 1_000_000_000:
        raise ValueError
    return values, enforcement


def _integer(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _required_positive(value: object) -> int:
    result = _integer(value)
    if result is None or result < 1:
        raise ValueError
    return result


__all__ = [
    "SfuAggregateBackpressureSample", "SfuEgressFairnessDecision",
    "SfuEgressFairnessProfilePolicy", "SfuFairnessRuntimeCapabilities",
    "SfuFairnessScope", "SfuFairnessSignaturePort", "SfuPublisherBackpressureDecision",
]
