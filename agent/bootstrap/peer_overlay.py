"""Hub-only composition root for the decentralized peer data overlay."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.peer_overlay_control_service import PeerOverlayControlService
from agent.services.peer_overlay_state_store import PeerOverlayStateStore
from agent.services.peer_overlay_topology_service import PeerOverlayTopologyService


@dataclass(frozen=True, slots=True)
class PeerOverlayWiringStatus:
    ready: bool
    media_peer_dag: str
    reason_code: str | None


def initialize_peer_overlay(app: Flask) -> PeerOverlayWiringStatus:
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = PeerOverlayWiringStatus(False, "no_go", "peer_overlay_hub_role_required")
    else:
        try:
            state = PeerOverlayStateStore(
                Path(str(app.config.get("ANANTA_PEER_OVERLAY_STATE") or settings.peer_overlay_state))
            )
            key = hashlib.sha256(f"peer-overlay-control-v1:{app.secret_key}".encode()).digest()
            topology = PeerOverlayTopologyService(key, hub_key_id="ananta-hub-v1")
            control = PeerOverlayControlService(
                state,
                signing_key=key,
                hub_key_id="ananta-hub-v1",
                topology=topology,
                data_enabled=_configured_bool(
                    app.config.get("ANANTA_PEER_OVERLAY_DATA_ENABLED", settings.peer_overlay_data_enabled)
                ),
            )
        except (OSError, RuntimeError, ValueError):
            status = PeerOverlayWiringStatus(False, "no_go", "peer_overlay_configuration_invalid")
        else:
            app.extensions["peer_overlay_state_store"] = state
            app.extensions["peer_overlay_control_service"] = control
            status = PeerOverlayWiringStatus(True, "no_go", None)
    app.extensions["peer_overlay_wiring_status"] = status
    return status


def _configured_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["PeerOverlayWiringStatus", "initialize_peer_overlay"]
