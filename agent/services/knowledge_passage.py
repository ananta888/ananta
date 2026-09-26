"""Pick the passage of an indexed record that actually matches a query.

Whole-file index records made citations useless: a hit showed the first
characters of the file, not the place that matched, and carried no line.
``best_passage`` scores every line of the record's raw content by the query
tokens it contains, grows a window around the best line up to a character
budget and reports the window's 1-based line range, offset by the record's
own ``start_line`` when the index chunked the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-z0-9_]{3,}")
_CONTEXT_LINES_BEFORE = 2


@dataclass(frozen=True)
class Passage:
    text: str
    line_start: int
    line_end: int


def query_tokens(query: str) -> list[str]:
    """Lower-case search tokens; ``-`` and ``_`` match each other like in the ranking."""
    normalized = str(query or "").lower().replace("-", "_")
    return list(dict.fromkeys(_TOKEN.findall(normalized)))


def best_passage(content: str, query: str, *, max_chars: int, base_line: int = 1) -> Passage | None:
    """The best-matching window of ``content`` within ``max_chars``; ``None`` without content."""
    lines = str(content or "").splitlines()
    if not lines or max_chars <= 0:
        return None
    tokens = query_tokens(query)
    best = _best_line(lines, tokens)
    start = max(0, best - _CONTEXT_LINES_BEFORE)
    end, used = start, 0
    while end < len(lines) and used + len(lines[end]) + 1 <= max_chars:
        used += len(lines[end]) + 1
        end += 1
    if end == start:  # a single line longer than the budget
        end = start + 1
    text = "\n".join(lines[start:end])[:max_chars].strip("\n")
    first = max(1, int(base_line or 1))
    return Passage(text=text, line_start=first + start, line_end=first + end - 1)


def _best_line(lines: list[str], tokens: list[str]) -> int:
    if not tokens:
        return 0
    best_index, best_score = 0, 0
    for index, line in enumerate(lines):
        haystack = line.lower().replace("-", "_")
        score = sum(1 for token in tokens if token in haystack)
        if score > best_score:
            best_index, best_score = index, score
    return best_index
