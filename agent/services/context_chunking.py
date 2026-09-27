"""Splitting material that does not fit the window into budgeted chunks (LCTX-007/008).

- ``split_ordered``: one long text (document, log, transcript) into chunks in order,
  cut at paragraph, then line, then word boundaries, never above the budget;
- ``pack_parts``: independent parts (files, documents) packed into chunks; a part
  too large for one chunk is split itself. Nothing is dropped.

Token counts use the same estimate as the window check (``agent.context_window``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from agent.context_window import CHARS_PER_TOKEN, estimate_tokens


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    sources: tuple[str, ...] = field(default_factory=tuple)  # part ids a packed chunk contains

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def _pieces(text: str, budget_chars: int) -> list[str]:
    """Units no larger than the budget: paragraphs, else lines, else words, else hard slices."""
    units: list[str] = []
    for paragraph in text.split("\n\n"):
        if len(paragraph) <= budget_chars:
            units.append(paragraph)
            continue
        for line in paragraph.split("\n"):
            if len(line) <= budget_chars:
                units.append(line)
                continue
            words, current = line.split(" "), ""
            for word in words:
                while len(word) > budget_chars:  # a single overlong token
                    if current:
                        units.append(current)
                        current = ""
                    units.append(word[:budget_chars])
                    word = word[budget_chars:]
                candidate = f"{current} {word}" if current else word
                if len(candidate) > budget_chars:
                    units.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                units.append(current)
    return units


def split_ordered(text: str, chunk_tokens: int) -> list[Chunk]:
    """``text`` in order, as chunks of at most ``chunk_tokens`` (estimated)."""
    budget_chars = max(CHARS_PER_TOKEN * 16, int(chunk_tokens) * CHARS_PER_TOKEN)
    chunks: list[str] = []
    current = ""
    for unit in _pieces(str(text or ""), budget_chars):
        joined = f"{current}\n\n{unit}" if current else unit
        if len(joined) > budget_chars and current:
            chunks.append(current)
            current = unit
        else:
            current = joined
    if current.strip():
        chunks.append(current)
    return [Chunk(index, value) for index, value in enumerate(chunks)]


def pack_parts(parts: Iterable[tuple[str, str]], chunk_tokens: int) -> list[Chunk]:
    """Independent ``(part_id, text)`` parts packed into chunks of at most ``chunk_tokens``.

    A part too large for one chunk is split itself."""
    budget_chars = max(CHARS_PER_TOKEN * 16, int(chunk_tokens) * CHARS_PER_TOKEN)
    chunks: list[tuple[str, list[str]]] = []
    current_text, current_ids = "", []
    for part_id, text in parts:
        block = f"### {part_id}\n{text}"
        if len(block) > budget_chars:  # split the part itself, each piece labelled with its origin
            if current_text:
                chunks.append((current_text, current_ids))
                current_text, current_ids = "", []
            for piece in split_ordered(text, chunk_tokens - estimate_tokens(f"### {part_id} (x/y)\n")):
                chunks.append((f"### {part_id} ({piece.index + 1})\n{piece.text}", [part_id]))
            continue
        joined = f"{current_text}\n\n{block}" if current_text else block
        if len(joined) > budget_chars:
            chunks.append((current_text, current_ids))
            current_text, current_ids = block, [part_id]
        else:
            current_text, current_ids = joined, current_ids + [part_id]
    if current_text:
        chunks.append((current_text, current_ids))
    return [Chunk(index, text, tuple(dict.fromkeys(ids))) for index, (text, ids) in enumerate(chunks)]


def total_chars(chunks: Sequence[Chunk]) -> int:
    return sum(len(chunk.text) for chunk in chunks)
