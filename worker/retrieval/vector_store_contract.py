from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


class VectorStoreError(RuntimeError):
    """Stable, secret-free failure raised by vector-store implementations."""

    def __init__(self, reason: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.reason = str(reason or "vector_store_error")
        self.details = dict(details or {})
        super().__init__(self.reason)


class VectorStoreDimensionsMismatch(VectorStoreError):
    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(
            "dimensions_mismatch",
            details={"expected_dimensions": int(expected), "actual_dimensions": int(actual)},
        )


class VectorStoreClosedError(VectorStoreError):
    def __init__(self) -> None:
        super().__init__("vector_store_closed")


def _required_identifier(value: str, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"missing_{field_name}")
    if len(clean) > 256 or any(ord(char) < 32 for char in clean):
        raise ValueError(f"invalid_{field_name}")
    return clean


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    if not clean:
        return None
    if len(clean) > 1024 or any(ord(char) < 32 for char in clean):
        raise ValueError(f"invalid_{field_name}")
    return clean


@dataclass(frozen=True, slots=True)
class VectorScope:
    workspace_id: str
    repository_id: str
    profile_name: str = "default"
    domain: str = "codecompass"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_id", _required_identifier(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "repository_id", _required_identifier(self.repository_id, "repository_id"))
        object.__setattr__(self, "profile_name", _required_identifier(self.profile_name, "profile_name"))
        object.__setattr__(self, "domain", _required_identifier(self.domain, "domain"))

    def as_dict(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "repository_id": self.repository_id,
            "profile_name": self.profile_name,
            "domain": self.domain,
        }


@dataclass(frozen=True, slots=True)
class VectorStoreFilters:
    source_scope: str | None = None
    profile_name: str | None = None
    kinds: tuple[str, ...] = ()
    file_prefix: str | None = None
    role_labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_scope", _optional_text(self.source_scope, "source_scope"))
        object.__setattr__(self, "profile_name", _optional_text(self.profile_name, "profile_name"))
        object.__setattr__(self, "file_prefix", _optional_text(self.file_prefix, "file_prefix"))
        object.__setattr__(
            self,
            "kinds",
            tuple(_required_identifier(item, "kind") for item in self.kinds),
        )
        object.__setattr__(
            self,
            "role_labels",
            tuple(_required_identifier(item, "role_label") for item in self.role_labels),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_scope": self.source_scope,
            "profile_name": self.profile_name,
            "kinds": list(self.kinds),
            "file_prefix": self.file_prefix,
            "role_labels": list(self.role_labels),
        }


@dataclass(frozen=True, slots=True)
class CompatibilitySpec:
    dimensions: int
    distance: str = "cosine"
    provider: str = ""
    model: str = ""
    profile: str = ""
    encoding: str = "float32"
    config_hash: str = ""
    schema_version: str = "vector_store.v1"
    manifest_hash: str = ""

    def __post_init__(self) -> None:
        dimensions = int(self.dimensions)
        if dimensions <= 0:
            raise ValueError("invalid_vector_dimensions")
        distance = str(self.distance or "cosine").strip().lower()
        if distance not in {"cosine", "dot", "euclid"}:
            raise ValueError(f"unsupported_vector_distance:{distance}")
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "distance", distance)
        object.__setattr__(self, "provider", str(self.provider or "").strip())
        object.__setattr__(self, "model", str(self.model or "").strip())
        object.__setattr__(self, "profile", str(self.profile or "").strip())
        object.__setattr__(self, "encoding", str(self.encoding or "float32").strip())
        object.__setattr__(self, "config_hash", str(self.config_hash or "").strip())
        object.__setattr__(
            self,
            "schema_version",
            _required_identifier(self.schema_version, "schema_version"),
        )
        object.__setattr__(self, "manifest_hash", str(self.manifest_hash or "").strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimensions": self.dimensions,
            "distance": self.distance,
            "provider": self.provider,
            "model": self.model,
            "profile": self.profile,
            "encoding": self.encoding,
            "config_hash": self.config_hash,
            "schema_version": self.schema_version,
            "manifest_hash": self.manifest_hash,
        }


@dataclass(frozen=True, slots=True)
class PreparedVectorPoint:
    record_id: str
    vector: tuple[float, ...]
    scope: VectorScope
    payload: Mapping[str, Any]
    source_hash: str = ""
    point_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _required_identifier(self.record_id, "record_id"))
        vector = tuple(float(value) for value in self.vector)
        if not vector:
            raise ValueError("empty_vector")
        if any(not math.isfinite(value) for value in vector):
            raise ValueError("non_finite_vector_value")
        object.__setattr__(self, "vector", vector)
        object.__setattr__(self, "payload", dict(self.payload or {}))
        object.__setattr__(self, "source_hash", str(self.source_hash or "").strip())
        if self.point_id is not None:
            object.__setattr__(self, "point_id", _required_identifier(self.point_id, "point_id"))


@dataclass(frozen=True, slots=True)
class VectorSearchQuery:
    query_vector: tuple[float, ...]
    top_k: int = 10
    scope: VectorScope | None = None
    filters: VectorStoreFilters | None = None

    def __post_init__(self) -> None:
        vector = tuple(float(value) for value in self.query_vector)
        if not vector:
            raise ValueError("empty_query_vector")
        if any(not math.isfinite(value) for value in vector):
            raise ValueError("non_finite_query_vector")
        top_k = int(self.top_k)
        if top_k <= 0:
            raise ValueError("invalid_top_k")
        object.__setattr__(self, "query_vector", vector)
        object.__setattr__(self, "top_k", top_k)


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    record_id: str
    score: float
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _required_identifier(self.record_id, "record_id"))
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "payload", dict(self.payload or {}))

    def as_dict(self) -> dict[str, Any]:
        return {
            **dict(self.payload),
            "record_id": self.record_id,
            "vector_score": self.score,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    hits: tuple[VectorSearchHit, ...]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    requested_provider: str = ""
    effective_provider: str = ""
    provider_fallback: bool = False
    reason: str = "ok"

    def __post_init__(self) -> None:
        object.__setattr__(self, "hits", tuple(self.hits))
        object.__setattr__(self, "diagnostics", dict(self.diagnostics or {}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": [hit.as_dict() for hit in self.hits],
            "diagnostics": dict(self.diagnostics),
            "requested_provider": self.requested_provider,
            "effective_provider": self.effective_provider,
            "provider_fallback": bool(self.provider_fallback),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class IndexWriteResult:
    status: str
    mode: str
    reason: str
    indexed_documents: int
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    upserted: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", dict(self.diagnostics or {}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "reason": self.reason,
            "indexed_documents": int(self.indexed_documents),
            "diagnostics": dict(self.diagnostics),
            "upserted": int(self.upserted),
            "deleted": int(self.deleted),
            "skipped": int(self.skipped),
            "failed": int(self.failed),
        }


@dataclass(frozen=True, slots=True)
class VectorStoreDiagnostic:
    status: str
    reason: str
    provider: str
    backend_version: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", dict(self.details or {}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "provider": self.provider,
            "backend_version": self.backend_version,
            **dict(self.details),
        }


@runtime_checkable
class VectorSearchPort(Protocol):
    def search_by_vector(self, query: VectorSearchQuery) -> VectorSearchResult: ...


@runtime_checkable
class VectorIndexWriter(Protocol):
    def rebuild(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult: ...

    def refresh(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult: ...

    def upsert(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        batch_size: int = 128,
    ) -> IndexWriteResult: ...

    def delete(
        self,
        point_ids: Sequence[str],
        *,
        scope: VectorScope,
    ) -> IndexWriteResult: ...


@runtime_checkable
class VectorStoreDiagnosticsPort(Protocol):
    def diagnostics(self) -> VectorStoreDiagnostic: ...


@runtime_checkable
class VectorStoreLifecycle(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class VectorStore(
    VectorSearchPort,
    VectorIndexWriter,
    VectorStoreDiagnosticsPort,
    VectorStoreLifecycle,
    Protocol,
):
    pass


__all__ = [
    "CompatibilitySpec",
    "IndexWriteResult",
    "PreparedVectorPoint",
    "VectorIndexWriter",
    "VectorScope",
    "VectorSearchHit",
    "VectorSearchPort",
    "VectorSearchQuery",
    "VectorSearchResult",
    "VectorStore",
    "VectorStoreClosedError",
    "VectorStoreDiagnostic",
    "VectorStoreDiagnosticsPort",
    "VectorStoreDimensionsMismatch",
    "VectorStoreError",
    "VectorStoreFilters",
    "VectorStoreLifecycle",
]
