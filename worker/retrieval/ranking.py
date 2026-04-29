from __future__ import annotations

from collections import defaultdict
from typing import Any

from worker.retrieval.retrieval_contract import VALID_CHANNELS, normalize_channel_name

_TASK_WEIGHTS = {
    "bugfix": {"dense": 0.5, "lexical": 0.3, "symbol": 0.2},
    "feature": {"dense": 0.4, "lexical": 0.2, "symbol": 0.4},
    "bootstrap": {"dense": 0.3, "lexical": 0.5, "symbol": 0.2},
}

_PROFILE_MULTIPLIER = {
    "safe": 0.9,
    "balanced": 1.0,
    "fast": 1.1,
}


def _normalize_scores(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    channel_max: dict[str, float] = defaultdict(float)
    for item in candidates:
        channel = normalize_channel_name(str(item.get("channel") or "dense"))
        channel_max[channel] = max(channel_max[channel], float(item.get("score") or 0.0))
    normalized: list[dict[str, Any]] = []
    for item in candidates:
        channel = normalize_channel_name(str(item.get("channel") or "dense"))
        maximum = channel_max[channel] or 1.0
        normalized_score = float(item.get("score") or 0.0) / maximum
        normalized.append({**dict(item), "channel": channel, "normalized_score": normalized_score})
    return normalized


def merge_rank_candidates(
    *,
    candidates: list[dict[str, Any]],
    task_type: str = "bugfix",
    profile: str = "balanced",
    top_k: int = 8,
) -> list[dict[str, Any]]:
    normalized_candidates = _normalize_scores([item for item in list(candidates or []) if isinstance(item, dict)])
    if not normalized_candidates:
        return []
    task_weights = _TASK_WEIGHTS.get(str(task_type or "").strip().lower(), _TASK_WEIGHTS["bugfix"])
    profile_multiplier = _PROFILE_MULTIPLIER.get(str(profile or "").strip().lower(), 1.0)
    merged: dict[str, dict[str, Any]] = {}
    for candidate in normalized_candidates:
        channel = str(candidate.get("channel") or "dense")
        if channel not in VALID_CHANNELS:
            continue
        key = str(candidate.get("content_hash") or candidate.get("path") or "")
        if not key:
            continue
        weight = float(task_weights.get(channel) or 0.0)
        contribution = float(candidate.get("normalized_score") or 0.0) * weight * profile_multiplier
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                **dict(candidate),
                "final_score": contribution,
                "channel_contributions": {channel: contribution},
            }
            continue
        existing["final_score"] = float(existing.get("final_score") or 0.0) + contribution
        contributions = dict(existing.get("channel_contributions") or {})
        contributions[channel] = float(contributions.get(channel) or 0.0) + contribution
        existing["channel_contributions"] = contributions
    ranked = sorted(merged.values(), key=lambda item: float(item.get("final_score") or 0.0), reverse=True)
    return ranked[: max(1, int(top_k))]

