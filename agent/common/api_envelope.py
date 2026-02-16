from __future__ import annotations

from typing import Any


def unwrap_api_envelope(payload: Any, max_depth: int = 6) -> dict:
    """
    Normalisiert gemischte interne API-Antworten auf ein dict-Payload.
    Unterstützt wiederholte Wrapper-Formate wie:
    - {"status": "...", "data": {...}}
    - {"code": 200, "data": {...}}
    - {"data": {"data": {...}}}
    """
    cur = payload
    depth = 0
    while depth < max_depth and isinstance(cur, dict):
        depth += 1
        if "data" not in cur:
            break
        nested = cur.get("data")
        if isinstance(nested, dict):
            cur = nested
            continue
        break
    return cur if isinstance(cur, dict) else {}
