from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from client_surfaces.freecad.workbench.freecad_api import (
    capture_runtime_snapshot,
    extract_constraints,
    extract_document_payload,
    extract_object_payload,
    extract_selection_names,
)


def _safe_object_payload(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(raw.get("name") or ""),
        "type": str(raw.get("type") or "Unknown"),
        "visibility": bool(raw.get("visibility", True)),
        "volume": float(raw.get("volume") or 0.0),
    }


def capture_bounded_document_context(
    document: dict[str, Any] | None,
    objects: list[dict[str, Any]] | None,
    *,
    selection: list[str] | None = None,
    constraints: list[dict[str, Any]] | None = None,
    max_objects: int = 128,
    max_payload_bytes: int = 32768,
) -> dict[str, Any]:
    safe_document = dict(document or {})
    raw_objects = list(objects or [])
    bounded_objects = [_safe_object_payload(item) for item in raw_objects[: max(1, min(max_objects, 256))]]
    payload: dict[str, Any] = {
        "document": {
            "name": str(safe_document.get("name") or "Untitled"),
            "unit": str(safe_document.get("unit") or ""),
            "path": "",
        },
        "objects": bounded_objects,
        "selection": [str(item) for item in list(selection or [])[:32]],
        "constraints": [dict(item) for item in list(constraints or [])[:32]],
        "provenance": {
            "source": "freecad_workbench",
            "captured_at": datetime.now(UTC).isoformat(),
            "redaction": bool(safe_document.get("path")),
        },
        "limits": {
            "max_objects": max(1, min(max_objects, 256)),
            "max_payload_bytes": max(1024, max_payload_bytes),
        },
    }
    if safe_document.get("path"):
        payload["document"]["path"] = "redacted"

    serialized = json.dumps(payload, sort_keys=True).encode("utf-8")
    oversized = len(serialized) > max_payload_bytes
    if oversized and len(payload["objects"]) > 1:
        allowed = max(1, len(payload["objects"]) // 2)
        payload["objects"] = payload["objects"][:allowed]
        payload.setdefault("provenance", {})["redaction"] = True
        payload.setdefault("limits", {})["truncated"] = True
        payload.setdefault("limits", {})["object_count_before_truncation"] = len(raw_objects)
    return payload


def capture_context_from_freecad_document(
    document: Any,
    *,
    selection_objects: list[Any] | None = None,
    max_objects: int = 128,
    max_payload_bytes: int = 32768,
) -> dict[str, Any]:
    raw_objects = list(getattr(document, "Objects", None) or [])
    return capture_bounded_document_context(
        extract_document_payload(document),
        [extract_object_payload(item) for item in raw_objects],
        selection=extract_selection_names(selection_objects),
        constraints=extract_constraints(raw_objects),
        max_objects=max_objects,
        max_payload_bytes=max_payload_bytes,
    )


def capture_active_freecad_context(
    *,
    app_module: Any | None = None,
    gui_module: Any | None = None,
    max_objects: int = 128,
    max_payload_bytes: int = 32768,
) -> dict[str, Any]:
    if app_module is None:
        import FreeCAD as app_module  # type: ignore
    if gui_module is None:
        try:
            import FreeCADGui as gui_module  # type: ignore
        except ImportError:
            gui_module = None
    snapshot = capture_runtime_snapshot(app_module=app_module, gui_module=gui_module)
    return capture_bounded_document_context(
        snapshot["document"],
        snapshot["objects"],
        selection=snapshot["selection"],
        constraints=snapshot["constraints"],
        max_objects=max_objects,
        max_payload_bytes=max_payload_bytes,
    )
