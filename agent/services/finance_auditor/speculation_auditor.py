"""Deterministic casino-mechanism detection without trading signals."""

from __future__ import annotations

_FLAGS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("casino_like_markets", ("daytrad", "meme stock", "meme-stock", "casino", "binary option", "zero-sum"), 25),
    ("leverage_dependency", ("leverage", "leveraged", "margin", "derivative", "option", "future", "hebel"), 25),
    ("volatility_extraction", ("volatility", "high-frequency", "spread", "market maker", "volatilität"), 20),
    ("greater_fool_dependency", ("greater fool", "hype", "pump", "moon", "fomo", "meme", "crypto"), 20),
    ("liquidity_trap", ("illiquid", "exit liquidity", "lock-up", "cannot sell", "liquiditätsfalle"), 10),
)


def audit_speculation(text: str) -> tuple[tuple[str, ...], int]:
    lower = text.lower()
    flags = tuple(flag for flag, terms, _ in _FLAGS if any(term in lower for term in terms))
    score = min(100, sum(weight for flag, _, weight in _FLAGS if flag in flags))
    return flags, score
