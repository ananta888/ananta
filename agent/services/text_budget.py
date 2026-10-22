"""Deterministic text budget clamping for prompt context cutting.

Side-effect-free helpers that keep a string within a character budget and
provide a short truncation marker that itself fits the remaining budget.

Used by CMP-2502 as a deterministic, side-effect-free clamp for cutting
prompt context to a fixed character budget.
"""
from __future__ import annotations

_TRUNCATION_MARKER = "..."


def clamp_budget(text: str, max_chars: int) -> str:
    """Return ``text`` unchanged when it fits in ``max_chars``, otherwise its first ``max_chars`` characters.

    The leading characters are preserved and the result is always exactly
    ``max_chars`` characters long when ``len(text) > max_chars``.

    Args:
        text: The input string to fit into the budget.
        max_chars: Maximum number of characters allowed. Must be >= 1.

    Returns:
        The input unchanged if it fits, otherwise its prefix of length
        ``max_chars``.

    Raises:
        ValueError: If ``max_chars < 1``.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be >= 1, got {max_chars}")
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def truncation_marker(max_chars: int) -> str:
    """Return a short truncation marker that fits inside ``max_chars``.

    The marker is the literal '...' sliced to fit when the budget allows it;
    it is an empty string when ``max_chars < 3`` because the full marker would
    not fit. Returns a value whose length is always <= max_chars and, for
    max_chars >= 3, equals '_TRUNCATION_MARKER'.

    Args:
        max_chars: Maximum number of characters allowed for the marker. Must be >= 1.

    Returns:
        '' when ``max_chars < 3``, otherwise a marker of length min(3,
        ``max_chars``). Since the check already guarantees >= 3 here, this is
        always the full '_TRUNCATION_MARKER' in practice.

    Raises:
        ValueError: If ``max_chars < 1``.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be >= 1, got {max_chars}")
    if max_chars < 3:
        return ""
    return _TRUNCATION_MARKER[:max_chars]
