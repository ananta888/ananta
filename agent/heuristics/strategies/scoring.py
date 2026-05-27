"""Scoring utilities for Python HeuristicStrategies.

All utilities are deterministic and run without any AI/LLM call.
"""
from __future__ import annotations

from typing import Any


def clamp_score(score: float) -> float:
    """Clamp score to [0.0, 1.0]."""
    return max(0.0, min(1.0, float(score)))


def weighted_rank(
    items: list[str],
    weights: dict[str, float],
    *,
    default_weight: float = 0.5,
) -> list[tuple[str, float]]:
    """Return items sorted by weight descending."""
    scored = [(item, float(weights.get(item, default_weight))) for item in items]
    return sorted(scored, key=lambda x: x[1], reverse=True)


def build_reason_codes(*parts: str | None) -> list[str]:
    return [p for p in parts if p]


def is_below_threshold(score: float, threshold: float = 0.5) -> bool:
    return float(score) < float(threshold)


def top_n(items: list[str], n: int) -> list[str]:
    return items[:max(0, int(n))]


def keyword_score(text: str, keywords: list[str]) -> float:
    """Return fraction of keywords found in text (case-insensitive)."""
    if not keywords:
        return 0.0
    t = text.lower()
    matched = sum(1 for kw in keywords if kw.lower() in t)
    return clamp_score(matched / len(keywords))
