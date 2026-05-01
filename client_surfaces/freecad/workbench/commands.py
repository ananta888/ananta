from __future__ import annotations

from typing import Any

from client_surfaces.freecad.workbench.client import FreecadHubClient
from client_surfaces.freecad.workbench.context import capture_active_freecad_context
from client_surfaces.freecad.workbench.context_preview import build_context_preview


GOAL_CAPABILITY_ID = "freecad.model.inspect"


def submit_freecad_goal(client: FreecadHubClient, *, goal: str, context: dict[str, Any]) -> dict[str, Any]:
    preview = build_context_preview(context)
    if preview["oversize"]:
        return {"status": "degraded", "reason": "context_oversize", "preview": preview}
    response = client.submit_goal(goal=goal, context=context, capability_id=GOAL_CAPABILITY_ID)
    return {"status": str(response.get("status") or "degraded"), "preview": preview, "response": response}


def capture_active_context_command(
    *,
    app_module: Any | None = None,
    gui_module: Any | None = None,
    max_objects: int = 128,
    max_payload_bytes: int = 32768,
) -> dict[str, Any]:
    context = capture_active_freecad_context(
        app_module=app_module,
        gui_module=gui_module,
        max_objects=max_objects,
        max_payload_bytes=max_payload_bytes,
    )
    return {"status": "accepted", "context": context, "preview": build_context_preview(context)}


def submit_active_document_goal(
    client: FreecadHubClient,
    *,
    goal: str,
    app_module: Any | None = None,
    gui_module: Any | None = None,
) -> dict[str, Any]:
    captured = capture_active_context_command(
        app_module=app_module,
        gui_module=gui_module,
        max_objects=client.settings.max_context_objects,
        max_payload_bytes=client.settings.max_payload_bytes,
    )
    if captured["status"] != "accepted":
        return captured
    return submit_freecad_goal(client, goal=goal, context=dict(captured["context"]))


def preview_active_export_plan(
    client: FreecadHubClient,
    *,
    fmt: str,
    target_path: str,
    app_module: Any | None = None,
    gui_module: Any | None = None,
) -> dict[str, Any]:
    captured = capture_active_context_command(
        app_module=app_module,
        gui_module=gui_module,
        max_objects=client.settings.max_context_objects,
        max_payload_bytes=client.settings.max_payload_bytes,
    )
    selection_only = bool(list(((captured.get("context") or {}).get("selection") or [])))
    response = client.request_export_plan(fmt=fmt, target_path=target_path, selection_only=selection_only)
    return {"status": str(response.get("status") or "degraded"), "preview": captured.get("preview"), "response": response}


def preview_active_macro_plan(
    client: FreecadHubClient,
    *,
    objective: str,
    app_module: Any | None = None,
    gui_module: Any | None = None,
) -> dict[str, Any]:
    captured = capture_active_context_command(
        app_module=app_module,
        gui_module=gui_module,
        max_objects=client.settings.max_context_objects,
        max_payload_bytes=client.settings.max_payload_bytes,
    )
    preview = dict(captured.get("preview") or {})
    response = client.request_macro_plan(objective=objective, context_summary=preview)
    return {"status": str(response.get("status") or "degraded"), "preview": preview, "response": response}
