"""Hub-only composition root for the default-off native collaboration core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.collaboration_bridge_ports import DisabledCollaborationBridge
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_service import CollaborationWorkspaceService
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore


@dataclass(frozen=True, slots=True)
class CollaborationWorkspaceWiringStatus:
    ready: bool
    reason_code: str | None


def initialize_collaboration_workspace(app: Flask) -> CollaborationWorkspaceWiringStatus:
    enabled = _bool(app.config.get("ANANTA_COLLABORATION_WORKSPACE_ENABLED", settings.collaboration_workspace_enabled))
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = CollaborationWorkspaceWiringStatus(False, "collaboration_hub_role_required")
    elif not enabled:
        status = CollaborationWorkspaceWiringStatus(False, "collaboration_workspace_disabled")
    else:
        path = Path(
            str(app.config.get("ANANTA_COLLABORATION_WORKSPACE_STATE") or settings.collaboration_workspace_state)
        )
        service = CollaborationWorkspaceService(
            CollaborationWorkspaceStore(path), policy=CollaborationWorkspacePolicy()
        )
        app.extensions["collaboration_workspace_service"] = service
        app.extensions["collaboration_bridge"] = DisabledCollaborationBridge()
        status = CollaborationWorkspaceWiringStatus(True, None)
    app.extensions["collaboration_workspace_wiring_status"] = status
    return status


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["CollaborationWorkspaceWiringStatus", "initialize_collaboration_workspace"]
