"""Typed, JSON-serializable contracts for finance audits."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
    ETF = "etf"
    COMMODITY = "commodity"
    FOREX = "forex"
    DERIVATIVE = "derivative"
    DEBT = "debt"
    HOUSING = "housing"
    FOOD = "food"
    UNKNOWN = "unknown"


class AuditTone(str, Enum):
    NEUTRAL = "neutral"
    DIRECT = "direct"
    ACCUSATORY_GROUNDED = "accusatory_grounded"


class SourceType(str, Enum):
    REGULATOR = "regulator"
    OFFICIAL_REPORT = "official_report"
    ACADEMIC = "academic"
    ESTABLISHED_MEDIA = "established_media"
    COMPANY_PR = "company_pr"
    BANK_RESEARCH = "bank_research"
    INFLUENCER = "influencer"
    FORUM = "forum"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceReference:
    source_id: str
    source_type: SourceType = SourceType.UNKNOWN
    title: str = ""
    published_at: str | None = None
    conflict_disclosed: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SourceReference":
        allowed = {"source_id", "source_type", "title", "published_at", "conflict_disclosed"}
        if set(value) - allowed:
            raise ValueError("ziegler_source_unknown_field")
        source_id = str(value.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("ziegler_source_id_required")
        try:
            source_type = SourceType(str(value.get("source_type", "unknown")))
        except ValueError as exc:
            raise ValueError("ziegler_source_type_invalid") from exc
        return cls(
            source_id=source_id,
            source_type=source_type,
            title=str(value.get("title") or "").strip(),
            published_at=str(value["published_at"]) if value.get("published_at") else None,
            conflict_disclosed=value.get("conflict_disclosed", False) is True,
        )


@dataclass(frozen=True)
class ClassificationDetail:
    category: str
    explanation: str
    evidence_required: bool
    typical_indicators: tuple[str, ...]


@dataclass(frozen=True)
class HumanConsequence:
    category: str
    impact_type: str
    explanation: str


@dataclass(frozen=True)
class ZieglerAuditInput:
    claim: str
    context: str = ""
    asset_type: AssetType = AssetType.UNKNOWN
    optional_sources: tuple[SourceReference, ...] = ()
    requested_tone: AuditTone = AuditTone.DIRECT

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ZieglerAuditInput":
        allowed = {"claim", "context", "asset_type", "optional_sources", "requested_tone"}
        if set(value) - allowed:
            raise ValueError("ziegler_audit_unknown_field")
        claim = str(value.get("claim") or "").strip()
        if not claim:
            raise ValueError("ziegler_audit_claim_required")
        if len(claim) > 20_000 or len(str(value.get("context") or "")) > 50_000:
            raise ValueError("ziegler_audit_input_too_large")
        try:
            asset_type = AssetType(str(value.get("asset_type", "unknown")))
            requested_tone = AuditTone(str(value.get("requested_tone", "direct")))
        except ValueError as exc:
            raise ValueError("ziegler_audit_enum_invalid") from exc
        raw_sources = value.get("optional_sources") or []
        if not isinstance(raw_sources, list) or len(raw_sources) > 100:
            raise ValueError("ziegler_audit_sources_invalid")
        sources = tuple(SourceReference.from_mapping(item) for item in raw_sources if isinstance(item, Mapping))
        if len(sources) != len(raw_sources):
            raise ValueError("ziegler_audit_source_invalid")
        return cls(
            claim=claim,
            context=str(value.get("context") or "").strip(),
            asset_type=asset_type,
            optional_sources=sources,
            requested_tone=requested_tone,
        )


@dataclass(frozen=True)
class ZieglerAuditResult:
    classification: tuple[str, ...]
    classification_details: tuple[ClassificationDetail, ...]
    scores: dict[str, int]
    basic_needs_affected: tuple[str, ...]
    profiteers: tuple[str, ...]
    affected_groups: tuple[str, ...]
    human_consequences: tuple[HumanConsequence, ...]
    human_consequence_notes: tuple[str, ...]
    externalized_costs: tuple[str, ...]
    evidence_notes: tuple[str, ...]
    legality_vs_legitimacy_note: str
    legitimacy_verdict: str
    moral_balance_summary: str
    summary: str
    guardrail_flags: tuple[str, ...]
    confidence: float
    llm_advisory: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
