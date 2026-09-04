"""Explicit composition profiles; optional adapters never enter native core imports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CollaborationDeploymentProfile:
    name: str
    durable_adapter: str
    live_adapter: str
    bridge_adapter: str
    multi_hub: bool
    state: str
    reason_code: str
    dependencies: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()
    configured_safety_caps: tuple[tuple[str, int], ...] = ()
    capacity_evidence: str = "unverified"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["dependencies"] = list(self.dependencies)
        value["secret_refs"] = list(self.secret_refs)
        value["configured_safety_caps"] = dict(self.configured_safety_caps)
        return value


PROFILES = {
    "local": CollaborationDeploymentProfile(
        "local",
        "sqlite",
        "hub_relay",
        "disabled",
        False,
        "ready",
        "local_standalone_ready",
        dependencies=("hub", "sqlite"),
        configured_safety_caps=(("event_payload_bytes", 65_536), ("live_queue_items", 64)),
        capacity_evidence="local_technical_observation",
    ),
    "single_hub": CollaborationDeploymentProfile(
        "single_hub",
        "sqlite",
        "hub_relay",
        "disabled",
        False,
        "ready",
        "single_hub_ready",
        dependencies=("hub", "sqlite"),
        configured_safety_caps=(("event_payload_bytes", 65_536), ("live_queue_items", 64)),
    ),
    "multi_hub": CollaborationDeploymentProfile(
        "multi_hub",
        "shared_cas_required",
        "shared_relay_required",
        "disabled",
        True,
        "unverified",
        "multi_hub_split_brain_evidence_required",
        dependencies=("shared_cas", "shared_outbox", "shared_presence", "shared_cache"),
        secret_refs=("database_credentials_ref", "relay_credentials_ref"),
    ),
    "sfu_enabled": CollaborationDeploymentProfile(
        "sfu_enabled",
        "shared_cas_required",
        "sfu",
        "disabled",
        True,
        "unverified",
        "sfu_runtime_evidence_required",
        dependencies=("shared_cas", "sfu", "turn", "shared_presence"),
        secret_refs=("sfu_credentials_ref", "turn_credentials_ref"),
    ),
    "buzz_enabled": CollaborationDeploymentProfile(
        "buzz_enabled",
        "shared_cas_required",
        "shared_relay_required",
        "buzz",
        True,
        "unverified",
        "buzz_runtime_evidence_required",
        dependencies=("shared_cas", "shared_outbox", "buzz_relay"),
        secret_refs=("buzz_signing_key_ref", "buzz_auth_ref"),
    ),
}


def deployment_profile(name: str) -> CollaborationDeploymentProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError("collaboration_deployment_profile_unknown") from exc


__all__ = ["CollaborationDeploymentProfile", "PROFILES", "deployment_profile"]
