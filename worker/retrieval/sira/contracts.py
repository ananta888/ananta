from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class CorpusBinding:
    tenant_id: str
    scope: str
    repository_revision: str
    source_manifest_hash: str
    index_digest: str
    statistics_digest: str
    profile_version: str
    base_layer_id: str = ""
    delta_layer_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required = {
            "tenant_id": self.tenant_id,
            "scope": self.scope,
            "repository_revision": self.repository_revision,
            "source_manifest_hash": self.source_manifest_hash,
            "index_digest": self.index_digest,
            "statistics_digest": self.statistics_digest,
            "profile_version": self.profile_version,
        }
        if any(not str(value).strip() for value in required.values()):
            missing = next(name for name, value in required.items() if not str(value).strip())
            raise ValueError(f"sira_binding_{missing}_required")

    @property
    def scope_key(self) -> str:
        return f"{self.tenant_id}:{self.scope}:{self.repository_revision}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "scope": self.scope,
            "repository_revision": self.repository_revision,
            "source_manifest_hash": self.source_manifest_hash,
            "index_digest": self.index_digest,
            "statistics_digest": self.statistics_digest,
            "profile_version": self.profile_version,
            "base_layer_id": self.base_layer_id,
            "delta_layer_ids": list(self.delta_layer_ids),
        }


@dataclass(frozen=True, slots=True)
class GeneratedTerm:
    value: str
    confidence: float
    source_chunk_id: str = ""
    generator_profile: str = ""
    supporting_span: str = ""
    supporting_symbol: str = ""
    origin: str = "expansion"

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("sira_term_value_required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("sira_term_confidence_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "confidence": float(self.confidence),
            "source_chunk_id": self.source_chunk_id,
            "generator_profile": self.generator_profile,
            "supporting_span": self.supporting_span,
            "supporting_symbol": self.supporting_symbol,
            "origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class QueryExpansion:
    original_query: str
    evidence_sketch: str
    proposed_terms: tuple[GeneratedTerm, ...]
    model_id: str = ""
    model_digest: str = ""
    prompt_version: str = ""
    cache_key: str = ""
    fallback_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "evidence_sketch": self.evidence_sketch,
            "proposed_terms": [term.to_dict() for term in self.proposed_terms],
            "model_id": self.model_id,
            "model_digest": self.model_digest,
            "prompt_version": self.prompt_version,
            "cache_key": self.cache_key,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True, slots=True)
class CorpusTermStat:
    term: str
    document_frequency: int
    collection_frequency: int
    document_count: int
    fields: tuple[str, ...] = ()

    @property
    def document_frequency_ratio(self) -> float:
        return float(self.document_frequency) / float(max(1, self.document_count))


@dataclass(frozen=True, slots=True)
class TermDecision:
    term: GeneratedTerm
    accepted: bool
    reason_code: str
    stat: CorpusTermStat | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term.to_dict(),
            "accepted": self.accepted,
            "reason_code": self.reason_code,
            "statistics": None
            if self.stat is None
            else {
                "document_frequency": self.stat.document_frequency,
                "collection_frequency": self.stat.collection_frequency,
                "document_count": self.stat.document_count,
                "document_frequency_ratio": self.stat.document_frequency_ratio,
                "fields": list(self.stat.fields),
            },
        }


@dataclass(frozen=True, slots=True)
class WeightedTerm:
    value: str
    weight: float
    origin: str
    reason: str
    document_frequency: int = 0
    collection_frequency: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "weight": self.weight,
            "origin": self.origin,
            "reason": self.reason,
            "document_frequency": self.document_frequency,
            "collection_frequency": self.collection_frequency,
        }


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    original_query: str
    match_expression: str
    terms: tuple[WeightedTerm, ...]
    binding: CorpusBinding
    ranking_version: str = "sira-weighted-lexical.v1"
    fallback_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "match_expression": self.match_expression,
            "terms": [term.to_dict() for term in self.terms],
            "binding": self.binding.to_dict(),
            "ranking_version": self.ranking_version,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    execute_sira: bool
    shadow: bool
    required: bool
    reason_code: str
    feature_version: str = "sira-router.v1"
    features: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execute_sira": self.execute_sira,
            "shadow": self.shadow,
            "required": self.required,
            "reason_code": self.reason_code,
            "feature_version": self.feature_version,
            "features": dict(self.features),
        }


@runtime_checkable
class StructuredGenerationPort(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def model_digest(self) -> str: ...

    @property
    def local(self) -> bool: ...

    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class DocumentEnricherPort(Protocol):
    def enrich(self, document: Mapping[str, Any], *, binding: CorpusBinding) -> Mapping[str, Any]: ...


@runtime_checkable
class QueryExpanderPort(Protocol):
    def expand(self, query: str, *, binding: CorpusBinding) -> QueryExpansion: ...


@runtime_checkable
class CorpusTermStatisticsPort(Protocol):
    def lookup(self, terms: Sequence[str], *, binding: CorpusBinding) -> Mapping[str, CorpusTermStat]: ...


@runtime_checkable
class WeightedLexicalRetrieverPort(Protocol):
    def retrieve(self, query: CompiledQuery, *, top_k: int) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class PointwiseRerankerPort(Protocol):
    def rerank(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        *,
        top_n: int,
    ) -> tuple[Sequence[Mapping[str, Any]], Mapping[str, Any]]: ...
