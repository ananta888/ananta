"""Typed contracts for read-only derivatives analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class InterestStatus(str, Enum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


class UnderlyingRelation(str, Enum):
    OWNS_ASSET = "owns_asset"
    OWES_DEBT = "owes_debt"
    SUPPLIES_GOODS = "supplies_goods"
    NEEDS_HEDGE = "needs_hedge"
    MARKET_MAKER_INVENTORY = "market_maker_inventory"
    SYNTHETIC_ONLY = "synthetic_only"
    UNRELATED_BET = "unrelated_bet"
    UNKNOWN = "unknown"


class InfluenceLevel(str, Enum):
    NONE = "none"
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InsurableInterestAssessment:
    legitimate_underlying_interest: InterestStatus
    underlying_relation: UnderlyingRelation
    explanation: str


@dataclass(frozen=True)
class PredatoryDerivativeResult:
    classification: str
    classification_reason: str
    legitimate_underlying_interest: str
    underlying_relation: str
    interest_explanation: str
    underlying_type: str
    naked_exposure_flags: tuple[str, ...]
    naked_exposure_score: int
    damage_incentive_score: int
    damage_profit_mechanism: str
    ability_to_influence_damage: str
    influence_reasons: tuple[str, ...]
    basic_needs_derivative_flag: bool
    basic_needs_affected: tuple[str, ...]
    complexity_score: int
    opacity_score: int
    systemic_risk_score: int
    chain_reactions: tuple[str, ...]
    ban_worthiness_score: int
    regulatory_recommendation: str
    evidence_notes: tuple[str, ...]
    guardrail_flags: tuple[str, ...]
    leverage_factor_note: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
