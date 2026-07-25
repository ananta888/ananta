from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from agent.services.embedding_provider_config_service import (
    EmbeddingProviderConfigService,
    build_embedding_provider_from_config,
)
from agent.services.codecompass_retrieval_strategy import (
    RetrievalStrategyConfig,
    apply_semantic_prefilter,
)
from worker.retrieval.codecompass_embedding_loader import load_codecompass_embedding_documents
from worker.retrieval.codecompass_vector_engine import CodeCompassVectorEngine
from worker.retrieval.embedding_text_builder import (
    CODECOMPASS_EMBEDDING_TEXT_PROFILE,
    build_embedding_texts_batch,
)
from worker.retrieval.vector_encoding import VectorEncoder, VectorEncodingProfile
from worker.retrieval.vector_store_config import VectorStoreConfig
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorStore,
    VectorStoreError,
    VectorStoreFilters,
)
from worker.retrieval.vector_store_endpoint_policy import SecretResolver
from worker.retrieval.vector_store_factory import VectorStoreFactory

if TYPE_CHECKING:
    from agent.services.restricted_model_inference_service import RestrictedModelInferenceService

log = logging.getLogger(__name__)


class CodeCompassVectorRetrievalService:
    """Agent-side adapter for CodeCompass vector retrieval.

    The service owns file loading, provider resolution and index refresh. The
    worker package still owns embedding/vector execution.

    Optional ``restricted_inference_service`` and ``strategy_config`` enable
    the semantic pre-filter and other retrieval strategies (see
    ``codecompass_retrieval_strategy.py``).
    """

    def __init__(
        self,
        *,
        repo_root: str | Path,
        embedding_records_path: str | Path,
        manifest_path: str | Path,
        index_path: str | Path,
        provider_config: dict[str, Any] | None = None,
        embedding_text_profile: str = CODECOMPASS_EMBEDDING_TEXT_PROFILE,
        fail_mode: str = "degraded_empty",
        restricted_inference_service: "RestrictedModelInferenceService | None" = None,
        strategy_config: RetrievalStrategyConfig | None = None,
        vector_encoding_config: dict[str, Any] | None = None,
        vector_encoding_fallback_policy: str = "fallback_float32",
        vector_store_config: Mapping[str, Any] | VectorStoreConfig | None = None,
        vector_store_factory: VectorStoreFactory | None = None,
        vector_store: VectorStore | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.embedding_records_path = self._resolve_path(embedding_records_path)
        self.manifest_path = self._resolve_path(manifest_path)
        resolved_index_path = self._resolve_path(index_path)
        store_config = (
            vector_store_config
            if isinstance(vector_store_config, VectorStoreConfig)
            else VectorStoreConfig.from_mapping(vector_store_config)
            if vector_store_config is not None
            else VectorStoreConfig.for_json(resolved_index_path)
        )
        self.store = vector_store or (vector_store_factory or VectorStoreFactory()).create(
            store_config,
            secret_resolver=secret_resolver,
        )
        self.provider_config = dict(provider_config or {})
        self.embedding_text_profile = str(embedding_text_profile or CODECOMPASS_EMBEDDING_TEXT_PROFILE)
        self.fail_mode = str(fail_mode or "degraded_empty")
        self._restricted_inference = restricted_inference_service
        self._strategy_config: RetrievalStrategyConfig = strategy_config or RetrievalStrategyConfig()
        self._vector_encoder = VectorEncoder(VectorEncodingProfile.from_config(vector_encoding_config))
        self._vector_encoding_fallback_policy = str(vector_encoding_fallback_policy or "fallback_float32")
        self._last_diagnostic: dict[str, Any] = {"status": "not_run", "reason": "not_run"}

    def _resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.repo_root / path

    def last_diagnostic(self) -> dict[str, Any]:
        return dict(self._last_diagnostic)

    def search(self, *, query: str, top_k: int = 10, allowed_paths: list[str] | None = None) -> list[dict[str, Any]]:
        try:
            provider_service = EmbeddingProviderConfigService(global_config=self.provider_config)
            provider_cfg = provider_service.resolve("codecompass_vector")
            provider = build_embedding_provider_from_config(provider_cfg)
            engine = CodeCompassVectorEngine(store=self.store, embedding_provider=provider)
            effective_top_k = self._strategy_config.effective_top_k(top_k)
            if allowed_paths is None:
                rows = engine.search(
                    query=query,
                    top_k=effective_top_k,
                    retrieval_intent="fuzzy_semantic",
                )
            else:
                prefixes = [str(path).strip() for path in allowed_paths if str(path).strip()]
                by_record: dict[str, dict[str, Any]] = {}
                for prefix in prefixes:
                    for row in engine.search(
                        query=query,
                        top_k=effective_top_k,
                        retrieval_intent="fuzzy_semantic",
                        filters=VectorStoreFilters(file_prefix=prefix),
                    ):
                        record_id = str(row.get("record_id") or "")
                        current = by_record.get(record_id)
                        if current is None or float(row.get("score") or 0.0) > float(current.get("score") or 0.0):
                            by_record[record_id] = row
                rows = sorted(
                    by_record.values(),
                    key=lambda item: float(item.get("score") or 0.0),
                    reverse=True,
                )[:effective_top_k]

            prefilter_applied = False
            if self._strategy_config.wants_prefilter() and self._restricted_inference is not None:
                before = len(rows)
                rows = apply_semantic_prefilter(
                    rows,
                    query,
                    restricted_inference=self._restricted_inference,
                    config=self._strategy_config,
                    requested_top_k=top_k,
                )
                prefilter_applied = True
                log.debug(
                    "semantic_prefilter: %d → %d candidates (strategy=%s)",
                    before, len(rows), self._strategy_config.strategy,
                )
            elif self._strategy_config.wants_prefilter() and self._restricted_inference is None:
                log.debug("semantic_prefilter requested but no restricted_inference_service configured; skipping")
                rows = rows[:top_k]

            self._last_diagnostic = {
                **engine.last_diagnostic(),
                "candidate_count": len(rows),
                "retrieval_strategy": self._strategy_config.strategy,
                "prefilter_applied": prefilter_applied,
                "engine": engine.last_diagnostic(),
                "vector_encoding": {
                    "mode": self._vector_encoder.profile.mode,
                    "enabled": self._vector_encoder.profile.enabled,
                    "experimental": self._vector_encoder.profile.experimental,
                    "profile_hash": self._vector_encoder.profile.config_hash(),
                    "fallback_policy": self._vector_encoding_fallback_policy,
                },
            }
            return rows
        except Exception as exc:
            if self.fail_mode != "degraded_empty":
                raise
            self._last_diagnostic = {
                "status": "degraded",
                "reason": self._classify_exception(exc),
                "error": str(exc),
            }
            return []

    def refresh_index(self) -> dict[str, Any]:
        """Explicit Hub-owned indexing entry point; search remains read-only."""
        try:
            documents, manifest, load_diagnostics = self._load_documents()
            provider_service = EmbeddingProviderConfigService(global_config=self.provider_config)
            provider_cfg = provider_service.resolve("codecompass_vector")
            provider = build_embedding_provider_from_config(provider_cfg)
            vectors = provider.embed_texts(build_embedding_texts_batch(documents))
            if len(vectors) != len(documents):
                raise VectorStoreError("embedding_response_size_mismatch")
            scope = VectorScope(
                workspace_id=str(manifest.get("workspace_id") or "local"),
                repository_id=str(manifest.get("repository_id") or self.repo_root.name or "repository"),
                profile_name=str(manifest.get("profile_name") or "default"),
                domain="codecompass",
            )
            points = [
                PreparedVectorPoint(
                    record_id=str(document.get("record_id") or ""),
                    vector=tuple(float(item) for item in vector),
                    scope=scope,
                    source_hash=str(document.get("source_hash") or document.get("content_hash") or ""),
                    payload={
                        "kind": str(document.get("kind") or ""),
                        "file": str(document.get("file") or ""),
                        "parent_id": str(document.get("parent_id") or ""),
                        "role_labels": [
                            str(item)
                            for item in list(document.get("role_labels") or [])
                            if str(item).strip()
                        ],
                        "importance_score": float(document.get("importance_score") or 0.0),
                        "source_scope": str(document.get("source_scope") or manifest.get("source_scope") or "repo"),
                        "profile_name": str(document.get("profile_name") or manifest.get("profile_name") or "default"),
                        "source_manifest_hash": str(document.get("manifest_hash") or manifest.get("manifest_hash") or ""),
                        "embedding_text": str(document.get("embedding_text") or ""),
                    },
                )
                for document, vector in zip(documents, vectors, strict=True)
            ]
            result = self.store.refresh(
                points,
                compatibility=CompatibilitySpec(
                    dimensions=int(getattr(provider, "dimensions", 0) or 0),
                    distance="cosine",
                    provider=str(getattr(provider, "provider_id", "unknown") or "unknown"),
                    model=str(getattr(provider, "model_version", "unknown") or "unknown"),
                    profile=self.embedding_text_profile,
                    encoding=self._vector_encoder.profile.config_hash(),
                    config_hash=provider_cfg.config_hash(),
                    schema_version="codecompass_vector_index.v2",
                    manifest_hash=str(manifest.get("manifest_hash") or ""),
                ),
            )
            self._last_diagnostic = {
                "status": result.status,
                "reason": result.reason,
                "load": load_diagnostics,
                "refresh": dict(result.diagnostics),
            }
            return result.as_dict()
        except Exception as exc:
            if self.fail_mode != "degraded_empty":
                raise
            self._last_diagnostic = {
                "status": "degraded",
                "reason": self._classify_exception(exc),
                "error": str(exc),
            }
            return {
                "status": "degraded",
                "mode": "none",
                "reason": self._last_diagnostic["reason"],
                "indexed_documents": 0,
                "diagnostics": dict(self._last_diagnostic),
            }

    def _load_documents(self) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        if not self.embedding_records_path.exists():
            raise FileNotFoundError(f"missing_embedding_records:{self.embedding_records_path}")
        if not self.manifest_path.exists():
            manifest: dict[str, Any] = {}
        else:
            manifest = self._read_json_object(self.manifest_path)
        records = self._read_records(self.embedding_records_path)
        payload = load_codecompass_embedding_documents(records=records, manifest=manifest)
        documents = list(payload.get("documents") or [])
        if not documents:
            raise ValueError("no_codecompass_embedding_documents")
        return documents, manifest, dict(payload.get("diagnostics") or {})

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"expected_json_object:{path}")
        return payload

    @staticmethod
    def _read_records(path: Path) -> list[dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            records = payload.get("records")
            if isinstance(records, list):
                return [dict(item) for item in records if isinstance(item, dict)]
        raise ValueError(f"expected_embedding_records:{path}")

    def _classify_exception(exc: Exception) -> str:
        if isinstance(exc, VectorStoreError):
            return exc.reason
        text = str(exc)
        if isinstance(exc, FileNotFoundError):
            return "missing_embedding_records"
        if isinstance(exc, json.JSONDecodeError):
            return "invalid_json"
        if text.startswith("embedding_provider_blocked"):
            return "provider_blocked"
        if text.startswith("no_codecompass_embedding_documents"):
            return "no_embedding_documents"
        return "codecompass_vector_unavailable"
