from __future__ import annotations

from typing import Any


def inspect_model_context(payload: dict[str, Any]) -> dict[str, Any]:
    document = dict(payload.get("document") or {})
    objects = list(payload.get("objects") or [])
    return {
        "document_name": str(document.get("name") or ""),
        "object_count": len(objects),
        "visible_count": sum(1 for o in objects if bool(o.get("visibility", True))),
        "types": sorted({str(o.get("type") or "Unknown") for o in objects}),
        "warnings": ["empty_model"] if not objects else [],
        "mode": "read_only",
    }
