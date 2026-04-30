from __future__ import annotations

from typing import Any


def format_inspection_result(result: dict[str, Any]) -> dict[str, Any]:
    warnings = [str(item) for item in list(result.get("warnings") or []) if str(item).strip()]
    return {
        "headline": str(result.get("document_name") or "Unnamed document"),
        "object_count": int(result.get("object_count") or 0),
        "visible_count": int(result.get("visible_count") or 0),
        "types": [str(item) for item in list(result.get("types") or [])],
        "warnings": warnings,
        "mode": str(result.get("mode") or "read_only"),
        "mutation_claim": "none",
    }
