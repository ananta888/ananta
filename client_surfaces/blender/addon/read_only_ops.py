from __future__ import annotations


def read_scene_summary(scene: dict) -> dict:
    return {
        "name": scene.get("name"),
        "object_count": int(scene.get("object_count", 0)),
        "selection_count": int(scene.get("selection_count", 0)),
        "mode": "read_only",
    }


def render_readiness(context: dict) -> dict:
    settings = dict((context or {}).get("render_settings") or {})
    return {
        "mode": "read_only",
        "ready": bool(settings.get("camera") or settings.get("active_camera")),
        "resolution": settings.get("resolution") or settings.get("resolution_x"),
        "warnings": [] if settings else ["render_settings_missing"],
    }


def export_readiness(context: dict) -> dict:
    objects = list((context or {}).get("objects") or [])
    selected = [item for item in objects if item.get("selected")]
    return {
        "mode": "read_only",
        "object_count": len(objects),
        "selected_count": len(selected),
        "ready": bool(objects),
        "warnings": [] if objects else ["no_exportable_objects"],
    }
