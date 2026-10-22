"""Deterministic text budget clamping for prompt context cutting.

Side-effect-free helpers that keep a string within a character budget and
provide a short truncation marker that itself fits the remaining budget.
"""
from __future__ import annotations

_TRUNCATION_MARKER = "..."


def clamp_budget(text: str, max_chars: int) -> str:
    """Return ``text`` unchanged when it fits in ``max_chars``, otherwise its first ``max_chars`` characters.

    Raises:
        ValueError: if ``max_chars < 1``.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be >= 1, got {max_chars}")
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def truncation_marker(max_chars: int) -> str:
    """Return a short truncation marker that fits inside ``max_chars``.

    Returns an empty string when ``max_chars < 3`` (the full marker would not fit).

    Raises:
        ValueError: if ``max_chars < 1``.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be >= 1, got {max_chars}")
    if max_chars < 3:
        return ""
    return _TRUNCATION_MARKER[:max_chars]

