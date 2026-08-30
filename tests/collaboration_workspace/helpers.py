from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_service import CollaborationWorkspaceService, build_event
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore


def actor(
    actor_id: str = "human-user-a",
    *,
    kind: str = "human",
    authority: str = "oidc",
    subject: str = "user-a",
) -> dict[str, Any]:
    return {
        "schema": "ananta.collaboration-actor-binding.v1",
        "actor_binding_id": actor_id,
        "actor_kind": kind,
        "authority_kind": authority,
        "authority_subject": subject,
        "display_name": actor_id,
        "capabilities": [],
    }


def room(room_id: str = "room-main") -> dict[str, Any]:
    return {
        "schema": "ananta.collaboration-room.v1",
        "room_id": room_id,
        "room_kind": "project",
        "title": "Main Room",
        "binding_kind": "project",
        "binding_id": "project-a",
    }


def service(path: Path) -> CollaborationWorkspaceService:
    return CollaborationWorkspaceService(CollaborationWorkspaceStore(path), policy=CollaborationWorkspacePolicy())


__all__ = ["actor", "build_event", "room", "service"]
