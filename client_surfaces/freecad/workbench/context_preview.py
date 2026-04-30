from __future__ import annotations

import json
from typing import Any

SENSITIVE_KEYS = {"path", "token", "secret"}


def build_context_preview(context: dict[str, Any], *, max_preview_bytes: int | None = None) -> dict[str, Any]:
    serialized = json.dumps(dict(context or {}), sort_keys=True).encode("utf-8")
    payload_limit = int(((context.get("limits") or {}).get("max_payload_bytes") or 0) or 0)
    effective_limit = max_preview_bytes or payload_limit or 32768
    oversize = len(serialized) > effective_limit
    sensitive_fields = sorted(
        key
        for key in SENSITIVE_KEYS
        if key in dict(context.get("document") or {}) or key in dict(context.get("provenance") or {})
    )
    return {
        "document_name": str(((context.get("document") or {}).get("name") or "")),
        "object_count": len(list(context.get("objects") or [])),
        "selection_count": len(list(context.get("selection") or [])),
        "oversize": oversize,
        "payload_bytes": len(serialized),
        "sensitive_fields": sensitive_fields,
        "redaction": bool(((context.get("provenance") or {}).get("redaction"))),
        "expansion_mode": "explicit_only",
    }
