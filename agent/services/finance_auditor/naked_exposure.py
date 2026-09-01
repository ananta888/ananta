"""Explainable naked-exposure detection without construction advice."""

from __future__ import annotations

_NAKED_RULES = (
    ("naked_cds_like_exposure", ("naked cds", "default swap without", "sovereign default bet"), 30),
    ("naked_short_like_exposure", ("naked short", "short without borrow", "uncovered short"), 25),
    ("synthetic_short_exposure", ("synthetic short", "inverse synthetic", "short exposure via swap"), 20),
    ("pure_price_bet", ("pure price bet", "directional option bet", "fx leverage bet"), 15),
    ("unrelated_damage_bet", ("unrelated bet", "profit from someone else's loss", "bet on collapse"), 30),
)


def detect_naked_exposure(text: str) -> tuple[tuple[str, ...], int]:
    lower = text.lower()
    flags = tuple(flag for flag, terms, _ in _NAKED_RULES if any(term in lower for term in terms))
    return flags, min(100, sum(weight for flag, _, weight in _NAKED_RULES if flag in flags))
