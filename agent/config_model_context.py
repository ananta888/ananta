"""Parsing and lookup for configured model context windows."""

from __future__ import annotations

import json


def parse_model_contexts(raw: str | None) -> dict[str, int]:
    if not raw or not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not key.strip():
            continue
        try:
            tokens = int(value)
        except (TypeError, ValueError):
            continue
        if tokens > 0:
            result[key.strip().lower()] = tokens
    return result


def lookup_model_context_tokens(model_id: str | None, raw_contexts: str | None) -> int | None:
    needle = str(model_id or "").strip().lower()
    if not needle:
        return None
    contexts = parse_model_contexts(raw_contexts)
    if needle in contexts:
        return contexts[needle]
    candidates = [key for key in contexts if key in needle or needle in key]
    if not candidates:
        return None
    return contexts[max(candidates, key=len)]
