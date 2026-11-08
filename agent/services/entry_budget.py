"""Deterministic entry budget clamping for prompt context cutting.

Side-effect-free helpers used to bound how many context entries reach a
model prompt and what marker should signal that the rest were omitted.
No external dependencies; Python standard library only.
"""
from __future__ import annotations

OMISSION_MARKER = "..."


def clamp_entries(entries: list, max_entries: int) -> list:
    """Return entries unchanged when within budget, otherwise leading slice."""
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    if len(entries) <= max_entries:
        return entries
    return entries[:max_entries]


def omission_marker(max_entries: int) -> str:
    """Return a marker that fits inside max_entries, or '' when max_entries < 2."""
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    if max_entries < 2:
        return ""
    marker = OMISSION_MARKER[:max_entries]
    return marker
