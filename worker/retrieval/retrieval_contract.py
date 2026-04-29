from __future__ import annotations

from typing import Any

VALID_CHANNELS = ("dense", "lexical", "symbol")
DEFAULT_FALLBACK_ORDER = ("dense", "lexical", "symbol")


def normalize_channel_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_CHANNELS:
        return normalized
    raise ValueError(f"unknown_retrieval_channel:{normalized or '<missing>'}")


def validate_pipeline_payload(payload: dict[str, Any]) -> dict[str, Any]:
    channels = [normalize_channel_name(item) for item in list(payload.get("channels") or [])]
    fallback_order = [normalize_channel_name(item) for item in list(payload.get("fallback_order") or DEFAULT_FALLBACK_ORDER)]
    if not channels:
        raise ValueError("retrieval_channels_required")
    if any(channel not in channels for channel in fallback_order):
        raise ValueError("retrieval_fallback_not_subset")
    return {
        "schema": "retrieval_pipeline_contract.v1",
        "channels": channels,
        "fallback_order": fallback_order,
        "weights": {normalize_channel_name(key): float(value) for key, value in dict(payload.get("weights") or {}).items()},
    }

