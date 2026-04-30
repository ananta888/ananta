from __future__ import annotations


def summarize_context_for_preview(ctx: dict) -> dict:
    objs = list((ctx or {}).get("objects") or [])
    return {"object_count": len(objs), "scene": ((ctx or {}).get("scene") or {}).get("name", ""), "excluded": "bounded_payload"}
