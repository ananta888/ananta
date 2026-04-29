from __future__ import annotations

from typing import Any

from worker.retrieval.codecompass_budgeting import apply_codecompass_budget, resolve_codecompass_budget
from worker.retrieval.query_rewrite import rewrite_query
from worker.retrieval.ranking import merge_rank_candidates
from worker.retrieval.reranker import Reranker
from worker.retrieval.retrieval_trace import build_retrieval_trace
from worker.retrieval.retrieval_contract import DEFAULT_FALLBACK_ORDER, validate_pipeline_payload


class HybridRetrievalService:
    def __init__(self, *, reranker: Reranker | None = None):
        self._reranker = reranker or Reranker(enabled=False)

    @staticmethod
    def _graph_expansion_candidates(
        *,
        payload: dict[str, Any] | None,
        max_nodes: int,
    ) -> list[dict[str, Any]]:
        chunks = [dict(item) for item in list((payload or {}).get("chunks") or []) if isinstance(item, dict)]
        ranked: list[dict[str, Any]] = []
        for item in chunks[: max(1, int(max_nodes))]:
            metadata = dict(item.get("metadata") or {})
            ranked.append(
                {
                    "path": str(item.get("source") or metadata.get("file") or ""),
                    "record_id": str(metadata.get("record_id") or item.get("record_id") or ""),
                    "content_hash": str(metadata.get("record_id") or item.get("record_id") or item.get("source") or ""),
                    "score": float(item.get("score") or 0.15),
                    "metadata": metadata,
                    "content": str(item.get("content") or ""),
                }
            )
        return ranked

    def retrieve(
        self,
        *,
        query: str,
        pipeline_contract: dict[str, Any] | None,
        channel_results: dict[str, list[dict[str, Any]]],
        channel_config: dict[str, bool] | None = None,
        channel_errors: dict[str, str] | None = None,
        graph_expansion: dict[str, Any] | None = None,
        channel_latency_ms: dict[str, int] | None = None,
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
        normalized_errors = {
            str(key).strip().lower(): str(value).strip().lower()
            for key, value in dict(channel_errors or {}).items()
            if str(key).strip()
        }
        budget_profile = resolve_codecompass_budget(profile=str(profile or "balanced"))
        for channel in ordered_channels:
            channel_enabled = bool(normalized_config.get(channel, True))
            candidates = [dict(item) for item in list(channel_results.get(channel) or []) if isinstance(item, dict)]
            if not channel_enabled and channel.startswith("codecompass_"):
                diagnostics[channel] = {"status": "disabled", "reason": "flag_disabled", "candidate_count": len(candidates)}
                continue
            channel_error = normalized_errors.get(channel)
            if channel_error and channel.startswith("codecompass_"):
                diagnostics[channel] = {"status": "degraded", "reason": channel_error, "candidate_count": len(candidates)}
                continue
            if candidates:
                for item in candidates:
                    item["channel"] = channel
                    metadata = dict(item.get("metadata") or {})
                    if metadata.get("record_id") and not item.get("record_id"):
                        item["record_id"] = str(metadata.get("record_id"))
                merged_candidates.extend(candidates)
                used_channels.append(channel)
                diagnostics[channel] = {"status": "ready", "reason": "candidates_available", "candidate_count": len(candidates)}
            elif channel.startswith("codecompass_"):
                diagnostics[channel] = {"status": "degraded", "reason": "channel_empty", "candidate_count": 0}

        graph_enabled = bool(normalized_config.get("codecompass_graph", True))
        graph_error = normalized_errors.get("codecompass_graph")
        if graph_enabled and not graph_error and isinstance(graph_expansion, dict):
            expanded_candidates = self._graph_expansion_candidates(
                payload=graph_expansion,
                max_nodes=int(budget_profile.get("graph_expansion_max_nodes") or 8),
            )
            if expanded_candidates:
                for item in expanded_candidates:
                    item["channel"] = "codecompass_graph"
                merged_candidates.extend(expanded_candidates)
                if "codecompass_graph" not in used_channels:
                    used_channels.append("codecompass_graph")
                diagnostics["codecompass_graph"] = {
                    "status": "ready",
                    "reason": "expanded_from_seeds",
                    "candidate_count": len(expanded_candidates),
                    "seed_count": len(
                        {
                            str(item.get("record_id") or "")
                            for item in merged_candidates
                            if str(item.get("channel") or "") in {"codecompass_fts", "codecompass_vector"}
                            and str(item.get("record_id") or "")
                        }
                    ),
                    "expanded_count": len(expanded_candidates),
                }
            elif "codecompass_graph" not in diagnostics:
                diagnostics["codecompass_graph"] = {"status": "degraded", "reason": "expansion_empty", "candidate_count": 0}

        ranked = merge_rank_candidates(
            candidates=merged_candidates,
            task_type=str(task_type or "bugfix"),
            profile=str(profile or "balanced"),
            top_k=max(1, int(top_k) * 4),
        )
        reranked = self._reranker.rerank(query=str(rewrite["rewritten"]), candidates=ranked)
        budgeted = apply_codecompass_budget(
            ranked_candidates=reranked,
            profile=str(profile or "balanced"),
            top_k=max(1, int(top_k)),
        )
        if budgeted["budget"].get("degraded_reason"):
            diagnostics["codecompass_budget"] = {
                "status": "degraded",
                "reason": str(budgeted["budget"]["degraded_reason"]),
                "dropped": dict(budgeted["budget"].get("dropped_by_reason") or {}),
            }
        selected_candidates = list(budgeted.get("selected") or [])
        selected = []
        for item in selected_candidates[: max(1, int(top_k))]:
            selected.append(
                {
                    "path": str(item.get("path") or ""),
                    "symbol_name": str(item.get("symbol_name") or ""),
                    "content_hash": str(item.get("content_hash") or ""),
                    "channel": str(item.get("channel") or ""),
                    "final_score": float(item.get("final_score") or 0.0),
                    "channel_contributions": dict(item.get("channel_contributions") or {}),
                    "metadata": dict(item.get("metadata") or {}),
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
                "record_id": str(
                    item.get("record_id")
                    or item.get("content_hash")
                    or item.get("path")
                    or ""
                ),
                "file": str(item.get("path") or ""),
                "kind": str((item.get("metadata") or {}).get("record_kind") or ""),
                "score": float(item.get("final_score") or 0.0),
                "expanded_from": str((item.get("metadata") or {}).get("expanded_from") or ""),
                "relation_path": str((item.get("metadata") or {}).get("relation_path") or ""),
                "manifest_hash": str((item.get("metadata") or {}).get("source_manifest_hash") or ""),
            }
            for item in selected
        ]
        manifest_hash = next((str(item.get("manifest_hash") or "") for item in provenance if str(item.get("manifest_hash") or "")), "")
        graph_diag = dict(diagnostics.get("codecompass_graph") or {})
        retrieval_trace = build_retrieval_trace(
            query_original=str(rewrite["original"]),
            query_rewritten=str(rewrite["rewritten"]),
            channel_diagnostics=diagnostics,
            selected=selected,
            provenance=provenance,
            manifest_hash=manifest_hash,
            graph_seed_count=int(graph_diag.get("seed_count") or 0),
            graph_expanded_count=int(graph_diag.get("expanded_count") or 0),
            channel_latency_ms=channel_latency_ms,
        )
        return {
            "schema": "retrieval_selection.v1",
            "query_original": rewrite["original"],
            "query_rewritten": rewrite["rewritten"],
            "used_channels": used_channels,
            "channel_diagnostics": diagnostics,
            "provenance": provenance,
            "codecompass_budget": dict(budgeted.get("budget") or {}),
            "retrieval_trace": retrieval_trace,
            "selected": selected,
        }
