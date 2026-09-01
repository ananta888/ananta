"""Identify costs shifted away from financial beneficiaries."""

from __future__ import annotations

_COST_RULES = (
    ("environment", ("pollution", "carbon", "mining", "deforestation", "umwelt", "emission")),
    ("working_conditions", ("wage", "layoff", "gig worker", "sweatshop", "arbeit", "lohn")),
    ("public_infrastructure", ("public infrastructure", "privat", "municipal", "infrastruktur")),
    ("health", ("health", "toxic", "medicine", "gesund", "medizin")),
    ("hunger", ("food", "grain", "wheat", "hunger", "lebensmittel")),
    ("housing_displacement", ("housing", "rent", "eviction", "wohnung", "miete", "verdräng")),
    ("tax_base", ("tax haven", "offshore", "tax avoidance", "steueroase", "steuervermeid")),
)


def analyze_externalization(text: str) -> tuple[str, ...]:
    lower = text.lower()
    return tuple(category for category, terms in _COST_RULES if any(term in lower for term in terms))


def moral_balance_summary(costs: tuple[str, ...], profiteers: tuple[str, ...], affected: tuple[str, ...]) -> str:
    if not costs:
        return (
            "No specific externalized cost is established by the submitted claim; "
            "distributional effects still require evidence."
        )
    return (
        f"Potential gains accrue to {', '.join(profiteers)}, while {', '.join(affected)} "
        f"may carry externalized costs: {', '.join(costs)}."
    )
