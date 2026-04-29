from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_retrieval_trace(
    *,
    query_original: str,
    query_rewritten: str,
    channel_diagnostics: dict[str, dict[str, Any]],
    selected: list[dict[str, Any]],
    provenance: list[dict[str, Any]],
    manifest_hash: str | None = None,
    graph_seed_count: int = 0,
    graph_expanded_count: int = 0,
    channel_latency_ms: dict[str, int] | None = None,
) -> dict[str, Any]:
    diagnostics = {str(key): dict(value or {}) for key, value in dict(channel_diagnostics or {}).items()}
    enabled_channels = sorted(
        key
        for key, value in diagnostics.items()
        if str((value or {}).get("status") or "").strip().lower() == "ready"
    )
    degraded_channels = sorted(
        key
        for key, value in diagnostics.items()
        if str((value or {}).get("status") or "").strip().lower() in {"degraded", "missing_dependency"}
    )
    selected_counts: dict[str, int] = {}
    for item in list(selected or []):
        channel = str(item.get("channel") or "").strip().lower()
        if not channel:
            continue
        selected_counts[channel] = int(selected_counts.get(channel) or 0) + 1
    latencies = {
        str(key).strip().lower(): max(0, int(value))
        for key, value in dict(channel_latency_ms or {}).items()
        if str(key).strip()
    }
    context_hash = _stable_hash(
        {
            "query_rewritten": str(query_rewritten or ""),
            "selected_record_ids": [str(item.get("record_id") or "") for item in list(provenance or [])],
            "manifest_hash": str(manifest_hash or ""),
        }
    )
    trace_id = f"retrieval-{_stable_hash({'context_hash': context_hash, 'query_original': str(query_original or '')})[:16]}"
    return {
        "trace_id": trace_id,
        "enabled_channels": enabled_channels,
        "degraded_channels": degraded_channels,
        "seed_counts": {"graph_seed_count": int(graph_seed_count)},
        "graph_expansion_counts": {"expanded_nodes": int(graph_expanded_count)},
        "final_chunk_count": len(list(selected or [])),
        "context_hash": context_hash,
        "manifest_hash": str(manifest_hash or ""),
        "selected_chunk_counts_by_channel": selected_counts,
        "channel_latency_ms": latencies,
    }

