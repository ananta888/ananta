from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from agent.services.codecompass_retrieval_strategy import (
    RetrievalStrategyConfig,
    apply_semantic_prefilter,
)
from agent.services.embedding_provider_config_service import (
    EmbeddingProviderConfigService,
    build_embedding_provider_from_config,
)
from ananta_codecompass.embedding_loader import load_codecompass_embedding_documents
from ananta_codecompass.vector_engine import CodeCompassVectorEngine
from worker.retrieval.embedding_text_builder import (
    CODECOMPASS_EMBEDDING_TEXT_PROFILE,
    build_embedding_texts_batch,
)
from worker.retrieval.vector_encoding import VectorEncoder, VectorEncodingProfile
from worker.retrieval.vector_index_input_loader import (
    VectorIndexInputError,
    VectorIndexInputReference,
)
from worker.retrieval.vector_index_preparation import (
    CODECOMPASS_DOCUMENTS,
    VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA,
    VECTOR_INDEX_PREPARATION_SCHEMA,
    VectorIndexPreparationSpec,
)
from worker.retrieval.vector_store_config import VectorStoreConfig, VectorStoreProvider
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorIndexWriter,
    VectorScope,
    VectorSearchPort,
    VectorStore,
    VectorStoreError,
    VectorStoreFailClosedError,
    VectorStoreFilters,
    VectorStoreLifecycle,
)
from worker.retrieval.vector_store_endpoint_policy import SecretResolver
from worker.retrieval.vector_store_factory import VectorStoreFactory
from worker.retrieval.vector_store_observer import VectorStoreObserver

if TYPE_CHECKING:
    from agent.services.restricted_model_inference_service import (
        RestrictedModelInferenceService,
    )

log = logging.getLogger(__name__)


class VectorIndexTaskSubmissionPort(Protocol):
    def submit(self, **kwargs: Any) -> dict[str, Any]: ...


class VectorIndexInputPublisherPort(Protocol):
    def publish(
        self,
        *,
        scope: VectorScope,
        content: bytes,
        content_sha256: str,
    ) -> Mapping[str, Any]: ...


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
        vector_search_port: VectorSearchPort | None = None,
        vector_index_writer: VectorIndexWriter | None = None,
        secret_resolver: SecretResolver | None = None,
        vector_store_observer: VectorStoreObserver | None = None,
        trusted_scope: VectorScope | None = None,
        index_task_service: VectorIndexTaskSubmissionPort | None = None,
        index_input_publisher: VectorIndexInputPublisherPort | None = None,
        index_task_actor: str = "codecompass-vector-service",
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
        self._store_config = store_config
        legacy_fail_mode = str(fail_mode or "degraded_empty")
        self._search_fail_mode = (
            store_config.availability.on_unavailable.value
            if store_config.provider == VectorStoreProvider.QDRANT
            else legacy_fail_mode
        )
        composed_store = vector_store
        if composed_store is None and vector_search_port is None:
            composed_store = (vector_store_factory or VectorStoreFactory()).create(
                store_config,
                secret_resolver=secret_resolver,
                observer=vector_store_observer,
            )
        self._vector_search = vector_search_port or composed_store
        if self._vector_search is None:
            raise ValueError("vector_search_port_required")
        self._vector_index_writer = vector_index_writer or composed_store
        # Compatibility facade for callers that inspected the historical
        # attribute. Product composition injects the two narrow ports above.
        self.store = composed_store or self._vector_search
        self.provider_config = dict(provider_config or {})
        self.embedding_text_profile = str(embedding_text_profile or CODECOMPASS_EMBEDDING_TEXT_PROFILE)
        self.fail_mode = legacy_fail_mode
        self._restricted_inference = restricted_inference_service
        self._strategy_config: RetrievalStrategyConfig = strategy_config or RetrievalStrategyConfig()
        self._vector_encoder = VectorEncoder(VectorEncodingProfile.from_config(vector_encoding_config))
        self._vector_encoding_fallback_policy = str(vector_encoding_fallback_policy or "fallback_float32")
        repository_id = (
            re.sub(
                r"[^A-Za-z0-9._:-]+",
                "-",
                self.repo_root.name,
            ).strip("-")
            or "repository"
        )
        self._trusted_scope = trusted_scope or VectorScope(
            workspace_id="local",
            repository_id=repository_id,
            profile_name="default",
            domain="codecompass",
        )
        if self._trusted_scope.domain != "codecompass":
            raise ValueError("codecompass_vector_scope_domain_invalid")
        self._index_task_service = index_task_service
        self._index_input_publisher = index_input_publisher
        self._index_task_actor = str(index_task_actor or "codecompass-vector-service")
        self._last_diagnostic: dict[str, Any] = {"status": "not_run", "reason": "not_run"}
        self._closed = False

    def _resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.repo_root / path

    def last_diagnostic(self) -> dict[str, Any]:
        return dict(self._last_diagnostic)

    def close(self) -> None:
        """Release injected vector capabilities without double-closing."""

        if self._closed:
            return
        self._closed = True
        closed_ids: set[int] = set()
        for capability in (
            self._vector_search,
            self._vector_index_writer,
        ):
            if capability is None or id(capability) in closed_ids or not isinstance(capability, VectorStoreLifecycle):
                continue
            closed_ids.add(id(capability))
            capability.close()

    def search(self, *, query: str, top_k: int = 10, allowed_paths: list[str] | None = None) -> list[dict[str, Any]]:
        try:
            provider_service = EmbeddingProviderConfigService(global_config=self.provider_config)
            provider_cfg = provider_service.resolve("codecompass_vector")
            provider = build_embedding_provider_from_config(provider_cfg)
            manifest = self._read_json_object(self.manifest_path) if self.manifest_path.exists() else {}
            compatibility = self._compatibility(
                provider=provider,
                provider_config=provider_cfg,
                manifest=manifest,
            )
            engine = CodeCompassVectorEngine(
                store=self._vector_search,
                embedding_provider=provider,
                propagate_vector_store_errors=(self._search_fail_mode != "degraded_empty"),
            )
            effective_top_k = self._strategy_config.effective_top_k(top_k)
            if allowed_paths is None:
                rows = engine.search(
                    query=query,
                    top_k=effective_top_k,
                    retrieval_intent="fuzzy_semantic",
                    scope=self._trusted_scope,
                    compatibility=compatibility,
                )
            else:
                prefixes = [str(path).strip() for path in allowed_paths if str(path).strip()]
                by_record: dict[str, dict[str, Any]] = {}
                for prefix in prefixes:
                    for row in engine.search(
                        query=query,
                        top_k=effective_top_k,
                        retrieval_intent="fuzzy_semantic",
                        scope=self._trusted_scope,
                        filters=VectorStoreFilters(file_prefix=prefix),
                        compatibility=compatibility,
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
                    before,
                    len(rows),
                    self._strategy_config.strategy,
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
        except VectorStoreFailClosedError:
            raise
        except Exception as exc:
            if self._search_fail_mode != "degraded_empty":
                raise
            self._last_diagnostic = {
                "status": "degraded",
                "reason": self._classify_exception(exc),
            }
            return []

    def refresh_index(self) -> dict[str, Any]:
        """Prepare a refresh and delegate writes through the Hub when configured.

        Direct JSON refresh remains as an additive compatibility path for local
        callers. Qdrant mutations require the Hub task boundary.
        """
        try:
            documents, manifest, load_diagnostics = self._load_documents()
            provider_service = EmbeddingProviderConfigService(global_config=self.provider_config)
            provider_cfg = provider_service.resolve("codecompass_vector")
            provider = build_embedding_provider_from_config(provider_cfg)
            compatibility = self._compatibility(
                provider=provider,
                provider_config=provider_cfg,
                manifest=manifest,
            )
            if self._index_task_service is not None:
                task = self._submit_document_refresh_task(
                    documents=documents,
                    compatibility=compatibility,
                    provider_config=provider_cfg,
                )
                self._last_diagnostic = {
                    "status": str(task.get("status") or "queued"),
                    "reason": "vector_index_task_queued",
                    "load": load_diagnostics,
                    "job_id": task.get("job_id"),
                }
                return task
            if self._store_config.provider == VectorStoreProvider.QDRANT:
                raise VectorStoreError("vector_index_delegation_required")
            vectors = provider.embed_texts(build_embedding_texts_batch(documents))
            if len(vectors) != len(documents):
                raise VectorStoreError("embedding_response_size_mismatch")
            points = [
                PreparedVectorPoint(
                    record_id=str(document.get("record_id") or ""),
                    vector=tuple(float(item) for item in vector),
                    scope=self._trusted_scope,
                    source_hash=self._source_hash(document),
                    payload={
                        "kind": str(document.get("kind") or ""),
                        "file": str(document.get("file") or ""),
                        "parent_id": str(document.get("parent_id") or ""),
                        "role_labels": [
                            str(item) for item in list(document.get("role_labels") or []) if str(item).strip()
                        ],
                        "importance_score": float(document.get("importance_score") or 0.0),
                        "source_scope": str(document.get("source_scope") or manifest.get("source_scope") or "repo"),
                        "profile_name": str(document.get("profile_name") or manifest.get("profile_name") or "default"),
                        "source_manifest_hash": str(
                            document.get("manifest_hash") or manifest.get("manifest_hash") or ""
                        ),
                        "embedding_text": str(document.get("embedding_text") or ""),
                    },
                )
                for document, vector in zip(documents, vectors, strict=True)
            ]
            if self._vector_index_writer is None:
                raise VectorStoreError("vector_index_writer_required")
            result = self._vector_index_writer.refresh(
                points,
                compatibility=compatibility,
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
            }
            return {
                "status": "degraded",
                "mode": "none",
                "reason": self._last_diagnostic["reason"],
                "indexed_documents": 0,
                "diagnostics": dict(self._last_diagnostic),
            }

    def _submit_document_refresh_task(
        self,
        *,
        documents: list[dict[str, Any]],
        compatibility: CompatibilitySpec,
        provider_config: Any,
    ) -> dict[str, Any]:
        """Publish raw documents and delegate embedding plus refresh to a Worker."""

        from agent.services.vector_index_task_service import (
            VectorIndexTrustedScope,
        )

        if self._index_input_publisher is None:
            raise VectorStoreError("vector_index_input_publisher_required")
        serialized_documents = [self._delegated_document(document) for document in documents]
        content = json.dumps(
            {
                "schema": VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA,
                "kind": CODECOMPASS_DOCUMENTS,
                "documents": serialized_documents,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        content_sha256 = hashlib.sha256(content).hexdigest()
        published = self._index_input_publisher.publish(
            scope=self._trusted_scope,
            content=content,
            content_sha256=content_sha256,
        )
        if not isinstance(published, Mapping):
            raise VectorStoreError("vector_index_input_publisher_result_invalid")
        try:
            input_ref = VectorIndexInputReference.from_mapping(
                published,
                require_sha256=True,
                require_scope_fingerprint=True,
            )
        except VectorIndexInputError as exc:
            raise VectorStoreError(exc.reason) from exc
        if input_ref.sha256 != content_sha256:
            raise VectorStoreError("vector_index_input_publisher_digest_mismatch")
        try:
            input_ref.validate_binding(self._trusted_scope)
        except VectorIndexInputError as exc:
            raise VectorStoreError(exc.reason) from exc
        preparation = VectorIndexPreparationSpec.from_mapping(
            {
                "schema": VECTOR_INDEX_PREPARATION_SCHEMA,
                "kind": CODECOMPASS_DOCUMENTS,
                "embedding": self._delegated_embedding_config(provider_config),
                "embedding_text_profile": self.embedding_text_profile,
            }
        )
        intent = {
            "scope": self._trusted_scope.as_dict(),
            "manifest_hash": compatibility.manifest_hash,
            "config_hash": compatibility.config_hash,
            "record_count": len(serialized_documents),
            "input_sha256": content_sha256,
        }
        idempotency_key = (
            "codecompass-refresh-"
            + hashlib.sha256(
                json.dumps(
                    intent,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:32]
        )
        return self._index_task_service.submit(
            operation="refresh",
            trusted_scope=VectorIndexTrustedScope(
                workspace_id=self._trusted_scope.workspace_id,
                repository_id=self._trusted_scope.repository_id,
                profile_name=self._trusted_scope.profile_name,
                domain=self._trusted_scope.domain,
            ),
            idempotency_key=idempotency_key,
            payload={
                "input_ref": input_ref.to_dict(),
                "preparation": preparation.to_dict(),
                "compatibility": compatibility.as_dict(),
                "batch_size": 128,
            },
            actor=self._index_task_actor,
            priority="medium",
        )

    @classmethod
    def _delegated_document(
        cls,
        document: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "record_id": str(document.get("record_id") or ""),
            "kind": str(document.get("kind") or ""),
            "file": str(document.get("file") or ""),
            "parent_id": str(document.get("parent_id") or ""),
            "role_labels": [str(item) for item in list(document.get("role_labels") or ())],
            "importance_score": float(document.get("importance_score") or 0.0),
            "source_scope": str(document.get("source_scope") or "repo"),
            "profile_name": str(document.get("profile_name") or "default"),
            "manifest_hash": str(document.get("manifest_hash") or ""),
            "embedding_text": str(document.get("embedding_text") or ""),
            "source_hash": cls._source_hash(document),
        }

    @staticmethod
    def _delegated_embedding_config(
        provider_config: Any,
    ) -> dict[str, Any]:
        if str(getattr(provider_config, "_resolved_api_key", "") or "").strip():
            raise VectorStoreError("vector_index_embedding_plaintext_secret_forbidden")
        result: dict[str, Any] = {
            "provider": str(getattr(provider_config, "provider", "local_hash")),
            "model_version": str(getattr(provider_config, "model_version", "hash-v1")),
            "dimensions": int(getattr(provider_config, "dimensions", 0) or 0),
            "timeout_seconds": int(getattr(provider_config, "timeout_seconds", 20) or 20),
            "external_calls_allowed": bool(
                getattr(
                    provider_config,
                    "external_calls_allowed",
                    False,
                )
            ),
            "allowed_base_urls": list(getattr(provider_config, "allowed_base_urls", ()) or ()),
        }
        for field in (
            "policy_profile",
            "model",
            "base_url",
            "api_key_ref",
        ):
            value = getattr(provider_config, field, None)
            if value:
                result[field] = str(value)
        return result

    def _submit_refresh_task(
        self,
        *,
        points: list[PreparedVectorPoint],
        compatibility: CompatibilitySpec,
    ) -> dict[str, Any]:
        from agent.services.vector_index_task_service import (
            VectorIndexTrustedScope,
        )

        serialized_points = [
            {
                "record_id": point.record_id,
                "point_id": point.point_id,
                "vector": list(point.vector),
                "payload": dict(point.payload),
                "source_hash": point.source_hash,
            }
            for point in points
        ]
        intent = {
            "scope": self._trusted_scope.as_dict(),
            "manifest_hash": compatibility.manifest_hash,
            "config_hash": compatibility.config_hash,
            "record_count": len(points),
            "points_hash": hashlib.sha256(
                json.dumps(
                    [
                        {
                            "record_id": point.record_id,
                            "source_hash": point.source_hash,
                        }
                        for point in points
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        idempotency_key = (
            "codecompass-refresh-"
            + hashlib.sha256(
                json.dumps(
                    intent,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:32]
        )
        task_payload: dict[str, Any] = {
            "compatibility": compatibility.as_dict(),
            "batch_size": 128,
        }
        if len(serialized_points) <= 1000:
            task_payload["points"] = serialized_points
        else:
            if self._index_input_publisher is None:
                raise VectorStoreError("vector_index_input_publisher_required")
            content = json.dumps(
                {"points": serialized_points},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            content_sha256 = hashlib.sha256(content).hexdigest()
            published = self._index_input_publisher.publish(
                scope=self._trusted_scope,
                content=content,
                content_sha256=content_sha256,
            )
            if not isinstance(published, Mapping):
                raise VectorStoreError("vector_index_input_publisher_result_invalid")
            try:
                input_ref = VectorIndexInputReference.from_mapping(
                    published,
                    require_sha256=True,
                    require_scope_fingerprint=True,
                )
            except VectorIndexInputError as exc:
                raise VectorStoreError(exc.reason) from exc
            if input_ref.sha256 != content_sha256:
                raise VectorStoreError("vector_index_input_publisher_digest_mismatch")
            try:
                input_ref.validate_binding(self._trusted_scope)
            except VectorIndexInputError as exc:
                raise VectorStoreError(exc.reason) from exc
            task_payload["input_ref"] = input_ref.to_dict()
        return self._index_task_service.submit(
            operation="refresh",
            trusted_scope=VectorIndexTrustedScope(
                workspace_id=self._trusted_scope.workspace_id,
                repository_id=self._trusted_scope.repository_id,
                profile_name=self._trusted_scope.profile_name,
                domain=self._trusted_scope.domain,
            ),
            idempotency_key=idempotency_key,
            payload=task_payload,
            actor=self._index_task_actor,
            priority="medium",
        )

    def _compatibility(
        self,
        *,
        provider: Any,
        provider_config: Any,
        manifest: Mapping[str, Any],
    ) -> CompatibilitySpec:
        return CompatibilitySpec(
            dimensions=int(getattr(provider, "dimensions", 0) or 0),
            distance="cosine",
            provider=str(getattr(provider, "provider_id", "unknown") or "unknown"),
            model=str(getattr(provider, "model_version", "unknown") or "unknown"),
            profile=self.embedding_text_profile,
            encoding=self._vector_encoder.profile.config_hash(),
            config_hash=str(provider_config.config_hash()),
            schema_version="codecompass_vector_index.v2",
            manifest_hash=str(manifest.get("manifest_hash") or ""),
        )

    @staticmethod
    def _source_hash(document: Mapping[str, Any]) -> str:
        existing = str(document.get("source_hash") or document.get("content_hash") or "").strip()
        if existing:
            return existing
        return hashlib.sha256(
            json.dumps(
                dict(document),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()

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

    @staticmethod
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
