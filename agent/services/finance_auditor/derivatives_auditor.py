"""Deterministic classification and policy scoring for derivative claims."""

from __future__ import annotations

from agent.services.finance_auditor.conflict_of_interest import (
    analyze_damage_incentive,
    assess_influence,
)
from agent.services.finance_auditor.derivative_models import (
    InterestStatus,
    PredatoryDerivativeResult,
    UnderlyingRelation,
)
from agent.services.finance_auditor.insurable_interest import assess_insurable_interest
from agent.services.finance_auditor.naked_exposure import detect_naked_exposure

_BASIC_NEEDS = (
    ("food", ("food", "grain", "wheat", "hunger", "lebensmittel")),
    ("water", ("water", "wasser")),
    ("housing", ("housing", "rent", "mortgage", "wohnung", "miete")),
    ("energy", ("energy", "electricity", "gas price", "energie", "strom")),
    ("healthcare", ("health", "medicine", "hospital", "gesund", "medizin")),
)
_DERIVATIVE_TYPES = {
    "real_asset": ("equity option", "stock option", "owns the asset"),
    "debt_claim": ("bond", "cds", "credit default", "sovereign debt"),
    "commodity": ("commodity", "grain", "wheat", "oil", "gas future", "food future"),
    "index": ("index future", "index option", "index swap"),
    "synthetic_basket": ("synthetic etf", "synthetic basket", "synthetic cdo"),
    "derivative_on_derivative": ("derivative on derivative", "option on swap", "swaption", "re-securitized"),
    "no_clear_underlying": ("no clear underlying", "unrelated bet"),
}
_SYSTEMIC_RULES = (
    (("leverage", "leveraged", "margin"), 20, "Leverage can turn price moves into margin calls."),
    (("otc", "opaque", "intransparent"), 15, "Opaque bilateral exposure can conceal counterparty concentration."),
    (
        ("counterparty chain", "interconnected", "chain of counterparties"),
        20,
        "Counterparty failure can transmit losses through a chain.",
    ),
    (("margin call",), 15, "Margin calls can create urgent liquidity demand."),
    (("fire sale", "forced sale"), 15, "Forced sales can amplify falling prices and further margin calls."),
    (("concentration", "dominant dealer"), 10, "Concentration can create common points of failure."),
    (("bailout", "public rescue"), 15, "Systemic losses may be shifted to a public backstop."),
)
_MISUSE_ALLEGATIONS = ("manipulated", "caused intentionally", "short and distort", "committed fraud")
_OPERATIONAL_TERMS = ("how to build", "execute", "place the trade", "coordinate", "pump", "distort campaign")


class PredatoryDerivativesAuditor:
    def audit(self, claim: str) -> PredatoryDerivativeResult:
        text = str(claim or "").strip()
        if not text:
            raise ValueError("predatory_derivative_claim_required")
        lower = text.lower()
        interest = assess_insurable_interest(text)
        naked_flags, naked_score = detect_naked_exposure(text)
        damage_score, damage_mechanism = analyze_damage_incentive(text)
        influence, influence_reasons = assess_influence(text)
        needs = tuple(name for name, terms in _BASIC_NEEDS if any(term in lower for term in terms))
        underlying_type = self._underlying_type(lower)
        complexity_score = min(
            100,
            (45 if underlying_type == "derivative_on_derivative" else 0)
            + (25 if "multiple synthetic" in lower or "re-securitized" in lower else 0)
            + (15 if "leverage" in lower or "leveraged" in lower else 0),
        )
        opacity_score = min(
            100,
            (40 if underlying_type in {"no_clear_underlying", "unknown"} else 0)
            + (30 if any(term in lower for term in ("otc", "opaque", "intransparent")) else 0),
        )
        systemic_score, chain_reactions = self._systemic_risk(lower, complexity_score)
        classification, reason = self._classification(lower, interest, naked_score, damage_score)
        ban_score = self._ban_score(
            interest.legitimate_underlying_interest,
            naked_score,
            damage_score,
            bool(needs),
            complexity_score,
            opacity_score,
            systemic_score,
        )
        allegation = any(term in lower for term in _MISUSE_ALLEGATIONS)
        recommendation = self._recommendation(
            classification,
            ban_score,
            systemic_score,
            opacity_score,
            allegation,
        )
        guardrails = tuple(
            flag
            for flag, present in (
                ("operational_derivatives_instruction_not_provided", any(term in lower for term in _OPERATIONAL_TERMS)),
                ("misuse_allegation_requires_evidence", allegation),
            )
            if present
        )
        leverage_note = (
            "Leverage or layering is mentioned; losses, collateral demands and "
            "counterparty exposure may exceed the initial cash outlay."
            if any(
                term in lower for term in ("leverage", "leveraged", "multiple synthetic", "derivative on derivative")
            )
            else "No leverage factor is established by the submitted claim."
        )
        evidence_notes = (
            (
                "A concrete manipulation, intent or fraud allegation requires identified "
                "strong evidence; structural incentives do not prove intent.",
            )
            if allegation
            else ("This is a structural policy heuristic, not a legal opinion or finding of intent.",)
        )
        return PredatoryDerivativeResult(
            classification=classification,
            classification_reason=reason,
            legitimate_underlying_interest=interest.legitimate_underlying_interest.value,
            underlying_relation=interest.underlying_relation.value,
            interest_explanation=interest.explanation,
            underlying_type=underlying_type,
            naked_exposure_flags=naked_flags,
            naked_exposure_score=naked_score,
            damage_incentive_score=damage_score,
            damage_profit_mechanism=damage_mechanism,
            ability_to_influence_damage=influence.value,
            influence_reasons=influence_reasons,
            basic_needs_derivative_flag=bool(needs),
            basic_needs_affected=needs,
            complexity_score=complexity_score,
            opacity_score=opacity_score,
            systemic_risk_score=systemic_score,
            chain_reactions=chain_reactions,
            ban_worthiness_score=ban_score,
            regulatory_recommendation=recommendation,
            evidence_notes=evidence_notes,
            guardrail_flags=guardrails,
            leverage_factor_note=leverage_note,
            metadata={"read_only": True, "trading_advice": False, "policy_analysis_not_legal_opinion": True},
        )

    @staticmethod
    def _underlying_type(lower: str) -> str:
        return next(
            (kind for kind, terms in _DERIVATIVE_TYPES.items() if any(term in lower for term in terms)),
            "unknown",
        )

    @staticmethod
    def _classification(lower: str, interest, naked_score: int, damage_score: int) -> tuple[str, str]:
        if interest.underlying_relation == UnderlyingRelation.MARKET_MAKER_INVENTORY:
            return "market_making_inventory_hedge", "An identified dealer inventory exposure is being offset."
        if interest.legitimate_underlying_interest == InterestStatus.YES:
            if "provides liquidity" in lower:
                return "liquidity_service", "The stated position offsets an own risk while supporting market liquidity."
            return "legitimate_hedge", "The derivative offsets a stated owned, owed, supplied or operational exposure."
        if interest.legitimate_underlying_interest == InterestStatus.NO and damage_score >= 20:
            return (
                "predatory_derivative",
                "No own underlying risk is stated and profit is linked directly to another party's damage.",
            )
        if interest.legitimate_underlying_interest == InterestStatus.NO or naked_score >= 25:
            return "naked_damage_bet", "The stated exposure is naked, synthetic-only or unrelated to an own risk."
        if any(term in lower for term in ("speculative", "pure price bet", "directional")):
            return "speculative_bet", "The claim describes directional speculation without establishing risk reduction."
        return "unknown", "The claim does not establish enough information to distinguish hedge from speculation."

    @staticmethod
    def _systemic_risk(lower: str, complexity_score: int) -> tuple[int, tuple[str, ...]]:
        matches = [
            (weight, reaction) for terms, weight, reaction in _SYSTEMIC_RULES if any(term in lower for term in terms)
        ]
        score = min(100, sum(weight for weight, _ in matches) + complexity_score // 4)
        return score, tuple(reaction for _, reaction in matches)

    @staticmethod
    def _ban_score(
        interest: InterestStatus,
        naked_score: int,
        damage_score: int,
        basic_need: bool,
        complexity: int,
        opacity: int,
        systemic: int,
    ) -> int:
        score = naked_score // 2 + damage_score // 2 + complexity // 5 + opacity // 5 + systemic // 5
        if interest == InterestStatus.NO:
            score += 20
        if basic_need and interest == InterestStatus.NO:
            score += 30
        return min(100, score)

    @staticmethod
    def _recommendation(
        classification: str,
        ban_score: int,
        systemic_score: int,
        opacity_score: int,
        allegation: bool,
    ) -> str:
        if allegation:
            return "evidence_required"
        if classification == "predatory_derivative" and ban_score >= 70:
            return "ban_predatory_structure"
        if classification == "naked_damage_bet" and ban_score >= 45:
            return "ban_naked_exposure"
        if classification in {"naked_damage_bet", "predatory_derivative"}:
            return "require_underlying_interest"
        if opacity_score >= 50:
            return "require_exchange_transparency"
        if systemic_score >= 50:
            return "restrict"
        return "allow"
