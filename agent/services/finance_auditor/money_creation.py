"""Explain monetary mechanics and flag common misconceptions."""

from __future__ import annotations

_MECHANIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "savings_intermediary_misconception",
        ("only lend savings", "only lend deposits", "nur spareinlagen", "nur einlagen weiter"),
    ),
    (
        "commercial_bank_money_creation",
        ("create deposits", "creates money", "giralgeld", "deposit money", "bankgeld", "kreditgeldschöpf"),
    ),
    (
        "unlimited_creation_misconception",
        ("unlimited money", "without limits", "beliebig viel geld", "grenzenlos geld"),
    ),
    ("reserve_multiplier_oversimplification", ("money multiplier", "reserves are multiplied", "geldmultiplikator")),
    (
        "reserves_lent_to_public_misconception",
        ("lend reserves to households", "reserven an haushalte", "reserves to customers"),
    ),
    (
        "cash_and_deposit_confusion",
        ("cash is bank deposit", "cash equals", "bargeld ist giralgeld"),
    ),
    ("central_bank_and_commercial_money_confusion", ("all money is central bank money", "alles zentralbankgeld")),
    ("loan_repayment_destroys_deposit_money", ("repay loan", "loan repayment", "kredit tilgen", "kredittilgung")),
    ("bank_funding_constraint", ("funding cost", "refinancing", "liquidity constraint", "refinanzierung")),
    ("capital_constraint", ("capital requirement", "eigenkapitalanforder")),
    ("credit_demand_constraint", ("credit demand", "kreditnachfrage")),
    ("monetary_policy_constraint", ("policy rate", "leitzins", "monetary policy")),
    ("deposit_insurance_backstop", ("deposit insurance", "einlagensicherung")),
    ("cbdc_public_money_question", ("cbdc", "digital euro", "digitaler euro")),
    ("seigniorage_distribution_question", ("seigniorage", "geldschöpfungsgewinn")),
)

MONEY_FORMS = {
    "cash": "Central-bank-issued notes and coins available to the public.",
    "central_bank_reserves": (
        "Electronic central bank money held by eligible institutions for settlement and policy operations."
    ),
    "commercial_bank_deposits": (
        "Bank liabilities used by the public as money; lending or asset purchases "
        "can create them through balance-sheet expansion."
    ),
    "credit": "A contractual asset/liability relationship; it is not itself identical to every form of money.",
}


def analyze_money_creation(text: str) -> tuple[tuple[str, ...], str]:
    lower = text.lower()
    flags = tuple(flag for flag, terms in _MECHANIC_RULES if any(term in lower for term in terms))
    if "savings_intermediary_misconception" in flags:
        summary = (
            "Misleading: commercial banks do not merely pass on pre-existing savings. "
            "A loan normally creates a matching customer deposit through balance-sheet expansion."
        )
    elif "unlimited_creation_misconception" in flags:
        summary = (
            "Misleading: banks can create deposits when lending, but capital, liquidity, "
            "funding costs, regulation, risk appetite, credit demand and monetary policy constrain them."
        )
    else:
        summary = (
            "Commercial-bank deposits, central-bank reserves and cash are distinct. "
            "Bank lending can create deposits, while repayment can extinguish them; creation is constrained."
        )
    return flags, summary
