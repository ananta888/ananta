from __future__ import annotations


def build_export_plan(*, fmt: str, target_path: str) -> dict:
    return {
        "format": str(fmt or "gltf"),
        "target_path": str(target_path or ""),
        "approval_required": True,
        "mode": "plan_only",
    }
