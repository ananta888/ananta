"""Minimal Blender addon bootstrap for the Ananta bridge surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from client_surfaces.blender.addon.health import build_runtime_state as build_health_runtime_state

bl_info = {
    "name": "Ananta Bridge",
    "author": "Ananta",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "3D View > Sidebar",
    "description": "Thin Blender addon surface for Ananta hub-governed bridge flows.",
    "category": "3D View",
}


REGISTERED_MODULES = (
    "settings",
    "operators",
    "panels",
)

_REGISTERED = False


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
    legacy_state = BlenderBridgeRuntimeState(
        session_id=str(session_id).strip(),
        connected=bool(connected),
        metadata=dict(metadata or {}),
    )
    runtime = build_health_runtime_state(
        connected=connected,
        capabilities=list((metadata or {}).get("capabilities") or []),
        problems=list((metadata or {}).get("problems") or []),
    )
    return {**legacy_state.as_dict(), **runtime}


def registered_modules() -> list[str]:
    return list(REGISTERED_MODULES) if _REGISTERED else []


def register() -> None:
    """Blender addon registration entrypoint."""
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        import bpy  # type: ignore
    except Exception:
        _REGISTERED = True
        return
    for cls in ():
        bpy.utils.register_class(cls)
    _REGISTERED = True


def unregister() -> None:
    """Blender addon unregistration entrypoint."""
    global _REGISTERED
    if not _REGISTERED:
        return
    try:
        import bpy  # type: ignore
    except Exception:
        _REGISTERED = False
        return
    for cls in reversed(()):
        bpy.utils.unregister_class(cls)
    _REGISTERED = False
