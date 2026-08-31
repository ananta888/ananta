"""Application composition for the local runtime capability control plane."""

from __future__ import annotations

from dataclasses import dataclass

from flask import Flask

from agent.services.local_runtime_capability_composition import (
    local_runtime_capability_projection,
)
from agent.services.local_runtime_capability_task_dispatcher import (
    LocalRuntimeCapabilityRefreshDispatcher,
)


@dataclass(frozen=True, slots=True)
class LocalRuntimeCapabilityWiringStatus:
    ready: bool
    reason_code: str | None = None


def initialize_local_runtime_capability_services(app: Flask) -> LocalRuntimeCapabilityWiringStatus:
    app.extensions["local_runtime_capability_projection"] = local_runtime_capability_projection()
    role = str(app.config.get("ROLE") or "").strip().lower()
    if role != "hub":
        status = LocalRuntimeCapabilityWiringStatus(False, "local_runtime_capability_hub_role_required")
    else:
        app.extensions["local_runtime_capability_refresh_dispatch"] = (
            LocalRuntimeCapabilityRefreshDispatcher(
                provider_urls=dict(app.config.get("PROVIDER_URLS") or {}),
            )
        )
        status = LocalRuntimeCapabilityWiringStatus(True)
    app.extensions["local_runtime_capability_wiring_status"] = status
    return status


__all__ = [
    "LocalRuntimeCapabilityWiringStatus",
    "initialize_local_runtime_capability_services",
]
