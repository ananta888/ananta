"""Plural, non-promotional representation of monetary reform schools."""

from __future__ import annotations

from typing import Any


def reform_options(text: str) -> tuple[dict[str, Any], ...]:
    options = (
        {
            "school": "sovereign_money",
            "mechanism": (
                "Move issuance of generally accepted transaction money from commercial-bank "
                "balance sheets to a public monetary authority."
            ),
            "potential_benefits": ["public seigniorage", "clearer democratic mandate", "reduced deposit-run exposure"],
            "risks_and_critiques": [
                "transition risk",
                "credit availability",
                "central-bank power",
                "political allocation risk",
                "asset-bubble effects remain possible",
            ],
        },
        {
            "school": "full_reserve_or_100_percent_money",
            "mechanism": (
                "Require transaction deposits to be backed by central-bank money while separating lending finance."
            ),
            "potential_benefits": ["payment-account resilience", "clearer money-credit separation"],
            "risks_and_critiques": ["shadow-credit migration", "funding-cost changes", "implementation complexity"],
        },
        {
            "school": "narrow_banking",
            "mechanism": (
                "Restrict protected payment banks to liquid safe assets while risk credit uses separate funding."
            ),
            "potential_benefits": ["payment-system resilience"],
            "risks_and_critiques": ["credit moves outside perimeter", "boundary arbitrage"],
        },
        {
            "school": "mmt_and_post_keynesian_credit_theory",
            "mechanism": (
                "Analyze state currency capacity and endogenous bank credit without "
                "necessarily prescribing sovereign-money separation."
            ),
            "potential_benefits": ["focus on operational monetary mechanics", "attention to real-resource constraints"],
            "risks_and_critiques": ["institution-specific applicability", "inflation and governance disputes"],
        },
    )
    return options
