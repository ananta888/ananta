from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = [re.compile(r"(?i)(api[_-]?key|token|password)\s*[:=]\s*\S+")]


def redact_blender_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        out = {}
        for k, v in payload.items():
            lk = str(k).lower()
            if any(x in lk for x in ("token", "password", "secret", "api_key")):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_blender_payload(v)
        return out
    if isinstance(payload, list):
        return [redact_blender_payload(v) for v in payload]
    if isinstance(payload, str):
        text = payload
        for p in _SECRET_PATTERNS:
            text = p.sub("[REDACTED]", text)
        return text
    return payload
