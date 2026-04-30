from __future__ import annotations

from typing import Any


def capture_bounded_scene_context(scene_name: str, objects: list[dict[str, Any]], *, max_objects: int = 64) -> dict:
    bounded = list(objects or [])[:max(1, min(max_objects, 256))]
    return {
        "scene": {"name": scene_name},
        "objects": [{"name": o.get("name"), "type": o.get("type"), "selected": bool(o.get("selected"))} for o in bounded],
        "provenance": {"capture": "addon", "bounded": True},
    }
