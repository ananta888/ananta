"""Hub-owned validation of SIRA model and rollout configuration."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from ananta_contracts.sira_retrieval import SiraConfig, SiraMode


class SiraModelCatalogPort(Protocol):
    def get(self, model_id: str) -> Mapping[str, Any] | None: ...


class CodeCompassSiraConfigService:
    """Resolve policy-safe configuration before a Worker job is dispatched."""

    def resolve(self, *, settings: Any, model_catalog: SiraModelCatalogPort | None = None) -> dict[str, Any]:
        config = SiraConfig.from_mapping(
            {
                "schema": "codecompass.sira-config.v1",
                "mode": str(getattr(settings, "codecompass_sira_mode", "off") or "off"),
                "enrichment_model": str(getattr(settings, "codecompass_sira_enrichment_model", "") or ""),
                "query_model": str(getattr(settings, "codecompass_sira_query_model", "") or ""),
                "rerank_model": str(getattr(settings, "codecompass_sira_rerank_model", "") or ""),
                "reranker_enabled": bool(getattr(settings, "codecompass_sira_reranker_enabled", False)),
                "local_models_only": bool(getattr(settings, "codecompass_sira_local_models_only", True)),
                "temperature": 0.0,
                "profile_version": "corpus-discriminative-lexical.v1",
            }
        )
        if config.mode == SiraMode.OFF:
            return config.safe_dict()
        if model_catalog is None:
            raise ValueError("sira_model_catalog_required")
        required_models = {"query": config.query_model}
        if config.enrichment_model:
            required_models["enrichment"] = config.enrichment_model
        if config.reranker_enabled:
            required_models["rerank"] = config.rerank_model
        resolved: dict[str, Any] = {}
        for role, model_id in required_models.items():
            model = model_catalog.get(model_id)
            if not isinstance(model, Mapping):
                raise ValueError(f"sira_{role}_model_unavailable")
            capabilities = {str(item) for item in list(model.get("capabilities") or [])}
            if not capabilities.intersection({"chat", "code", "text_generation", "rerank"}):
                raise ValueError(f"sira_{role}_model_capability_missing")
            if config.local_models_only and not bool(model.get("local")):
                raise ValueError(f"sira_{role}_model_external_denied")
            resolved[role] = {
                "model_id": model_id,
                "model_digest": str(model.get("digest") or ""),
                "provider_id": str(model.get("provider_id") or ""),
                "local": bool(model.get("local")),
                "capabilities": sorted(capabilities),
            }
        return {**config.safe_dict(), "resolved_models": resolved}


__all__ = ["CodeCompassSiraConfigService", "SiraModelCatalogPort"]
