"""Wiki vector-index mutation strategies for local and delegated backends."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.embedding_provider_config_service import (
    EmbeddingProviderConfigService,
)
from worker.retrieval.embedding_provider import HashEmbeddingProvider
from worker.retrieval.vector_index_input_loader import (
    VectorIndexInputError,
    VectorIndexInputReference,
)
from worker.retrieval.vector_index_preparation import (
    VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA,
    VECTOR_INDEX_PREPARATION_SCHEMA,
    WIKI_DOCUMENTS,
    VectorIndexPreparationSpec,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    VectorScope,
    VectorStoreError,
)
from worker.retrieval.wiki_vector_store import (
    WIKI_EMBEDDING_PROFILE,
    WIKI_VECTOR_PAYLOAD_SCHEMA,
    WikiVectorPayloadAdapter,
    WikiVectorStoreConfig,
)


class WikiVectorMutationPort(Protocol):
    """Small mutation capability consumed by Wiki retrieval composition."""

    def refresh(
        self,
        *,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]: ...

    def rebuild(
        self,
        *,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]: ...

    def delete(self, *, record_ids: list[str]) -> Mapping[str, Any]: ...


class WikiVectorStoreMutationPort(Protocol):
    """Legacy/local Wiki store surface required by the local strategy."""

    def refresh(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def rebuild(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def delete(self, **kwargs: Any) -> Mapping[str, Any]: ...


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


class LocalWikiVectorMutationService:
    """Apply JSON-backed Wiki mutations without queue or Qdrant policy."""

    def __init__(
        self,
        *,
        vector_store: WikiVectorStoreMutationPort,
        embedding_provider: Any,
    ) -> None:
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider

    def refresh(
        self,
        *,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        return self._write(
            operation="refresh",
            documents=documents,
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
        )

    def rebuild(
        self,
        *,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        return self._write(
            operation="rebuild",
            documents=documents,
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
        )

    def delete(self, *, record_ids: list[str]) -> Mapping[str, Any]:
        return self._vector_store.delete(record_ids=record_ids)

    def _write(
        self,
        *,
        operation: str,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        method = getattr(self._vector_store, operation)
        return method(
            documents=documents,
            embedding_provider=self._embedding_provider,
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
        )


class DelegatedWikiVectorMutationService:
    """Publish Wiki source documents and submit immutable Hub-owned tasks."""

    def __init__(
        self,
        *,
        vector_config: WikiVectorStoreConfig,
        embedding_provider: Any,
        index_task_service: VectorIndexTaskSubmissionPort,
        index_input_publisher: VectorIndexInputPublisherPort,
        embedding_provider_config: Mapping[str, Any] | None = None,
        index_task_actor: str = "wiki-vector-service",
    ) -> None:
        if vector_config.provider != "qdrant":
            raise ValueError("wiki_vector_delegated_backend_required")
        self._vector_config = vector_config
        self._embedding_provider = embedding_provider
        self._index_task_service = index_task_service
        self._index_input_publisher = index_input_publisher
        self._embedding_provider_config = dict(embedding_provider_config or {})
        self._index_task_actor = str(index_task_actor or "wiki-vector-service")

    def refresh(
        self,
        *,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        return self._write_documents(
            operation="refresh",
            documents=documents,
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
        )

    def rebuild(
        self,
        *,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        return self._write_documents(
            operation="rebuild",
            documents=documents,
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
        )

    def delete(self, *, record_ids: list[str]) -> Mapping[str, Any]:
        from agent.services.vector_index_task_service import (
            VectorIndexTrustedScope,
        )

        scope = self._vector_config.vector_scope()
        normalized_record_ids = sorted(str(item) for item in record_ids)
        intent = {
            "scope": scope.as_dict(),
            "record_ids": normalized_record_ids,
        }
        return self._index_task_service.submit(
            operation="delete",
            trusted_scope=VectorIndexTrustedScope(**scope.as_dict()),
            idempotency_key=(
                "wiki-delete-"
                + hashlib.sha256(
                    json.dumps(
                        intent,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()[:32]
            ),
            payload={"point_ids": normalized_record_ids},
            actor=self._index_task_actor,
            priority="medium",
        )

    def _write_documents(
        self,
        *,
        operation: str,
        documents: list[dict[str, Any]],
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        from agent.services.vector_index_task_service import (
            VectorIndexTrustedScope,
        )

        scope = self._vector_config.vector_scope()
        adapted = WikiVectorPayloadAdapter(self._vector_config)
        safe_documents = [self._wiki_document(adapted.adapt(document)) for document in documents]
        if not safe_documents:
            raise VectorStoreError("wiki_vector_documents_required")
        content = json.dumps(
            {
                "schema": VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA,
                "kind": WIKI_DOCUMENTS,
                "documents": safe_documents,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        published = self._index_input_publisher.publish(
            scope=scope,
            content=content,
            content_sha256=digest,
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
        if input_ref.sha256 != digest:
            raise VectorStoreError("vector_index_input_publisher_digest_mismatch")
        try:
            input_ref.validate_binding(scope)
        except VectorIndexInputError as exc:
            raise VectorStoreError(exc.reason) from exc
        provider_config = self._worker_embedding_config()
        preparation = VectorIndexPreparationSpec.from_mapping(
            {
                "schema": VECTOR_INDEX_PREPARATION_SCHEMA,
                "kind": WIKI_DOCUMENTS,
                "embedding": provider_config,
                "embedding_text_profile": WIKI_EMBEDDING_PROFILE,
                "retrieval_cache_state": retrieval_cache_state,
            }
        )
        compatibility = self._compatibility(
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
            provider_config=provider_config,
        )
        intent = {
            "operation": operation,
            "scope": scope.as_dict(),
            "input_sha256": digest,
            "compatibility": compatibility.as_dict(),
        }
        return self._index_task_service.submit(
            operation=operation,
            trusted_scope=VectorIndexTrustedScope(**scope.as_dict()),
            idempotency_key=(
                f"wiki-{operation}-"
                + hashlib.sha256(
                    json.dumps(
                        intent,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()[:32]
            ),
            payload={
                "input_ref": input_ref.to_dict(),
                "preparation": preparation.to_dict(),
                "compatibility": compatibility.as_dict(),
                "batch_size": 128,
            },
            actor=self._index_task_actor,
            priority="medium",
        )

    @staticmethod
    def _wiki_document(document: Any) -> dict[str, Any]:
        return {
            "record_id": document.record_id,
            "embedding_text": document.embedding_text,
            "kind": document.kind,
            "file": document.file,
            "parent_id": document.parent_id,
            "role_labels": list(document.role_labels),
            "importance_score": document.importance_score,
            "source_scope": document.source_scope,
            **dict(document.metadata),
        }

    def _worker_embedding_config(self) -> dict[str, Any]:
        if self._embedding_provider_config:
            service = EmbeddingProviderConfigService(global_config=self._embedding_provider_config)
            config = service.resolve("worker_retrieval")
            if str(config._resolved_api_key or "").strip():
                raise VectorStoreError("vector_index_embedding_plaintext_secret_forbidden")
            result = {
                "provider": config.provider,
                "policy_profile": config.policy_profile,
                "provider_id": str(
                    getattr(
                        self._embedding_provider,
                        "provider_id",
                        ("local_hash" if config.provider in {"local", "local_hash", "hash"} else "openai_compatible"),
                    )
                ),
                "model": config.model,
                "model_version": config.model_version,
                "dimensions": config.dimensions,
                "base_url": config.base_url,
                "api_key_ref": config.api_key_ref,
                "timeout_seconds": config.timeout_seconds,
                "external_calls_allowed": (config.external_calls_allowed),
                "allowed_base_urls": config.allowed_base_urls,
            }
            if (
                int(
                    getattr(
                        self._embedding_provider,
                        "dimensions",
                        config.dimensions,
                    )
                )
                != config.dimensions
                or str(
                    getattr(
                        self._embedding_provider,
                        "model_version",
                        config.model_version,
                    )
                )
                != config.model_version
            ):
                raise VectorStoreError("wiki_vector_embedding_config_mismatch")
            return {key: value for key, value in result.items() if value is not None and value != ""}
        provider_id = str(
            getattr(
                self._embedding_provider,
                "provider_id",
                "wiki_local_hash",
            )
        )
        if not isinstance(
            self._embedding_provider,
            HashEmbeddingProvider,
        ):
            raise VectorStoreError("vector_index_embedding_config_required")
        return {
            "provider": "local_hash",
            "provider_id": provider_id,
            "model_version": str(
                getattr(
                    self._embedding_provider,
                    "model_version",
                    "wiki-hash-v1",
                )
            ),
            "dimensions": int(getattr(self._embedding_provider, "dimensions", 12)),
            "timeout_seconds": 20,
            "external_calls_allowed": False,
            "allowed_base_urls": [],
        }

    def _compatibility(
        self,
        *,
        retrieval_cache_state: str,
        manifest_hash: str,
        provider_config: Mapping[str, Any] | None = None,
    ) -> CompatibilitySpec:
        execution_config = dict(provider_config or {})
        return CompatibilitySpec(
            dimensions=int(
                execution_config.get("dimensions")
                or getattr(
                    self._embedding_provider,
                    "dimensions",
                    0,
                )
                or 0
            ),
            distance="cosine",
            provider=str(
                execution_config.get("provider_id")
                or getattr(
                    self._embedding_provider,
                    "provider_id",
                    "unknown",
                )
            ),
            model=str(
                execution_config.get("model_version")
                or getattr(
                    self._embedding_provider,
                    "model_version",
                    "unknown",
                )
            ),
            profile=WIKI_EMBEDDING_PROFILE,
            encoding="float32",
            config_hash=hashlib.sha256(str(retrieval_cache_state or "").encode("utf-8")).hexdigest()[:24],
            schema_version=WIKI_VECTOR_PAYLOAD_SCHEMA,
            manifest_hash=str(manifest_hash or ""),
        )


__all__ = [
    "DelegatedWikiVectorMutationService",
    "LocalWikiVectorMutationService",
    "VectorIndexInputPublisherPort",
    "VectorIndexTaskSubmissionPort",
    "WikiVectorMutationPort",
]
