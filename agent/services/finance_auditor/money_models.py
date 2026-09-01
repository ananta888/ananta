"""Contracts for deterministic monetary-system analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from agent.services.finance_auditor.models import AuditTone, SourceReference


class MonetaryTopic(str, Enum):
    COMMERCIAL_BANK_MONEY = "commercial_bank_money"
    CENTRAL_BANK_MONEY = "central_bank_money"
    SOVEREIGN_MONEY = "sovereign_money"
    SEIGNIORAGE = "seigniorage"
    PUBLIC_DEBT = "public_debt"
    INTEREST = "interest"
    INFLATION = "inflation"
    CBDC = "cbdc"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MoneyCreationAuditInput:
    claim: str
    monetary_topic: MonetaryTopic = MonetaryTopic.UNKNOWN
    optional_sources: tuple[SourceReference, ...] = ()
    context: str = ""
    requested_tone: AuditTone = AuditTone.DIRECT

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MoneyCreationAuditInput":
        allowed = {"claim", "monetary_topic", "optional_sources", "context", "requested_tone"}
        if set(value) - allowed:
            raise ValueError("money_audit_unknown_field")
        claim = str(value.get("claim") or "").strip()
        if not claim:
            raise ValueError("money_audit_claim_required")
        try:
            topic = MonetaryTopic(str(value.get("monetary_topic", "unknown")))
            tone = AuditTone(str(value.get("requested_tone", "direct")))
        except ValueError as exc:
            raise ValueError("money_audit_enum_invalid") from exc
        raw_sources = value.get("optional_sources") or []
        if not isinstance(raw_sources, list) or len(raw_sources) > 100:
            raise ValueError("money_audit_sources_invalid")
        sources = tuple(SourceReference.from_mapping(item) for item in raw_sources if isinstance(item, Mapping))
        if len(sources) != len(raw_sources):
            raise ValueError("money_audit_source_invalid")
        return cls(
            claim=claim,
            monetary_topic=topic,
            optional_sources=sources,
            context=str(value.get("context") or "").strip(),
            requested_tone=tone,
        )


@dataclass(frozen=True)
class MoneyCreationAuditResult:
    mechanics_summary: str
    mechanics_flags: tuple[str, ...]
    money_forms: dict[str, str]
    power_analysis: tuple[str, ...]
    bank_money_privilege_note: str
    beneficiaries: tuple[str, ...]
    affected_groups: tuple[str, ...]
    monetary_democracy_score: int
    democracy_score_factors: dict[str, int]
    democratic_legitimacy_note: str
    reform_options: tuple[dict[str, Any], ...]
    caveats: tuple[str, ...]
    guardrail_flags: tuple[str, ...]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
