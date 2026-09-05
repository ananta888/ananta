"""Hub-owned, independently scoped peer-overlay rollout policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ananta_contracts.peer_overlay import require_overlay_id

FEATURES = ("mesh", "data_overlay", "media_overlay", "native_sframe", "mls")
SCOPES = ("tenant", "room", "publication", "browser")
FORCED_NO_GO = {
    "media_overlay": "peer_overlay_cross_peer_media_standard_no_go",
    "native_sframe": "peer_overlay_native_sframe_unavailable",
    "mls": "peer_overlay_mls_rejected",
}


@dataclass(frozen=True, slots=True)
class PeerOverlayRolloutDecision:
    allowed: bool
    reason_code: str


class PeerOverlayRolloutPolicy:
    """Evaluates one feature without coupling sibling transport rollouts."""

    def __init__(
        self,
        *,
        enabled: Mapping[str, bool] | None = None,
        gate_bindings: Mapping[str, bool] | None = None,
        allowlists: Mapping[str, list[str] | tuple[str, ...]] | None = None,
    ) -> None:
        raw_enabled = dict(enabled or {})
        raw_gates = dict(gate_bindings or {})
        raw_allowlists = dict(allowlists or {})
        if (
            set(raw_enabled) - set(FEATURES)
            or set(raw_gates) - set(FEATURES)
            or set(raw_allowlists) - set(SCOPES)
            or any(not isinstance(value, bool) for value in (*raw_enabled.values(), *raw_gates.values()))
            or any(not isinstance(value, (list, tuple)) for value in raw_allowlists.values())
        ):
            raise ValueError("peer_overlay_rollout_fields_invalid")
        self._enabled = {feature: raw_enabled.get(feature) is True for feature in FEATURES}
        self._gates = {feature: raw_gates.get(feature) is True for feature in FEATURES}
        self._allowlists = {
            scope: frozenset(require_overlay_id(value, f"{scope}_allowlist") for value in raw_allowlists.get(scope, ()))
            for scope in SCOPES
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any] | None, *, legacy_data_enabled: bool = False
    ) -> PeerOverlayRolloutPolicy:
        payload = dict(value or {})
        if set(payload) - {"enabled", "gate_bindings", "allowlists"}:
            raise ValueError("peer_overlay_rollout_fields_invalid")
        raw_enabled = payload.get("enabled") or {}
        raw_gates = payload.get("gate_bindings") or {}
        raw_allowlists = payload.get("allowlists") or {}
        if not all(isinstance(item, Mapping) for item in (raw_enabled, raw_gates, raw_allowlists)):
            raise ValueError("peer_overlay_rollout_fields_invalid")
        enabled = dict(raw_enabled)
        gates = dict(raw_gates)
        if legacy_data_enabled and "data_overlay" not in enabled:
            enabled["data_overlay"] = True
            gates["data_overlay"] = True
        return cls(enabled=enabled, gate_bindings=gates, allowlists=dict(raw_allowlists))

    def evaluate(
        self,
        feature: str,
        *,
        tenant_id: str,
        room_id: str,
        publication_id: str,
        browser_id: str | None = None,
    ) -> PeerOverlayRolloutDecision:
        if feature not in FEATURES:
            raise ValueError("peer_overlay_rollout_feature_invalid")
        if not self._enabled[feature]:
            return PeerOverlayRolloutDecision(False, "peer_overlay_feature_disabled")
        if not self._gates[feature]:
            return PeerOverlayRolloutDecision(False, "peer_overlay_release_gate_incomplete")
        if feature in FORCED_NO_GO:
            return PeerOverlayRolloutDecision(False, FORCED_NO_GO[feature])
        values = {
            "tenant": require_overlay_id(tenant_id, "tenant_id"),
            "room": require_overlay_id(room_id, "room_id"),
            "publication": require_overlay_id(publication_id, "publication_id"),
            "browser": require_overlay_id(browser_id, "browser_id") if browser_id else None,
        }
        for scope, allowed in self._allowlists.items():
            if allowed and values[scope] not in allowed:
                return PeerOverlayRolloutDecision(False, f"peer_overlay_{scope}_canary_denied")
        return PeerOverlayRolloutDecision(True, "peer_overlay_canary_allowed")

    def matrix(self) -> dict[str, dict[str, bool]]:
        return {
            feature: {
                "enabled": self._enabled[feature],
                "gate_bound": self._gates[feature],
                "effective": self._enabled[feature] and self._gates[feature] and feature not in FORCED_NO_GO,
            }
            for feature in FEATURES
        }


__all__ = ["FEATURES", "FORCED_NO_GO", "PeerOverlayRolloutDecision", "PeerOverlayRolloutPolicy"]
