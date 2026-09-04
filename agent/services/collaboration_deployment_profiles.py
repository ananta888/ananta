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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PROFILES = {
    "local": CollaborationDeploymentProfile(
        "local", "sqlite", "hub_relay", "disabled", False, "ready", "local_standalone_ready"
    ),
    "single_hub": CollaborationDeploymentProfile(
        "single_hub", "sqlite", "hub_relay", "disabled", False, "ready", "single_hub_ready"
    ),
    "multi_hub": CollaborationDeploymentProfile(
        "multi_hub",
        "shared_cas_required",
        "shared_relay_required",
        "disabled",
        True,
        "unverified",
        "multi_hub_split_brain_evidence_required",
    ),
}


def deployment_profile(name: str) -> CollaborationDeploymentProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError("collaboration_deployment_profile_unknown") from exc


__all__ = ["CollaborationDeploymentProfile", "PROFILES", "deployment_profile"]
