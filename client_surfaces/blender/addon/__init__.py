"""Minimal Blender addon bootstrap for the Ananta bridge surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

bl_info = {
    "name": "Ananta Bridge",
    "author": "Ananta",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "3D View > Sidebar",
    "description": "Thin Blender addon surface for Ananta hub-governed bridge flows.",
    "category": "3D View",
}


@dataclass(frozen=True)
class BlenderBridgeRuntimeState:
    """Represents addon runtime state that can be exported to the bridge."""

    session_id: str
    connected: bool
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "connected": self.connected,
            "metadata": dict(self.metadata),
        }


def build_runtime_state(*, session_id: str, connected: bool, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    state = BlenderBridgeRuntimeState(
        session_id=str(session_id).strip(),
        connected=bool(connected),
        metadata=dict(metadata or {}),
    )
    return state.as_dict()


def register() -> None:
    """Blender addon registration entrypoint."""


def unregister() -> None:
    """Blender addon unregistration entrypoint."""
