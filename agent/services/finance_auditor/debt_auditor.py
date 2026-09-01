"""Debt-power analysis that distinguishes productive from extractive credit."""

from __future__ import annotations

_DEBT_FLAGS = (
    ("debt_dependency", ("debt", "loan", "credit", "schuld", "kredit")),
    ("interest_extraction", ("interest", "apr", "payday", "zins", "wucher")),
    ("austerity_pressure", ("austerity", "imf condition", "budget cut", "sparpolitik", "kürzung")),
    ("dependency_cycle", ("rollover", "refinance", "minimum payment", "debt spiral", "umschuld", "schuldenspirale")),
)


def audit_debt(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    lower = text.lower()
    flags = tuple(flag for flag, terms in _DEBT_FLAGS if any(term in lower for term in terms))
    notes: list[str] = []
    if flags:
        notes.append("Creditors retain power through repayment schedules, collateral and access to refinancing.")
        if any(term in lower for term in ("productive", "infrastructure", "education", "investment", "produktiv")):
            notes.append(
                "The stated productive purpose weighs against a blanket finding of "
                "exploitation; terms, alternatives and outcomes still matter."
            )
        else:
            notes.append(
                "No productive purpose is established; assess necessity, bargaining power, "
                "rates and consequences before judging legitimacy."
            )
    return flags, tuple(notes)
