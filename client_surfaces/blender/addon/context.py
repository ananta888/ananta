from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _bounded_text(value: Any, *, max_chars: int = 160) -> str:
    return str(value or "")[: max(1, max_chars)]


def _object_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _bounded_text(item.get("name")),
        "type": _bounded_text(item.get("type") or "UNKNOWN", max_chars=64),
        "selected": bool(item.get("selected")),
        "visible": bool(item.get("visible", item.get("visibility", True))),
        "collection": _bounded_text(item.get("collection"), max_chars=128) or None,
        "material_slots": [
            _bounded_text(value, max_chars=128)
            for value in list(item.get("material_slots") or item.get("materials") or [])[:8]
        ],
        "modifiers": [
            {
                "name": _bounded_text((mod or {}).get("name"), max_chars=128),
                "type": _bounded_text((mod or {}).get("type"), max_chars=64),
            }
            for mod in list(item.get("modifiers") or [])[:16]
            if isinstance(mod, dict)
        ],
    }


def capture_bounded_scene_context(
    scene_name: str,
    objects: list[dict[str, Any]],
    *,
    max_objects: int = 64,
    blender_version: str = "unknown",
    collections: list[dict[str, Any]] | None = None,
    materials: list[dict[str, Any]] | None = None,
    render_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    max_count = max(1, min(max_objects, 512))
    raw_objects = list(objects or [])
    bounded = [_object_summary(dict(o or {})) for o in raw_objects[:max_count]]
    selection = [str(o.get("name")) for o in bounded if bool(o.get("selected")) and str(o.get("name") or "").strip()]
    return {
        "schema": "blender_scene_context.v1",
        "domain_schema": "blender_context.v1",
        "scene_name": _bounded_text(scene_name),
        "blender_version": _bounded_text(blender_version, max_chars=64),
        "selection": selection,
        "scene": {"name": _bounded_text(scene_name)},
        "collections": [
            {
                "name": _bounded_text((item or {}).get("name"), max_chars=128),
                "object_count": int((item or {}).get("object_count") or 0),
            }
            for item in list(collections or [])[:64]
            if isinstance(item, dict)
        ],
        "objects": bounded,
        "materials": [
            {"name": _bounded_text((item or {}).get("name"), max_chars=128), "users": int((item or {}).get("users") or 0)}
            for item in list(materials or [])[:128]
            if isinstance(item, dict)
        ],
        "render_settings": dict(render_settings or {}),
        "provenance": {
            "capture": "addon",
            "bounded": True,
            "captured_at": datetime.now(UTC).isoformat(),
            "object_count_total": len(raw_objects),
            "object_count_included": len(bounded),
            "objects_clipped": len(raw_objects) > len(bounded),
        },
    }


def capture_context_from_bpy(bpy_module: Any, *, max_objects: int = 128) -> dict[str, Any]:
    context = getattr(bpy_module, "context", None)
    data = getattr(bpy_module, "data", None)
    scene = getattr(context, "scene", None)
    scene_name = str(getattr(scene, "name", "") or "Scene")
    selected_objects = list(getattr(context, "selected_objects", []) or [])
    objects: list[dict[str, Any]] = []
    for obj in list(getattr(data, "objects", []) or []):
        visible_get = getattr(obj, "visible_get", None)
        objects.append(
            {
                "name": getattr(obj, "name", ""),
                "type": getattr(obj, "type", "UNKNOWN"),
                "selected": any(obj is selected_obj or getattr(obj, "name", None) == getattr(selected_obj, "name", None) for selected_obj in selected_objects),
                "visible": bool(visible_get()) if callable(visible_get) else True,
                "materials": [getattr(slot, "name", "") for slot in list(getattr(obj, "material_slots", []) or [])],
                "modifiers": [
                    {"name": getattr(mod, "name", ""), "type": getattr(mod, "type", "")}
                    for mod in list(getattr(obj, "modifiers", []) or [])
                ],
            }
        )
    version = ".".join(str(part) for part in getattr(getattr(bpy_module, "app", None), "version", ()) or ("unknown",))
    return capture_bounded_scene_context(scene_name, objects, max_objects=max_objects, blender_version=version)
