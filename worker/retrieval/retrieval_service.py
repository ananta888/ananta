from __future__ import annotations

from typing import Any

from worker.retrieval.query_rewrite import rewrite_query
from worker.retrieval.ranking import merge_rank_candidates
from worker.retrieval.reranker import Reranker
from worker.retrieval.retrieval_contract import DEFAULT_FALLBACK_ORDER, validate_pipeline_payload


class HybridRetrievalService:
    def __init__(self, *, reranker: Reranker | None = None):
        self._reranker = reranker or Reranker(enabled=False)

    def retrieve(
        self,
        *,
        query: str,
        pipeline_contract: dict[str, Any] | None,
        channel_results: dict[str, list[dict[str, Any]]],
        channel_config: dict[str, bool] | None = None,
        task_type: str = "bugfix",
        profile: str = "balanced",
        top_k: int = 8,
    ) -> dict[str, Any]:
        contract = validate_pipeline_payload(pipeline_contract or {"channels": list(DEFAULT_FALLBACK_ORDER)})
        rewrite = rewrite_query(query)
        ordered_channels = list(contract.get("fallback_order") or DEFAULT_FALLBACK_ORDER)
        merged_candidates: list[dict[str, Any]] = []
        used_channels: list[str] = []
        diagnostics: dict[str, dict[str, Any]] = {}
        normalized_config = {str(key).strip().lower(): bool(value) for key, value in dict(channel_config or {}).items()}
        for channel in ordered_channels:
            channel_enabled = bool(normalized_config.get(channel, True))
            candidates = [dict(item) for item in list(channel_results.get(channel) or []) if isinstance(item, dict)]
            if not channel_enabled and channel.startswith("codecompass_"):
                diagnostics[channel] = {"status": "disabled", "reason": "flag_disabled", "candidate_count": len(candidates)}
                continue
            if candidates:
                for item in candidates:
                    item["channel"] = channel
                merged_candidates.extend(candidates)
                used_channels.append(channel)
                diagnostics[channel] = {"status": "ready", "reason": "candidates_available", "candidate_count": len(candidates)}
            elif channel.startswith("codecompass_"):
                diagnostics[channel] = {"status": "degraded", "reason": "channel_empty", "candidate_count": 0}
        ranked = merge_rank_candidates(
            candidates=merged_candidates,
            task_type=str(task_type or "bugfix"),
            profile=str(profile or "balanced"),
            top_k=max(1, int(top_k)),
        )
        reranked = self._reranker.rerank(query=str(rewrite["rewritten"]), candidates=ranked)
        selected = []
        for item in reranked[: max(1, int(top_k))]:
            selected.append(
                {
                    "path": str(item.get("path") or ""),
                    "symbol_name": str(item.get("symbol_name") or ""),
                    "content_hash": str(item.get("content_hash") or ""),
                    "channel": str(item.get("channel") or ""),
                    "final_score": float(item.get("final_score") or 0.0),
                    "channel_contributions": dict(item.get("channel_contributions") or {}),
                    "rationale": {
                        "channel": str(item.get("channel") or ""),
                        "score": float(item.get("final_score") or 0.0),
                        "profile": str(profile or "balanced"),
                    },
                }
            )
        provenance = [
            {
                "engine": str(item.get("channel") or ""),
                "record_id": str(item.get("content_hash") or item.get("path") or ""),
                "file": str(item.get("path") or ""),
                "score": float(item.get("final_score") or 0.0),
            }
            for item in selected
        ]
        return {
            "schema": "retrieval_selection.v1",
            "query_original": rewrite["original"],
            "query_rewritten": rewrite["rewritten"],
            "used_channels": used_channels,
            "channel_diagnostics": diagnostics,
            "provenance": provenance,
            "selected": selected,
        }
