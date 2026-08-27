"""Redacted Hub read model for SIRA operations and UI surfaces."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from agent.services.codecompass_retrieval_flag_service import evaluate_codecompass_retrieval_flags
from agent.services.codecompass_sira_config_service import CodeCompassSiraConfigService, SiraModelCatalogPort


class SiraWorkerStatusPort(Protocol):
    def read(self) -> Mapping[str, Any]: ...


class CodeCompassSiraStatusService:
    _INDEX_FIELDS = {
        "status",
        "reason",
        "base_layer_id",
        "delta_layer_ids",
        "artifact_count",
        "activation_digest",
        "index_digest",
        "statistics_digest",
        "profile_version",
        "last_successful_sync",
        "queue_depth",
        "cache_hit_rate",
        "compaction_status",
    }

    def build(
        self,
        *,
        settings: Any,
        model_catalog: SiraModelCatalogPort | None = None,
        worker_status: SiraWorkerStatusPort | None = None,
    ) -> dict[str, Any]:
        flags = evaluate_codecompass_retrieval_flags(settings=settings)
        try:
            config = CodeCompassSiraConfigService().resolve(settings=settings, model_catalog=model_catalog)
            config_status = "ready"
            config_reason = "sira_config_valid"
        except ValueError as exc:
            config = {
                "mode": str(getattr(settings, "codecompass_sira_mode", "off") or "off"),
                "profile_version": "corpus-discriminative-lexical.v1",
            }
            config_status = "degraded"
            config_reason = str(exc)
        index = {"status": "degraded", "reason": "worker_status_unavailable"}
        if worker_status is not None:
            try:
                raw = worker_status.read()
                index = {key: raw[key] for key in self._INDEX_FIELDS if key in raw}
                index.setdefault("status", "degraded")
                index.setdefault("reason", "worker_status_incomplete")
            except Exception:
                index = {"status": "degraded", "reason": "worker_status_failed"}
        mode = str(config.get("mode") or "off")
        overall = "disabled" if mode == "off" else "ready"
        if mode != "off" and (config_status != "ready" or index.get("status") != "ready"):
            overall = "degraded"
        return {
            "schema": "codecompass.sira-status.v1",
            "status": overall,
            "config_status": config_status,
            "config_reason": config_reason,
            "config": config,
            "flags": flags,
            "index": index,
            "rollout": {
                "mode": mode,
                "result_affecting": mode in {"preferred", "required"},
                "shadow_non_effecting": mode == "shadow",
                "kill_switches": {
                    "online_expansion": mode == "off",
                    "offline_enrichment": mode == "off",
                    "reranker": not bool(config.get("reranker_enabled")),
                },
            },
        }


__all__ = ["CodeCompassSiraStatusService", "SiraWorkerStatusPort"]
