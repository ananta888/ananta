"""Transparent rule catalog and actor identification."""

from __future__ import annotations

from dataclasses import dataclass

from agent.services.finance_auditor.models import ClassificationDetail


@dataclass(frozen=True)
class KeywordRule:
    category: str
    keywords: tuple[str, ...]
    explanation: str
    evidence_required: bool = False


CLASSIFICATION_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        "speculation_on_necessities",
        (
            "food",
            "grain",
            "wheat",
            "water",
            "housing",
            "rent",
            "energy",
            "healthcare",
            "lebensmittel",
            "miete",
            "wohnung",
        ),
        "A basic need is treated as a speculative asset.",
    ),
    KeywordRule(
        "debt_dependency",
        ("debt", "loan", "credit card", "interest", "austerity", "schuld", "kredit", "zins"),
        "Debt may create durable power asymmetry between creditor and debtor.",
    ),
    KeywordRule(
        "rentier_extraction",
        ("rent", "landlord", "dividend extraction", "license fee", "miete", "vermieter"),
        "Income is extracted through control of scarce assets rather than new production.",
    ),
    KeywordRule(
        "regulatory_capture",
        ("regulatory capture", "revolving door", "lobbying", "regulator", "lobby"),
        "Private financial interests may shape the public rules that govern them.",
    ),
    KeywordRule(
        "tax_haven_risk",
        ("tax haven", "offshore", "shell company", "steueroase", "briefkastenfirma"),
        "Opaque or low-tax structures may weaken the public tax base.",
    ),
    KeywordRule(
        "extractive_finance",
        ("leverage", "fees", "market maker", "private equity", "foreclosure", "hebel", "gebühr", "zwangsversteiger"),
        "The mechanism shows indicators of value extraction through financial control.",
    ),
    KeywordRule(
        "structural_violence",
        ("hunger", "eviction", "uninsured", "wage cut", "austerity", "displacement", "obdach", "lohnkürzung"),
        "Institutional arrangements expose people to avoidable harm without requiring one violent actor.",
    ),
    KeywordRule(
        "legal_but_harmful",
        ("legal", "compliant", "within the law", "regulated", "legal but", "rechtmäßig"),
        "Formal legality does not settle the social legitimacy of harmful outcomes.",
    ),
)

CRIME_TERMS = (
    "fraud",
    "criminal",
    "crime",
    "illegal",
    "theft",
    "embezzlement",
    "betrug",
    "kriminell",
    "illegal",
    "diebstahl",
)


def classification_details(text: str) -> tuple[ClassificationDetail, ...]:
    lower = text.lower()
    matches = [
        ClassificationDetail(rule.category, rule.explanation, rule.evidence_required, rule.keywords)
        for rule in CLASSIFICATION_RULES
        if any(term in lower for term in rule.keywords)
    ]
    if not matches:
        matches.append(
            ClassificationDetail(
                "distributional_risk",
                "The claim needs a distributional analysis of benefits, power and costs.",
                False,
                ("profit", "loss", "risk", "control"),
            )
        )
    return tuple(matches)


def identify_actors(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    lower = text.lower()
    mapping = (
        (("broker", "daytrad"), "brokers", "retail traders"),
        (("exchange", "börse"), "exchanges", "market participants"),
        (("market maker",), "market makers", "retail traders"),
        (("etf", "fund"), "fund providers", "fee-paying investors"),
        (("bank", "credit", "loan", "kredit"), "banks and creditors", "debtors"),
        (("crypto", "bitcoin", "meme"), "early buyers and platforms", "late buyers"),
        (("influencer",), "influencers and affiliates", "followers"),
        (("data", "high-frequency"), "data providers", "slower market participants"),
        (("rent", "housing", "miete", "wohnung"), "property owners and lenders", "tenants and displaced residents"),
        (("food", "grain", "wheat", "lebensmittel"), "commodity intermediaries", "food consumers and producers"),
    )
    profiteers: list[str] = []
    affected: list[str] = []
    for terms, winner, loser in mapping:
        if any(term in lower for term in terms):
            profiteers.append(winner)
            affected.append(loser)
    return tuple(dict.fromkeys(profiteers or ["financial intermediaries"])), tuple(
        dict.fromkeys(affected or ["risk-bearing public and market participants"])
    )
