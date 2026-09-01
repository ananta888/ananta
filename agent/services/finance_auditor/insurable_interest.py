"""Assess whether a derivative offsets an observable existing risk."""

from __future__ import annotations

from agent.services.finance_auditor.derivative_models import (
    InsurableInterestAssessment,
    InterestStatus,
    UnderlyingRelation,
)

_RELATION_RULES = (
    (
        UnderlyingRelation.UNRELATED_BET,
        ("unrelated bet", "naked cds", "naked short", "owns no", "no underlying interest"),
    ),
    (
        UnderlyingRelation.SYNTHETIC_ONLY,
        ("synthetic only", "synthetic short", "derivative on derivative", "no physical position"),
    ),
    (UnderlyingRelation.MARKET_MAKER_INVENTORY, ("market maker inventory", "dealer inventory")),
    (UnderlyingRelation.OWNS_ASSET, ("owns the asset", "owns bonds", "farmer", "producer", "inventory hedge")),
    (UnderlyingRelation.OWES_DEBT, ("owes debt", "borrower hedges", "debt service hedge")),
    (UnderlyingRelation.SUPPLIES_GOODS, ("supplies goods", "anticipated production", "supplier hedge")),
    (
        UnderlyingRelation.NEEDS_HEDGE,
        ("needs hedge", "currency exposure", "importer", "exporter", "airline fuel", "price risk"),
    ),
)


def assess_insurable_interest(text: str) -> InsurableInterestAssessment:
    lower = text.lower()
    relation = next(
        (relation for relation, terms in _RELATION_RULES if any(term in lower for term in terms)),
        UnderlyingRelation.UNKNOWN,
    )
    if relation in {
        UnderlyingRelation.OWNS_ASSET,
        UnderlyingRelation.OWES_DEBT,
        UnderlyingRelation.SUPPLIES_GOODS,
        UnderlyingRelation.NEEDS_HEDGE,
        UnderlyingRelation.MARKET_MAKER_INVENTORY,
    }:
        return InsurableInterestAssessment(
            InterestStatus.YES,
            relation,
            "The claim states an owned, owed, supplied, operational or inventory risk that the position can offset.",
        )
    if relation in {UnderlyingRelation.SYNTHETIC_ONLY, UnderlyingRelation.UNRELATED_BET}:
        return InsurableInterestAssessment(
            InterestStatus.NO,
            relation,
            "The claim states only synthetic exposure or an unrelated price/damage bet, not an offsetting own risk.",
        )
    return InsurableInterestAssessment(
        InterestStatus.UNCLEAR,
        relation,
        "The submitted claim does not establish whether the position offsets an owned or operational risk.",
    )
