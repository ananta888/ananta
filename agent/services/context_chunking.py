"""Bounded text chunking for prompt context splitting (CMP-001).

``chunk_text`` splits ``text`` into pieces of at most ``max_chars`` characters
without dropping, reordering or duplicating any character outside the declared
overlap. Breaks prefer whitespace when a break fits inside ``max_chars``; otherwise
the cut is hard.
"""
from __future__ import annotations


def chunk_text(text: str, max_chars: int, overlap: int = 0) -> list[str]:
    """Split ``text`` into chunks of at most ``max_chars`` characters.

    Args:
        text: The input string to split. An empty string yields ``[]``.
        max_chars: Maximum number of characters per chunk (must be >= 1).
        overlap: Number of trailing characters of each chunk that are repeated
            at the start of the next chunk (must satisfy 0 <= overlap < max_chars).

    Returns:
        A list of strings, each with ``len(chunk) <= max_chars``. With
        ``overlap == 0`` the chunks reassemble the input exactly.

    Raises:
        ValueError: If ``max_chars < 1``, ``overlap < 0`` or ``overlap >= max_chars``.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be >= 1, got {max_chars}")
    if overlap < 0:
        raise ValueError(f"overlap must be >= 0, got {overlap}")
    if overlap >= max_chars:
        raise ValueError(
            f"overlap ({overlap}) must be < max_chars ({max_chars})"
        )

    if not text:
        return []

    chunks: list[str] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + max_chars, n)
        segment = text[start:end]

        # Prefer breaking at the last whitespace position within the segment.
        if end < n:  # not the final chunk; look for a break point
            # Search backwards from the end of the segment for whitespace.
            # The break must leave at least (overlap + 1) characters in the
            # current chunk so that the next chunk can start with overlap chars
            # plus at least one new character.
            min_end = start + overlap + 1 if overlap > 0 else start + 1
            cut = -1
            for i in range(len(segment) - 1, max(min_end - start - 1, -1), -1):
                if segment[i] in " \t\r\n":
                    cut = i + 1  # position after the whitespace char
                    break
            if cut != -1:
                end = start + cut
        chunks.append(text[start:end])
        start = end - overlap if overlap > 0 else end

    return chunks
