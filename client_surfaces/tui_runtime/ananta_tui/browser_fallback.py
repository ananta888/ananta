from __future__ import annotations

from typing import Any

from client_surfaces.tui_runtime.ananta_tui.state import TuiViewState

_BROWSER_PATHS = {
    "goal": "/goals/{id}",
    "task": "/tasks/{id}",
    "artifact": "/artifacts/{id}",
    "approval": "/approvals/{id}",
    "repair": "/repairs/{id}",
    "config": "/config",
    "audit": "/audit",
}


def build_object_browser_url(base_url: str, object_kind: str, object_id: str | None = None) -> str | None:
    template = _BROWSER_PATHS.get(str(object_kind or "").strip().lower())
    if not template:
        return None
    if "{id}" in template and not object_id:
        return None
    normalized_base = base_url.rstrip("/")
    path = template.format(id=object_id or "")
    return f"{normalized_base}{path}"


def build_browser_fallback_snapshot(base_url: str, state: TuiViewState) -> dict[str, Any]:
    links = {
        "selected_goal": build_object_browser_url(base_url, "goal", state.selected_goal_id),
        "selected_task": build_object_browser_url(base_url, "task", state.selected_task_id),
        "selected_artifact": build_object_browser_url(base_url, "artifact", state.selected_artifact_id),
        "selected_approval": build_object_browser_url(base_url, "approval", state.selected_approval_id),
        "selected_repair": build_object_browser_url(base_url, "repair", state.selected_repair_session_id),
        "config": build_object_browser_url(base_url, "config"),
        "audit": build_object_browser_url(base_url, "audit"),
    }
    return {
        "schema": "tui_browser_fallback_snapshot_v1",
        "browser_first_operations": [
            "deep_admin_configuration",
            "risky_bulk_cleanup",
            "complex_repair_session_drilldown",
            "rich_binary_artifact_rendering",
        ],
        "links": links,
    }
