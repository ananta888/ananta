"""Runtime-neutral retrieval and provenance contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

RETRIEVAL_RESULT_SCHEMA = "ananta.retrieval_result.v1"
SOURCE_REF_SCHEMA = "ananta.source_ref.v2"
_SOURCE_ID_PATTERN = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")
_SHA256_PATTERN = re.compile(r"^(?:sha256:)?[a-fA-F0-9]{64}$")


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Version-qualified, tenant-bound reference issued by an authority.

    The contract validates supplied identities; it never creates an identifier
    from a path, position or content fragment.
    """

    source_id: str
    source_version: str
    tenant_id: str
    scope: str
    provenance_digest: str
    schema: str = SOURCE_REF_SCHEMA

    def __post_init__(self) -> None:
        schema = str(self.schema or "").strip()
        source_id = str(self.source_id or "").strip()
        source_version = str(self.source_version or "").strip()
        tenant_id = str(self.tenant_id or "").strip()
        scope = str(self.scope or "").strip()
        provenance_digest = str(self.provenance_digest or "").strip().lower()
        if schema != SOURCE_REF_SCHEMA:
            raise ValueError("source_ref_schema_invalid")
        if _SOURCE_ID_PATTERN.fullmatch(source_id) is None:
            raise ValueError("source_ref_id_invalid")
        if not source_version or len(source_version) > 256:
            raise ValueError("source_ref_version_invalid")
        if not tenant_id or len(tenant_id) > 256:
            raise ValueError("source_ref_tenant_invalid")
        if not scope or len(scope) > 256:
            raise ValueError("source_ref_scope_invalid")
        if _SHA256_PATTERN.fullmatch(provenance_digest) is None:
            raise ValueError("source_ref_provenance_digest_invalid")
        if provenance_digest.startswith("sha256:"):
            provenance_digest = provenance_digest.split(":", 1)[1]
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_version", source_version)
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "provenance_digest", provenance_digest)
        object.__setattr__(self, "schema", schema)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SourceRef":
        return cls(
            source_id=str(raw.get("source_id") or ""),
            source_version=str(raw.get("source_version") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            scope=str(raw.get("scope") or ""),
            provenance_digest=str(raw.get("provenance_digest") or ""),
            schema=str(raw.get("schema") or SOURCE_REF_SCHEMA),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "tenant_id": self.tenant_id,
            "scope": self.scope,
            "provenance_digest": self.provenance_digest,
        }


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    tenant_id: str
    scope: str
    allowed_source_ids: frozenset[str]
    max_results: int = 5
    allowed_source_refs: tuple[SourceRef, ...] = ()
    repository_revision: str = ""
    manifest_hash: str = ""
    source_allowlist_version: str = ""
    retrieval_profile: Mapping[str, Any] = field(default_factory=dict)

    def source_ref(self, source_id: str) -> SourceRef | None:
        matches = [ref for ref in self.allowed_source_refs if ref.source_id == source_id]
        if len(matches) > 1:
            raise ValueError("retrieval_duplicate_source_ref")
        return matches[0] if matches else None


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
    source_ref: SourceRef | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
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
        if self.source_ref is not None:
            payload["source_ref"] = self.source_ref.to_dict()
        return payload


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
