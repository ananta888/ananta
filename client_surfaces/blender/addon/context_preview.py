from __future__ import annotations

import json


def summarize_context_for_preview(ctx: dict, *, max_payload_bytes: int = 32768) -> dict:
    objs = list((ctx or {}).get("objects") or [])
    payload_size = len(json.dumps(ctx or {}, ensure_ascii=False).encode("utf-8"))
    warnings: list[str] = []
    if payload_size > max_payload_bytes:
        warnings.append("payload_budget_exceeded")
    if ((ctx or {}).get("provenance") or {}).get("objects_clipped"):
        warnings.append("objects_clipped")
    return {
        "object_count": len(objs),
        "scene": ((ctx or {}).get("scene") or {}).get("name", ""),
        "selection_count": len(list((ctx or {}).get("selection") or [])),
        "payload_size_bytes": payload_size,
        "max_payload_bytes": max_payload_bytes,
        "warnings": warnings,
        "excluded": "bounded_payload",
    }
