"""Deterministic Hub policy for ordinary/SFU media topology transitions.

The policy is intentionally pure.  It decides which already-authorized media
path may be used, while browser adapters execute the transition and the Hub's
SFU admission service remains the authority for room and publication rights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent.services.sfu_broadcast_participant_limits import SFU_BROADCAST_MAX_ROOM_PARTICIPANTS

MediaTopology = Literal[
    "ordinary_direct",
    "ordinary_mesh",
    "ordinary_sfu",
    "semantic_sfu",
    "relay_control_only",
]

_BULK_TOPOLOGIES = frozenset({"ordinary_direct", "ordinary_mesh", "ordinary_sfu", "semantic_sfu"})


@dataclass(frozen=True, slots=True)
class MediaTopologyContext:
    current: MediaTopology
    participant_count: int
    now_ms: int
    last_transition_ms: int
    ordinary_direct_healthy: bool
    ordinary_sfu_healthy: bool
    sfu_enabled: bool
    sfu_admitted: bool
    sfu_e2ee_ready: bool
    semantic_contract_active: bool
    semantic_quality_healthy: bool
    relay_control_available: bool
    strict_e2ee: bool = True
    user_override: Literal["auto", "ordinary", "direct", "sfu"] = "auto"
    feature_killed: bool = False


@dataclass(frozen=True, slots=True)
class MediaTopologyDecision:
    target: MediaTopology
    reason_code: str
    changed: bool
    retry_after_ms: int
    bulk_path_count: int = 1


class MediaTopologyPolicy:
    """Select one bulk media path with explicit cooldown and safe fallback."""

    def __init__(
        self,
        *,
        cooldown_ms: int = 5_000,
        semantic_stability_ms: int = 10_000,
        mesh_participant_limit: int = 3,
    ) -> None:
        if cooldown_ms < 0 or semantic_stability_ms < cooldown_ms:
            raise ValueError("media_topology_timing_invalid")
        if not 2 <= mesh_participant_limit <= 4:
            raise ValueError("media_topology_mesh_limit_invalid")
        self._cooldown_ms = cooldown_ms
        self._semantic_stability_ms = semantic_stability_ms
        self._mesh_limit = mesh_participant_limit

    def decide(self, context: MediaTopologyContext) -> MediaTopologyDecision:
        self._validate(context)
        desired, reason = self._desired(context)
        if desired == context.current:
            return MediaTopologyDecision(desired, reason, False, 0)

        elapsed = context.now_ms - context.last_transition_ms
        required = self._semantic_stability_ms if desired == "semantic_sfu" else self._cooldown_ms
        # Safety transitions never wait for hysteresis.  This includes an E2EE
        # failure, feature kill, explicit ordinary override and complete loss
        # of the current bulk route.
        safety_transition = (
            context.feature_killed
            or context.user_override in {"ordinary", "direct"}
            or (context.current == "semantic_sfu" and not context.semantic_quality_healthy)
            or (context.current in {"ordinary_sfu", "semantic_sfu"} and not context.ordinary_sfu_healthy)
            or (
                context.strict_e2ee
                and context.current in {"ordinary_sfu", "semantic_sfu"}
                and not context.sfu_e2ee_ready
            )
        )
        if not safety_transition and elapsed < required:
            return MediaTopologyDecision(
                context.current,
                "media_topology_cooldown_active",
                False,
                required - elapsed,
            )
        return MediaTopologyDecision(desired, reason, True, 0)

    def _desired(self, value: MediaTopologyContext) -> tuple[MediaTopology, str]:
        ordinary_fallback = self._ordinary_fallback(value)
        if value.feature_killed:
            return ordinary_fallback, "media_topology_feature_killed"
        if value.user_override == "direct":
            return self._direct_or_control(value), "media_topology_user_direct"
        if value.user_override == "ordinary":
            return ordinary_fallback, "media_topology_user_ordinary"

        sfu_ready = (
            value.sfu_enabled
            and value.sfu_admitted
            and value.ordinary_sfu_healthy
            and (value.sfu_e2ee_ready or not value.strict_e2ee)
        )
        if value.user_override == "sfu" and not sfu_ready:
            return ordinary_fallback, "media_topology_sfu_not_admitted"
        if sfu_ready and value.semantic_contract_active and value.semantic_quality_healthy:
            return "semantic_sfu", "media_topology_semantic_sfu_healthy"
        if sfu_ready and (value.user_override == "sfu" or value.participant_count > self._mesh_limit):
            return "ordinary_sfu", "media_topology_ordinary_sfu_healthy"
        return ordinary_fallback, "media_topology_ordinary_safe_default"

    def _ordinary_fallback(self, value: MediaTopologyContext) -> MediaTopology:
        if value.ordinary_direct_healthy:
            if value.participant_count <= 2:
                return "ordinary_direct"
            if value.participant_count <= self._mesh_limit:
                return "ordinary_mesh"
        if (
            value.sfu_enabled
            and value.sfu_admitted
            and value.ordinary_sfu_healthy
            and (value.sfu_e2ee_ready or not value.strict_e2ee)
        ):
            return "ordinary_sfu"
        # A healthy 1:1 connection is not a functional group route.  Above the
        # mesh limit, fail to the bounded control plane even when that plane is
        # currently unavailable; callers must never present a direct pair as
        # successfully carrying group media.
        return "relay_control_only"

    @staticmethod
    def _direct_or_control(value: MediaTopologyContext) -> MediaTopology:
        if value.ordinary_direct_healthy:
            return "ordinary_direct"
        return "relay_control_only" if value.relay_control_available else "ordinary_direct"

    @staticmethod
    def _validate(value: MediaTopologyContext) -> None:
        if value.current not in _BULK_TOPOLOGIES | {"relay_control_only"}:
            raise ValueError("media_topology_current_invalid")
        if not 1 <= value.participant_count <= SFU_BROADCAST_MAX_ROOM_PARTICIPANTS:
            raise ValueError("media_topology_participant_count_invalid")
        if value.now_ms < 0 or value.last_transition_ms < 0 or value.last_transition_ms > value.now_ms:
            raise ValueError("media_topology_clock_invalid")


__all__ = ["MediaTopologyContext", "MediaTopologyDecision", "MediaTopologyPolicy"]
