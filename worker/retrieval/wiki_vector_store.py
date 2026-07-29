"""Typed Wiki adapter over the shared JSON/Qdrant vector-store ports."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from worker.retrieval.embedding_provider import EmbeddingProvider
from worker.retrieval.vector_store_config import (
    AvailabilityPolicy,
    JsonVectorStoreConfig,
    QdrantVectorStoreConfig,
    VectorStoreConfig,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStore,
    VectorStoreFilters,
)

WIKI_VECTOR_PAYLOAD_SCHEMA = "ananta.wiki_vector_payload.v1"
WIKI_VECTOR_DOMAIN = "wiki"
WIKI_EMBEDDING_PROFILE = "wiki_embedding_text.v1"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SENSITIVE_FIELDS = frozenset(
    {"api_key", "authorization", "embedding", "vector", "embedding_text"}
)
_RESERVED_PAYLOAD_FIELDS = frozenset(
    {
        "record_id",
        "workspace_id",
        "repository_id",
        "profile_name",
        "domain",
        "payload_schema",
        "source_scope",
    }
)


@dataclass(frozen=True, slots=True)
class WikiVectorStoreConfig:
    provider: str = "json"
    qdrant_enabled: bool = False
    collection_prefix: str = "ananta-wiki"
    workspace_id: str = "wiki-local"
    source_id: str = "wiki"
    profile_name: str = "default"
    retrieval_cache_state: str = ""
    manifest_hash: str = ""
    qdrant: QdrantVectorStoreConfig | None = None
    availability: AvailabilityPolicy = field(
        default_factory=AvailabilityPolicy
    )
    json: JsonVectorStoreConfig = field(
        default_factory=lambda: JsonVectorStoreConfig(
            index_path=Path(".rag/wiki/vector_index.json")
        )
    )
    expected_compatibility: CompatibilitySpec | None = None

    def __post_init__(self) -> None:
        provider = str(self.provider or "").strip().lower()
        if type(self.qdrant_enabled) is not bool:
            raise ValueError("wiki_qdrant_enabled_invalid")
        if provider not in {"json", "qdrant"}:
            raise ValueError("wiki_vector_provider_invalid")
        if provider == "qdrant" and not self.qdrant_enabled:
            raise ValueError("wiki_qdrant_explicit_opt_in_required")
        if provider == "qdrant" and self.qdrant is None:
            raise ValueError("wiki_qdrant_config_required")
        for field_name in (
            "collection_prefix",
            "workspace_id",
            "source_id",
            "profile_name",
        ):
            if _NAME.fullmatch(str(getattr(self, field_name) or "")) is None:
                raise ValueError(f"wiki_vector_{field_name}_invalid")
        if not self.collection_prefix.startswith("ananta-wiki"):
            raise ValueError("wiki_vector_collection_prefix_must_be_separate")
        if self.qdrant is not None:
            if not self.qdrant.collection_prefix.startswith("ananta-wiki"):
                raise ValueError("wiki_qdrant_collection_prefix_must_be_separate")
            if self.qdrant.collection_prefix != self.collection_prefix:
                raise ValueError("wiki_qdrant_collection_prefix_mismatch")
        if not isinstance(self.availability, AvailabilityPolicy):
            raise ValueError("wiki_vector_availability_policy_invalid")
        if not isinstance(self.json, JsonVectorStoreConfig):
            raise ValueError("wiki_vector_json_config_invalid")
        if (
            self.expected_compatibility is not None
            and not isinstance(
                self.expected_compatibility,
                CompatibilitySpec,
            )
        ):
            raise ValueError("wiki_vector_compatibility_invalid")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(
            self,
            "retrieval_cache_state",
            str(self.retrieval_cache_state or "").strip(),
        )
        object.__setattr__(
            self,
            "manifest_hash",
            str(self.manifest_hash or "").strip(),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "WikiVectorStoreConfig":
        payload = dict(value or {})
        allowed_fields = {
            "provider",
            "qdrant_enabled",
            "collection_prefix",
            "workspace_id",
            "source_id",
            "profile_name",
            "retrieval_cache_state",
            "manifest_hash",
            "expected_compatibility",
            "qdrant",
            "availability",
            "json",
        }
        unknown_fields = sorted(set(payload) - allowed_fields)
        if unknown_fields:
            raise ValueError(
                f"wiki_vector_config_fields_unknown:{','.join(unknown_fields)}"
            )
        qdrant_payload = payload.get("qdrant")
        qdrant = (
            qdrant_payload
            if isinstance(qdrant_payload, QdrantVectorStoreConfig)
            else (
                QdrantVectorStoreConfig.from_mapping(dict(qdrant_payload))
                if isinstance(qdrant_payload, Mapping)
                else None
            )
        )
        compatibility_payload = payload.get(
            "expected_compatibility"
        )
        expected_compatibility = (
            compatibility_payload
            if isinstance(
                compatibility_payload,
                CompatibilitySpec,
            )
            else (
                CompatibilitySpec(**dict(compatibility_payload))
                if isinstance(compatibility_payload, Mapping)
                else None
            )
        )
        availability_payload = payload.get("availability")
        availability = (
            availability_payload
            if isinstance(availability_payload, AvailabilityPolicy)
            else VectorStoreConfig.from_mapping(
                {
                    "provider": "json",
                    "availability": dict(availability_payload or {}),
                }
            ).availability
        )
        json_payload = payload.get("json")
        json_config = (
            json_payload
            if isinstance(json_payload, JsonVectorStoreConfig)
            else JsonVectorStoreConfig(
                index_path=Path(
                    dict(json_payload or {}).get("index_path")
                    or ".rag/wiki/vector_index.json"
                )
            )
        )
        return cls(
            provider=str(payload.get("provider") or "json"),
            qdrant_enabled=payload.get("qdrant_enabled", False),
            collection_prefix=str(
                payload.get("collection_prefix")
                or (qdrant.collection_prefix if qdrant else "ananta-wiki")
            ),
            workspace_id=str(payload.get("workspace_id") or "wiki-local"),
            source_id=str(payload.get("source_id") or "wiki"),
            profile_name=str(payload.get("profile_name") or "default"),
            retrieval_cache_state=str(payload.get("retrieval_cache_state") or ""),
            manifest_hash=str(payload.get("manifest_hash") or ""),
            expected_compatibility=expected_compatibility,
            qdrant=qdrant,
            availability=availability,
            json=json_config,
        )

    def vector_scope(self) -> VectorScope:
        return VectorScope(
            workspace_id=self.workspace_id,
            repository_id=self.source_id,
            profile_name=self.profile_name,
            domain=WIKI_VECTOR_DOMAIN,
        )

    def collection_scope(self) -> str:
        digest = hashlib.sha256(
            (
                f"{self.workspace_id}:{self.source_id}:"
                f"{self.profile_name}:{WIKI_VECTOR_DOMAIN}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"{self.collection_prefix}-{digest}"


@dataclass(frozen=True, slots=True)
class WikiVectorPayload:
    record_id: str
    embedding_text: str
    kind: str
    file: str
    parent_id: str
    role_labels: tuple[str, ...]
    importance_score: float
    source_scope: str
    workspace_id: str
    repository_id: str
    profile_name: str
    domain: str = WIKI_VECTOR_DOMAIN
    payload_schema: str = WIKI_VECTOR_PAYLOAD_SCHEMA
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_store_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file": self.file,
            "parent_id": self.parent_id,
            "role_labels": list(self.role_labels),
            "importance_score": self.importance_score,
            "source_scope": self.source_scope,
            "metadata": {
                **dict(self.metadata),
                "payload_schema": self.payload_schema,
            },
        }

    def source_hash(self, manifest_hash: str) -> str:
        canonical = json.dumps(
            {
                "record_id": self.record_id,
                "embedding_text": self.embedding_text,
                "payload": self.as_store_payload(),
                "manifest_hash": str(manifest_hash or ""),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WikiVectorPayloadAdapter:
    """Validate external Wiki records and produce a versioned typed payload."""

    def __init__(self, config: WikiVectorStoreConfig) -> None:
        self._config = config

    def adapt(self, record: Mapping[str, Any]) -> WikiVectorPayload:
        provenance = dict(record.get("_provenance") or {})
        record_id = str(record.get("record_id") or provenance.get("record_id") or "").strip()
        if not record_id:
            raise ValueError("wiki_vector_record_id_required")
        embedding_text = str(record.get("embedding_text") or "").strip()
        if not embedding_text:
            raise ValueError("wiki_vector_embedding_text_required")
        source_scope = str(record.get("source_scope") or "wiki").strip().lower()
        if source_scope != WIKI_VECTOR_DOMAIN:
            raise ValueError("wiki_vector_source_scope_invalid")
        known = {
            *_RESERVED_PAYLOAD_FIELDS,
            "embedding_text",
            "kind",
            "file",
            "parent_id",
            "role_labels",
            "importance_score",
            "source_scope",
            "_provenance",
        }
        metadata = {
            str(key): value
            for key, value in record.items()
            if key not in known and str(key).lower() not in _SENSITIVE_FIELDS
        }
        return WikiVectorPayload(
            record_id=record_id,
            embedding_text=embedding_text,
            kind=str(record.get("kind") or "wiki_section_chunk"),
            file=str(record.get("file") or ""),
            parent_id=str(record.get("parent_id") or ""),
            role_labels=tuple(
                str(item)
                for item in list(record.get("role_labels") or ())
                if str(item).strip()
            ),
            importance_score=float(record.get("importance_score") or 0.0),
            source_scope=WIKI_VECTOR_DOMAIN,
            workspace_id=self._config.workspace_id,
            repository_id=self._config.source_id,
            profile_name=self._config.profile_name,
            metadata=metadata,
        )


class WikiVectorBackend(Protocol):
    def rebuild(
        self,
        documents: Sequence[WikiVectorPayload],
        embedding_provider: EmbeddingProvider,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> IndexWriteResult: ...

    def refresh(
        self,
        documents: Sequence[WikiVectorPayload],
        embedding_provider: EmbeddingProvider,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> IndexWriteResult: ...

    def search(
        self,
        query: str,
        embedding_provider: EmbeddingProvider,
        top_k: int,
    ) -> VectorSearchResult: ...

    def delete(self, record_ids: Sequence[str]) -> IndexWriteResult: ...

    def close(self) -> None: ...


class WikiVectorStoreFactory(Protocol):
    def create(
        self,
        config: VectorStoreConfig,
        *,
        secret_resolver: Any = None,
        observer: Any = None,
    ) -> VectorStore: ...


class WikiPreparedVectorBackend:
    """Wiki-specific embedding/payload adapter over a shared VectorStore."""

    def __init__(self, store: VectorStore, config: WikiVectorStoreConfig) -> None:
        self._store = store
        self._config = config
        self._scope = config.vector_scope()
        self._last_compatibility: CompatibilitySpec | None = None

    def rebuild(
        self,
        documents: Sequence[WikiVectorPayload],
        embedding_provider: EmbeddingProvider,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> IndexWriteResult:
        points, compatibility = self._prepare(
            documents,
            embedding_provider,
            retrieval_cache_state,
            manifest_hash,
        )
        self._last_compatibility = compatibility
        return self._store.rebuild(points, compatibility=compatibility)

    def refresh(
        self,
        documents: Sequence[WikiVectorPayload],
        embedding_provider: EmbeddingProvider,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> IndexWriteResult:
        points, compatibility = self._prepare(
            documents,
            embedding_provider,
            retrieval_cache_state,
            manifest_hash,
        )
        self._last_compatibility = compatibility
        return self._store.refresh(points, compatibility=compatibility)

    def search(
        self,
        query: str,
        embedding_provider: EmbeddingProvider,
        top_k: int,
    ) -> VectorSearchResult:
        vectors = embedding_provider.embed_texts([str(query or "")])
        if len(vectors) != 1:
            raise ValueError("wiki_embedding_response_size_mismatch")
        compatibility = self._search_compatibility(embedding_provider)
        return self._store.search_by_vector(
            VectorSearchQuery(
                query_vector=tuple(float(item) for item in vectors[0]),
                top_k=int(top_k),
                scope=self._scope,
                filters=VectorStoreFilters(
                    source_scope=WIKI_VECTOR_DOMAIN,
                    profile_name=self._config.profile_name,
                ),
                compatibility=compatibility,
            )
        )

    def delete(self, record_ids: Sequence[str]) -> IndexWriteResult:
        return self._store.delete(tuple(str(item) for item in record_ids), scope=self._scope)

    def close(self) -> None:
        self._store.close()

    def _prepare(
        self,
        documents: Sequence[WikiVectorPayload],
        embedding_provider: EmbeddingProvider,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> tuple[tuple[PreparedVectorPoint, ...], CompatibilitySpec]:
        rows = tuple(documents)
        vectors = embedding_provider.embed_texts([row.embedding_text for row in rows])
        if len(vectors) != len(rows):
            raise ValueError("wiki_embedding_response_size_mismatch")
        dimensions = int(getattr(embedding_provider, "dimensions", 0) or 0)
        if dimensions <= 0:
            raise ValueError("wiki_embedding_dimensions_invalid")
        points = tuple(
            PreparedVectorPoint(
                record_id=row.record_id,
                vector=tuple(float(item) for item in vector),
                scope=self._scope,
                payload=row.as_store_payload(),
                source_hash=row.source_hash(manifest_hash),
            )
            for row, vector in zip(rows, vectors, strict=True)
        )
        if any(len(point.vector) != dimensions for point in points):
            raise ValueError("wiki_embedding_dimensions_mismatch")
        return points, self._compatibility(
            embedding_provider,
            retrieval_cache_state=retrieval_cache_state,
            manifest_hash=manifest_hash,
        )

    @staticmethod
    def _compatibility(
        embedding_provider: EmbeddingProvider,
        *,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> CompatibilitySpec:
        dimensions = int(getattr(embedding_provider, "dimensions", 0) or 0)
        if dimensions <= 0:
            raise ValueError("wiki_embedding_dimensions_invalid")
        return CompatibilitySpec(
            dimensions=dimensions,
            distance="cosine",
            provider=str(getattr(embedding_provider, "provider_id", "unknown")),
            model=str(getattr(embedding_provider, "model_version", "unknown")),
            profile=WIKI_EMBEDDING_PROFILE,
            encoding="float32",
            config_hash=hashlib.sha256(
                str(retrieval_cache_state or "").encode("utf-8")
            ).hexdigest()[:24],
            schema_version=WIKI_VECTOR_PAYLOAD_SCHEMA,
            manifest_hash=str(manifest_hash or ""),
        )

    def _search_compatibility(
        self,
        embedding_provider: EmbeddingProvider,
    ) -> CompatibilitySpec:
        expected = self._config.expected_compatibility
        if expected is not None:
            current_dimensions = int(
                getattr(embedding_provider, "dimensions", 0) or 0
            )
            current_provider = str(
                getattr(embedding_provider, "provider_id", "unknown")
            )
            current_model = str(
                getattr(
                    embedding_provider,
                    "model_version",
                    "unknown",
                )
            )
            if expected.dimensions != current_dimensions:
                raise ValueError("dimensions_mismatch")
            if expected.provider != current_provider:
                raise ValueError("provider_changed")
            if expected.model != current_model:
                raise ValueError("model_changed")
            if expected.profile != WIKI_EMBEDDING_PROFILE:
                raise ValueError("profile_changed")
            if expected.schema_version != WIKI_VECTOR_PAYLOAD_SCHEMA:
                raise ValueError("migration_required")
            return expected
        configured = bool(
            self._config.retrieval_cache_state and self._config.manifest_hash
        )
        if configured:
            return self._compatibility(
                embedding_provider,
                retrieval_cache_state=self._config.retrieval_cache_state,
                manifest_hash=self._config.manifest_hash,
            )
        current_dimensions = int(
            getattr(embedding_provider, "dimensions", 0) or 0
        )
        current_provider = str(
            getattr(embedding_provider, "provider_id", "unknown")
        )
        current_model = str(
            getattr(embedding_provider, "model_version", "unknown")
        )
        known = self._last_compatibility
        if (
            known is not None
            and known.dimensions == current_dimensions
            and known.provider == current_provider
            and known.model == current_model
        ):
            return known
        return self._compatibility(
            embedding_provider,
            retrieval_cache_state=self._config.retrieval_cache_state,
            manifest_hash=self._config.manifest_hash,
        )


class WikiVectorStore:
    """Backward-compatible Wiki facade with explicit typed backend composition."""

    def __init__(
        self,
        *,
        index_path: str | Path | None = None,
        backend: WikiVectorBackend | None = None,
        config: WikiVectorStoreConfig | None = None,
        secret_resolver: Any = None,
        observer: Any = None,
        store_factory: WikiVectorStoreFactory | None = None,
    ) -> None:
        self.config = config or WikiVectorStoreConfig()
        if store_factory is None:
            from worker.retrieval.vector_store_factory import (
                VectorStoreFactory,
            )

            store_factory = VectorStoreFactory()
        self._store_factory = store_factory
        if backend is None:
            backend = WikiPreparedVectorBackend(
                self._build_store(
                    index_path=index_path,
                    secret_resolver=secret_resolver,
                    observer=observer,
                ),
                self.config,
            )
        self._backend = backend
        self._payload_adapter = WikiVectorPayloadAdapter(self.config)

    def rebuild(
        self,
        *,
        documents: list[dict[str, Any]],
        embedding_provider: EmbeddingProvider,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        result = self._backend.rebuild(
            tuple(self._payload_adapter.adapt(item) for item in documents),
            embedding_provider,
            retrieval_cache_state,
            manifest_hash,
        )
        return result.as_dict()

    def refresh(
        self,
        *,
        documents: list[dict[str, Any]],
        embedding_provider: EmbeddingProvider,
        retrieval_cache_state: str,
        manifest_hash: str,
    ) -> Mapping[str, Any]:
        result = self._backend.refresh(
            tuple(self._payload_adapter.adapt(item) for item in documents),
            embedding_provider,
            retrieval_cache_state,
            manifest_hash,
        )
        return result.as_dict()

    def search(
        self,
        *,
        query: str,
        embedding_provider: EmbeddingProvider,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        if embedding_provider is None:
            raise ValueError("wiki_embedding_provider_required")
        result = self._backend.search(query, embedding_provider, top_k)
        rows: list[dict[str, Any]] = []
        for hit in result.hits:
            payload = hit.as_dict()
            metadata = dict(payload.pop("metadata", {}) or {})
            payload.update(
                {
                    key: value
                    for key, value in metadata.items()
                    if str(key).lower()
                    not in _SENSITIVE_FIELDS | _RESERVED_PAYLOAD_FIELDS
                }
            )
            payload["payload_schema"] = WIKI_VECTOR_PAYLOAD_SCHEMA
            payload["domain"] = WIKI_VECTOR_DOMAIN
            payload.setdefault("id", hit.record_id)
            payload.setdefault("chunk_id", hit.record_id)
            rows.append(payload)
        return rows

    def delete(self, *, record_ids: Sequence[str]) -> Mapping[str, Any]:
        return self._backend.delete(record_ids).as_dict()

    def close(self) -> None:
        self._backend.close()

    def _build_store(
        self,
        *,
        index_path: str | Path | None,
        secret_resolver: Any,
        observer: Any,
    ) -> VectorStore:
        if self.config.provider == "json":
            if index_path is None:
                raise ValueError("wiki_vector_index_path_required")
            json_config = JsonVectorStoreConfig(
                index_path=Path(index_path)
            )
        else:
            json_config = (
                JsonVectorStoreConfig(index_path=Path(index_path))
                if index_path is not None
                else self.config.json
            )
        if self.config.qdrant is None:
            if self.config.provider == "qdrant":
                raise ValueError("wiki_qdrant_config_required")
        common = VectorStoreConfig(
            provider=self.config.provider,
            availability=self.config.availability,
            json=json_config,
            qdrant=self.config.qdrant,
        )
        return self._store_factory.create(
            common,
            secret_resolver=secret_resolver,
            observer=observer,
        )


__all__ = [
    "WIKI_EMBEDDING_PROFILE",
    "WIKI_VECTOR_DOMAIN",
    "WIKI_VECTOR_PAYLOAD_SCHEMA",
    "WikiPreparedVectorBackend",
    "WikiVectorBackend",
    "WikiVectorPayload",
    "WikiVectorPayloadAdapter",
    "WikiVectorStore",
    "WikiVectorStoreConfig",
    "WikiVectorStoreFactory",
]
