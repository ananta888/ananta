"""Independent fail-closed release lanes for native, live and bridge capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class CollaborationReleaseGate:
    NATIVE_REQUIRED = frozenset(
        {
            "contracts",
            "migrations",
            "authorization",
            "threads",
            "outbox_inbox",
            "projection_rebuild",
            "search_isolation",
            "headless_commands",
            "backup_restore",
            "standalone_without_bridge",
        }
    )
    LIVE_REQUIRED = frozenset(
        {"multi_receiver_authorization", "revocation", "backpressure", "reconnect", "e2ee", "runtime_evidence"}
    )
    BRIDGE_REQUIRED = frozenset(
        {"pinned_revision", "signature", "key_custody", "ingress", "delivery_replay", "runtime_evidence"}
    )

    def evaluate(
        self,
        *,
        native: Mapping[str, bool],
        live: Mapping[str, bool],
        bridge: Mapping[str, bool],
        deployment_profile: str,
    ) -> dict[str, Any]:
        if deployment_profile not in {"local", "single_hub", "multi_hub"}:
            raise ValueError("collaboration_deployment_profile_invalid")
        native_lane = self._lane(native, self.NATIVE_REQUIRED, "native")
        if deployment_profile == "multi_hub":
            native_lane = self._blocked(native_lane, "native_multi_hub_store_unverified")
        live_lane = self._lane(live, self.LIVE_REQUIRED, "live")
        bridge_lane = self._lane(bridge, self.BRIDGE_REQUIRED, "bridge")
        return {
            "schema": "ananta.collaboration-release-gate.v1",
            "deployment_profile": deployment_profile,
            "lanes": {"native": native_lane, "live": live_lane, "bridge": bridge_lane},
            "native_core_available": native_lane["state"] == "passed",
            "human_intervention_required": False,
        }

    @staticmethod
    def _lane(values: Mapping[str, bool], required: frozenset[str], name: str) -> dict[str, Any]:
        unknown = set(values) - required
        if unknown:
            raise ValueError(f"collaboration_{name}_gate_fields_invalid")
        missing = sorted(key for key in required if values.get(key) is not True)
        return {
            "state": "passed" if not missing else "unverified",
            "missing": missing,
            "reason_code": f"{name}_gate_passed" if not missing else f"{name}_evidence_incomplete",
        }

    @staticmethod
    def _blocked(lane: Mapping[str, Any], reason_code: str) -> dict[str, Any]:
        return {**dict(lane), "state": "unverified", "reason_code": reason_code}


__all__ = ["CollaborationReleaseGate"]
