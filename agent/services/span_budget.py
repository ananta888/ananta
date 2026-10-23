"""Deterministic span budget clamp for prompt context cutting.

Side-effect-free helpers that bound how many spans may appear in a prompt
context and provide an omission marker indicating truncated content.
"""
from __future__ import annotations

OMISSION_MARKER = "..."


def clamp_spans(spans: list, max_spans: int) -> list:
    """Return up to ``max_spans`` leading spans from ``spans``.

    When ``len(spans) <= max_spans`` the input is returned unchanged. Otherwise
    only the first ``max_spans`` spans are kept. Raises ``ValueError`` when
    ``max_spans < 1``.
    """
    if max_spans < 1:
        raise ValueError("max_spans must be >= 1")
    if len(spans) <= max_spans:
        return spans
    return spans[:max_spans]


def omission_marker(max_spans: int) -> str:
    """Return a short marker that fits inside ``max_spans`` characters.

    Returns an empty string when ``max_spans < 2`` (no room for a visible
    marker). Raises ``ValueError`` when ``max_spans < 1``.
    """
    if max_spans < 1:
        raise ValueError("max_spans must be >= 1")
    if max_spans < 2:
        return ""
    return OMISSION_MARKER[:max_spans]
