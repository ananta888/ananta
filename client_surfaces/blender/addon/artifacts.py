from __future__ import annotations


def normalize_artifacts(items: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    for item in list(items or []):
        payload = dict(item or {})
        payload.setdefault("id", payload.get("artifact_id") or "")
        payload.setdefault("artifact_type", payload.get("type") or payload.get("media_type") or "unknown")
        payload.setdefault("preview_mode", preview_mode_for_artifact(payload))
        normalized.append(payload)
    return normalized


def preview_mode_for_artifact(item: dict) -> str:
    artifact_type = str((item or {}).get("artifact_type") or (item or {}).get("media_type") or "").lower()
    name = str((item or {}).get("filename") or (item or {}).get("name") or "").lower()
    if artifact_type.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return "image_ref"
    if "json" in artifact_type or name.endswith(".json"):
        return "json_text"
    if artifact_type.startswith("text/") or name.endswith((".txt", ".md", ".py")):
        return "text"
    return "safe_unknown"
