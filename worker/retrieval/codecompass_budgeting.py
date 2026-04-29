from __future__ import annotations

from typing import Any

_PROFILE_BUDGETS = {
    "safe": {
        "max_total_chunks": 6,
        "channel_caps": {"codecompass_fts": 4, "codecompass_vector": 1, "codecompass_graph": 1},
        "graph_expansion_max_nodes": 8,
        "strategy": "exact_minimal",
    },
    "balanced": {
        "max_total_chunks": 8,
        "channel_caps": {"codecompass_fts": 4, "codecompass_vector": 2, "codecompass_graph": 2},
        "graph_expansion_max_nodes": 14,
        "strategy": "hybrid_bounded",
    },
    "fast": {
        "max_total_chunks": 10,
        "channel_caps": {"codecompass_fts": 3, "codecompass_vector": 4, "codecompass_graph": 3},
        "graph_expansion_max_nodes": 18,
        "strategy": "broad_bounded",
    },
}


def resolve_codecompass_budget(*, profile: str) -> dict[str, Any]:
    normalized = str(profile or "").strip().lower() or "balanced"
    return {
        "profile": normalized if normalized in _PROFILE_BUDGETS else "balanced",
        **dict(_PROFILE_BUDGETS.get(normalized) or _PROFILE_BUDGETS["balanced"]),
    }


def apply_codecompass_budget(
    *,
    ranked_candidates: list[dict[str, Any]],
    profile: str,
    top_k: int,
) -> dict[str, Any]:
    budget = resolve_codecompass_budget(profile=profile)
    max_total = min(max(1, int(top_k)), int(budget["max_total_chunks"]))
    channel_caps = {str(key): int(value) for key, value in dict(budget.get("channel_caps") or {}).items()}
    channel_counts: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    dropped_by_reason = {"total_budget": 0, "channel_cap": 0}
    for candidate in list(ranked_candidates or []):
        channel = str(candidate.get("channel") or "").strip().lower()
        if channel in channel_caps:
            count = int(channel_counts.get(channel) or 0)
            if count >= int(channel_caps[channel]):
                dropped_by_reason["channel_cap"] += 1
                continue
            channel_counts[channel] = count + 1
        selected.append(dict(candidate))
        if len(selected) >= max_total:
            break
    remaining_candidates = max(0, len(list(ranked_candidates or [])) - len(selected))
    if remaining_candidates > 0:
        dropped_by_reason["total_budget"] = remaining_candidates
    degraded_reason = None
    if remaining_candidates > 0:
        degraded_reason = "budget_exhausted"
    return {
        "selected": selected,
        "budget": {
            "profile": budget["profile"],
            "strategy": budget["strategy"],
            "max_total_chunks": max_total,
            "channel_caps": channel_caps,
            "channel_counts": channel_counts,
            "graph_expansion_max_nodes": int(budget["graph_expansion_max_nodes"]),
            "dropped_by_reason": dropped_by_reason,
            "degraded_reason": degraded_reason,
        },
    }

