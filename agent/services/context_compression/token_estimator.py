"""
HCCA-003 — Token Estimator

Lightweight, dependency-free token estimation.
Rule of thumb: ~4 characters per token (conservative; no tiktoken required).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenMetrics:
    char_count: int
    estimated_tokens: int   # rough estimate: chars / 4
    line_count: int
    word_count: int
    json_key_count: int     # 0 if not JSON
    unique_line_ratio: float  # dedup indicator: unique_lines / total_lines


class TokenEstimator:
    CHARS_PER_TOKEN: int = 4  # conservative estimate without tiktoken

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(self, text: str) -> TokenMetrics:
        """Return TokenMetrics for *text*."""
        char_count = len(text)
        estimated_tokens = max(1, char_count // self.CHARS_PER_TOKEN)

        lines = text.splitlines()
        line_count = len(lines)
        word_count = len(text.split())

        json_key_count = self._count_json_keys(text)

        if line_count > 0:
            unique_line_ratio = len(set(lines)) / line_count
        else:
            unique_line_ratio = 1.0

        return TokenMetrics(
            char_count=char_count,
            estimated_tokens=estimated_tokens,
            line_count=line_count,
            word_count=word_count,
            json_key_count=json_key_count,
            unique_line_ratio=unique_line_ratio,
        )

    def estimate_many(self, texts: list[str]) -> list[TokenMetrics]:
        """Return TokenMetrics for each text in *texts*."""
        return [self.estimate(t) for t in texts]

    def budget_exceeded(self, text: str, budget_tokens: int) -> bool:
        """Return True if estimated tokens exceed *budget_tokens* (0 = no limit)."""
        if budget_tokens <= 0:
            return False
        return self.estimate(text).estimated_tokens > budget_tokens

    def reduction_percent(self, before: str, after: str) -> float:
        """Return percentage token reduction (positive = savings).
        Returns 0.0 if *before* is empty."""
        if not before:
            return 0.0
        before_tokens = self.estimate(before).estimated_tokens
        after_tokens = self.estimate(after).estimated_tokens
        return max(0.0, (before_tokens - after_tokens) / before_tokens * 100.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_json_keys(text: str) -> int:
        """Count top-level JSON keys; returns 0 on parse failure."""
        stripped = text.strip()
        if not stripped.startswith(("{", "[")):
            return 0
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                return len(obj)
            if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                return len(obj[0])
        except (json.JSONDecodeError, IndexError):
            pass
        return 0
