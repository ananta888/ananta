from __future__ import annotations

from typing import Any

from client_surfaces.tui_runtime.ananta_tui.surface_map import build_hub_api_surface_map


def build_vscode_frontend_api_surface_map() -> dict[str, Any]:
    """Build the VS Code extension surface map from backend-reuse oriented API entries.

    The VS Code extension is intentionally backend-first: the surface map should
    reflect the reusable backend API contract rather than local model-specific
    behavior.
    """

    hub_surface_map = build_hub_api_surface_map()
    by_section: dict[str, list[dict[str, Any]]] = {}
    for section, entries in (hub_surface_map.get("by_section") or {}).items():
        transformed: list[dict[str, Any]] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            transformed.append(
                {
                    "section": section,
                    "method": entry.get("method"),
                    "http_method": entry.get("http_method"),
                    "endpoint": entry.get("endpoint"),
                    "classification": entry.get("classification"),
                    "source": "backend_api",
                    "notes": entry.get("notes"),
                    "derived_from": "tui_frontend_api_surface_map_v1",
                }
            )
        if transformed:
            by_section[str(section)] = transformed

    return {
        "schema": "vscode_frontend_api_surface_map_v1",
        "sections": list(hub_surface_map.get("sections") or []),
        "by_section": by_section,
    }
