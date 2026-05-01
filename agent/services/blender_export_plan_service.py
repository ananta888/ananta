from __future__ import annotations

SUPPORTED_EXPORT_FORMATS = {"GLTF", "FBX", "OBJ", "USD"}


def build_export_plan(*, fmt: str, target_path: str, selection_only: bool = False) -> dict:
    normalized_format = str(fmt or "GLTF").strip().upper()
    if normalized_format == "GLB":
        normalized_format = "GLTF"
    return {
        "format": normalized_format,
        "supported": normalized_format in SUPPORTED_EXPORT_FORMATS,
        "target_path": str(target_path or ""),
        "selection_only": bool(selection_only),
        "approval_required": True,
        "execution_mode": "plan_only",
        "safety_notes": [
            "export planning does not write files",
            "actual export requires approval-bound execution",
        ],
    }
