"""Transparent bounded scoring for structural and extraction risks."""

from __future__ import annotations


def calculate_scores(
    *,
    classifications: tuple[str, ...],
    basic_needs: tuple[str, ...],
    casino_score: int,
    debt_flags: tuple[str, ...],
    externalized_costs: tuple[str, ...],
) -> dict[str, int]:
    structural = 10 * len(classifications) + 12 * len(basic_needs) + 6 * len(externalized_costs)
    if any(need in {"food", "housing", "healthcare", "basic_services"} for need in basic_needs):
        structural += 15
    extraction = 12 * len(debt_flags) + (15 if "extractive_finance" in classifications else 0)
    return {
        "structural_harm_score": min(100, structural),
        "casino_score": max(0, min(100, casino_score)),
        "extraction_score": min(100, extraction),
    }
