from __future__ import annotations


def read_scene_summary(scene: dict) -> dict:
    return {"name": scene.get("name"), "object_count": int(scene.get("object_count",0)), "mode": "read_only"}
