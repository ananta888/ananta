from __future__ import annotations

from collections import defaultdict
from typing import Any

from ananta_codecompass.ranking.profiles import (
    WORKER_CHANNEL_WEIGHTS,
    WORKER_TASK_PROFILE_ALIASES,
)
from worker.retrieval.retrieval_contract import VALID_CHANNELS, normalize_channel_name

_PROFILE_MULTIPLIER = {
    "safe": 0.9,
    "balanced": 1.0,
    "fast": 1.1,
}

WORKER_RANKING_VERSION = "universal-channel-fusion.v1"


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
    requested_intent = str(task_type or "").strip().lower()
    intent = WORKER_TASK_PROFILE_ALIASES.get(requested_intent, requested_intent)
    task_weights = WORKER_CHANNEL_WEIGHTS.get(intent, WORKER_CHANNEL_WEIGHTS["debugging"])
    profile_multiplier = _PROFILE_MULTIPLIER.get(str(profile or "").strip().lower(), 1.0)
    active_channels = {str(item.get("channel") or "dense") for item in normalized_candidates}
    active_weight_total = sum(float(task_weights.get(channel) or 0.08) for channel in active_channels) or 1.0
    merged: dict[str, dict[str, Any]] = {}
    for candidate in normalized_candidates:
        channel = str(candidate.get("channel") or "dense")
        if channel not in VALID_CHANNELS:
            continue
        metadata = dict(candidate.get("metadata") or {})
        key = str(
            candidate.get("record_id")
            or metadata.get("record_id")
            or candidate.get("content_hash")
            or candidate.get("path")
            or ""
        )
        if not key:
            continue
        weight = float(task_weights.get(channel) or 0.08) / active_weight_total
        contribution = float(candidate.get("normalized_score") or 0.0) * weight * profile_multiplier
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                **dict(candidate),
                "final_score": contribution,
                "channel_contributions": {channel: contribution},
                "ranking_version": WORKER_RANKING_VERSION,
                "score_explanation": {channel: {
                    "raw_score": float(candidate.get("score") or 0.0),
                    "normalized_score": float(candidate.get("normalized_score") or 0.0),
                    "weight": weight,
                    "contribution": contribution,
                }},
            }
            continue
        existing["final_score"] = float(existing.get("final_score") or 0.0) + contribution
        contributions = dict(existing.get("channel_contributions") or {})
        contributions[channel] = float(contributions.get(channel) or 0.0) + contribution
        existing["channel_contributions"] = contributions
        explanation = dict(existing.get("score_explanation") or {})
        explanation[channel] = {
            "raw_score": float(candidate.get("score") or 0.0),
            "normalized_score": float(candidate.get("normalized_score") or 0.0),
            "weight": weight,
            "contribution": contribution,
        }
        existing["score_explanation"] = explanation
    ranked = sorted(
        merged.values(),
        key=lambda item: (
            -float(item.get("final_score") or 0.0),
            str(item.get("record_id") or item.get("path") or item.get("content_hash") or ""),
        ),
    )
    return ranked[: max(1, int(top_k))]
