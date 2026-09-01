"""Map financial abstractions to direct and indirect human consequences."""

from __future__ import annotations

from agent.services.finance_auditor.models import HumanConsequence

_CONSEQUENCE_RULES = (
    (
        ("food", "grain", "wheat", "hunger", "lebensmittel"),
        "food",
        "direct",
        "Price pressure on staple food can reduce access to adequate nutrition.",
    ),
    (
        ("housing", "rent", "eviction", "wohnung", "miete", "räumung"),
        "housing",
        "direct",
        "Asset-price and rent pressure can displace residents or consume essential income.",
    ),
    (
        ("health", "medicine", "hospital", "gesund", "medizin"),
        "healthcare",
        "direct",
        "Financial extraction from care can delay or prevent necessary treatment.",
    ),
    (
        ("wage", "pay cut", "layoff", "lohn", "entlass"),
        "wages",
        "direct",
        "Financial pressure can shift costs to workers through lower pay or job loss.",
    ),
    (
        ("debt", "interest", "credit", "schuld", "zins", "kredit"),
        "debt",
        "indirect",
        "Compounding obligations can narrow choices and entrench creditor power.",
    ),
    (
        ("austerity", "public budget", "municipal", "staatshaushalt", "sparpolitik"),
        "public_budgets",
        "indirect",
        "Debt-service or austerity pressure can crowd out public services.",
    ),
    (
        ("privat", "utility", "water", "energy", "grundversorgung"),
        "basic_services",
        "indirect",
        "Privatized essential services can exclude people who cannot pay market prices.",
    ),
)


def analyze_structural_violence(text: str) -> tuple[tuple[str, ...], tuple[HumanConsequence, ...]]:
    lower = text.lower()
    needs: list[str] = []
    consequences: list[HumanConsequence] = []
    for terms, category, impact_type, explanation in _CONSEQUENCE_RULES:
        if any(term in lower for term in terms):
            needs.append(category)
            consequences.append(HumanConsequence(category, impact_type, explanation))
    return tuple(dict.fromkeys(needs)), tuple(consequences)
