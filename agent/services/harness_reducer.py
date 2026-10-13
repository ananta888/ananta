"""Evidence-preserving reducer with a fail-closed verification fallback (SEH-007).

The reducer may shrink logs/tool output, but every retained quote must resolve to
an exact span of the original. Quotes are machine-verified against the source
(``original[start:end] == quote``). On any mismatch, an empty selection or a
non-verbatim reduction the reducer falls back to the **unabridged** observation.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

_CHARS_PER_TOKEN = 4


def _tokens(text: str) -> int:
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    text: str
    source_start: int
    source_end: int

    def to_mapping(self) -> dict:
        return {"text": self.text, "source_start": self.source_start, "source_end": self.source_end}


@dataclass(frozen=True, slots=True)
class Reduction:
    reduced_text: str
    spans: tuple[EvidenceSpan, ...]
    fallback: bool
    reason: str | None
    original_tokens: int
    reduced_tokens: int

    @property
    def saved_tokens(self) -> int:
        return max(0, self.original_tokens - self.reduced_tokens)


SpanSelector = Callable[[str], Iterable[tuple[int, int, str]]]


class EvidencePreservingReducer:
    def reduce(self, original: str, *, select: SpanSelector) -> Reduction:
        if not isinstance(original, str):
            raise ValueError("reducer_original_invalid")
        original_tokens = _tokens(original)
        try:
            candidates = list(select(original))
        except Exception:
            return self._fallback(original, original_tokens, "reducer_selector_error")
        spans: list[EvidenceSpan] = []
        for candidate in candidates:
            if not isinstance(candidate, tuple) or len(candidate) != 3:
                return self._fallback(original, original_tokens, "reducer_span_shape_invalid")
            start, end, quote = candidate
            if (type(start) is not int or type(end) is not int or not isinstance(quote, str)
                    or not 0 <= start <= end <= len(original)):
                return self._fallback(original, original_tokens, "reducer_span_bounds_invalid")
            if original[start:end] != quote:
                return self._fallback(original, original_tokens, "reducer_span_not_verbatim")
            spans.append(EvidenceSpan(quote, start, end))
        if not spans:
            return self._fallback(original, original_tokens, "reducer_no_evidence")
        reduced_text = "\n".join(span.text for span in spans)
        return Reduction(reduced_text=reduced_text, spans=tuple(spans), fallback=False, reason=None,
                         original_tokens=original_tokens, reduced_tokens=_tokens(reduced_text))

    @staticmethod
    def _fallback(original: str, original_tokens: int, reason: str) -> Reduction:
        return Reduction(reduced_text=original, spans=(), fallback=True, reason=reason,
                         original_tokens=original_tokens, reduced_tokens=original_tokens)

    @staticmethod
    def select_by_markers(markers: Iterable[str]) -> SpanSelector:
        """Convenience selector: the first verbatim occurrence of each marker."""
        def select(original: str) -> list[tuple[int, int, str]]:
            found: list[tuple[int, int, str]] = []
            for marker in markers:
                if not isinstance(marker, str) or not marker:
                    continue
                index = original.find(marker)
                if index >= 0:
                    found.append((index, index + len(marker), marker))
            return found

        return select
