"""Runtime-neutral retrieval and provenance contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

RETRIEVAL_RESULT_SCHEMA = "ananta.retrieval_result.v1"


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    tenant_id: str
    scope: str
    allowed_source_ids: frozenset[str]
    max_results: int = 5


@dataclass(frozen=True)
class RetrievedSource:
    source_id: str
    source_version: str
    tenant_id: str
    scope: str
    path: str
    content: str
    score: float
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_version": self.source_version,
            "tenant_id": self.tenant_id,
            "scope": self.scope,
            "path": self.path,
            "content": self.content,
            "score": self.score,
            "provenance": dict(self.provenance),
            "verification_status": "verified",
        }


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    sources: tuple[RetrievedSource, ...]
    rejected_count: int = 0
    rejection_reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = RETRIEVAL_RESULT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "query": self.query,
            "sources": [source.to_dict() for source in self.sources],
            "rejected_count": self.rejected_count,
            "rejection_reasons": list(self.rejection_reasons),
            "metadata": dict(self.metadata),
        }


class RetrieverPort(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...
