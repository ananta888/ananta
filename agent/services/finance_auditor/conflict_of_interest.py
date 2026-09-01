"""Damage-profit and influence analysis without inferring intent."""

from __future__ import annotations

from agent.services.finance_auditor.derivative_models import InfluenceLevel

_DAMAGE_RULES = (
    (
        ("default", "zahlungsunfähig", "sovereign crisis"),
        "Profit rises when the referenced debtor defaults or its credit quality deteriorates.",
        30,
    ),
    (("price fall", "collapse", "short profit", "preisverfall"), "Profit rises as the referenced price falls.", 20),
    (
        ("forced sale", "fire sale", "zwangsverkauf"),
        "Profit can rise during forced liquidation and distressed pricing.",
        20,
    ),
    (("hunger", "food crisis"), "Profit is linked to food stress or hunger-related price disruption.", 30),
    (("eviction", "housing loss", "wohnungsverlust"), "Profit is linked to housing distress or displacement.", 30),
    (("energy crisis", "energiekrise"), "Profit is linked to disruption of essential energy access.", 25),
)


def analyze_damage_incentive(text: str) -> tuple[int, str]:
    lower = text.lower()
    matches = [
        (explanation, weight) for terms, explanation, weight in _DAMAGE_RULES if any(term in lower for term in terms)
    ]
    if not matches:
        return 0, "No direct profit-from-damage mechanism is established by the submitted claim."
    return min(100, sum(weight for _, weight in matches)), " ".join(explanation for explanation, _ in matches)


def assess_influence(text: str) -> tuple[InfluenceLevel, tuple[str, ...]]:
    lower = text.lower()
    rules = (
        (
            InfluenceLevel.STRONG,
            ("can cancel credit", "controls supply", "dominant market power"),
            "Direct contractual or supply power could affect the referenced harm.",
        ),
        (
            InfluenceLevel.MEDIUM,
            ("rating influence", "political lobby", "media owner"),
            "Institutional influence could affect perceptions, policy or financing conditions.",
        ),
        (
            InfluenceLevel.WEAK,
            ("information advantage", "analyst platform", "large following"),
            "Information or media reach may weakly affect market conditions.",
        ),
        (
            InfluenceLevel.NONE,
            ("no influence", "price taker"),
            "The claim explicitly states no ability to affect the outcome.",
        ),
    )
    for level, terms, reason in rules:
        if any(term in lower for term in terms):
            return level, (reason,)
    return InfluenceLevel.UNKNOWN, (
        "Profit exposure alone does not establish an ability or intention to cause damage.",
    )
