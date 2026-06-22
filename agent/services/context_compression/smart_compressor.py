"""
HCCA-007 — Smart Compressor

Deterministic, strategy-based content compressor. Pure Python, no LLM calls,
no external dependencies. Routes by content_type to the right strategy.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Log lines to always preserve regardless of noise removal
_PRESERVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(ERROR|EXCEPTION|TRACEBACK|CRITICAL|FATAL)\b", re.IGNORECASE),
    re.compile(r"\bWARN(ING)?\b", re.IGNORECASE),
]

# Common log noise — lines that are safe to drop
_LOG_NOISE: re.Pattern[str] = re.compile(
    r"^\s*(?:DEBUG|TRACE|VERBOSE)\b", re.IGNORECASE | re.MULTILINE
)

# Timestamp prefix pattern (ISO-ish or epoch): drop it as "redundant" indicator
_TIMESTAMP_PREFIX: re.Pattern[str] = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s+",
    re.MULTILINE,
)


@dataclass(frozen=True)
class SmartCompressionResult:
    content: str
    strategy_used: str
    char_before: int
    char_after: int
    lines_removed: int
    quality_hint: float  # estimated quality 0.0–1.0


class SmartCompressor:
    """Route content to the right deterministic compression strategy."""

    # Content-type → strategy method name
    _STRATEGY_MAP: dict[str, str] = {
        "json":                    "_compress_json",
        "log":                     "_compress_log",
        "search_results":          "_compress_search_results",
        "rag_results":             "_compress_search_results",
        "codecompass_symbol_list": "_compress_search_results",
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compress(
        self,
        content: str,
        content_type: str,
        target_reduction_percent: float = 35.0,
    ) -> SmartCompressionResult:
        """Compress *content* using the strategy for *content_type*."""
        strategy_name = self._STRATEGY_MAP.get(content_type, "_compress_generic")
        strategy = getattr(self, strategy_name)
        try:
            result = strategy(content, target_reduction_percent)
        except Exception as exc:  # pylint: disable=broad-except
            log.warning(
                "SmartCompressor: strategy %s failed (%s); falling back to generic",
                strategy_name,
                exc,
            )
            result = self._compress_generic(content, target_reduction_percent)
        return result

    # ------------------------------------------------------------------
    # Strategy: JSON
    # ------------------------------------------------------------------

    def _compress_json(
        self, content: str, target_reduction_percent: float
    ) -> SmartCompressionResult:
        char_before = len(content)
        strategy = "json"

        try:
            obj = json.loads(content.strip())
        except (json.JSONDecodeError, ValueError):
            log.debug("SmartCompressor._compress_json: parse failed — falling back to generic")
            result = self._compress_generic(content, target_reduction_percent)
            return SmartCompressionResult(
                content=result.content,
                strategy_used="json_fallback_generic",
                char_before=result.char_before,
                char_after=result.char_after,
                lines_removed=result.lines_removed,
                quality_hint=result.quality_hint,
            )

        obj = self._prune_json(obj, max_depth=4, max_str_len=200)
        try:
            compressed = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            compressed = content

        char_after = len(compressed)
        quality_hint = 0.85 if char_after < char_before else 0.95
        return SmartCompressionResult(
            content=compressed,
            strategy_used=strategy,
            char_before=char_before,
            char_after=char_after,
            lines_removed=0,
            quality_hint=quality_hint,
        )

    # ------------------------------------------------------------------
    # Strategy: Log
    # ------------------------------------------------------------------

    def _compress_log(
        self, content: str, target_reduction_percent: float
    ) -> SmartCompressionResult:
        char_before = len(content)
        lines = content.splitlines()
        original_count = len(lines)

        # Always keep lines matching preservation patterns
        kept: list[str] = []
        prev: str | None = None
        for line in lines:
            if self._is_critical_line(line):
                kept.append(line)
                prev = line
                continue
            # Drop noise
            if _LOG_NOISE.match(line):
                continue
            # Deduplicate consecutive identical lines
            if line == prev:
                continue
            kept.append(line)
            prev = line

        lines_removed = original_count - len(kept)

        # If still too long, keep first 60% and last 20% with omission marker
        target_chars = int(char_before * (1 - target_reduction_percent / 100))
        result_text = "\n".join(kept)
        if len(result_text) > max(char_before - 10, target_chars):
            result_text = self._head_tail(kept, target_chars)

        char_after = len(result_text)
        quality_hint = 0.80 if lines_removed > 0 else 0.95
        return SmartCompressionResult(
            content=result_text,
            strategy_used="log",
            char_before=char_before,
            char_after=char_after,
            lines_removed=lines_removed,
            quality_hint=quality_hint,
        )

    # ------------------------------------------------------------------
    # Strategy: Search / RAG / symbol list results
    # ------------------------------------------------------------------

    def _compress_search_results(
        self, content: str, target_reduction_percent: float
    ) -> SmartCompressionResult:
        char_before = len(content)
        lines = content.splitlines()
        original_count = len(lines)

        # Deduplicate source paths (assume lines containing "/" or "." are paths/symbols)
        seen_paths: set[str] = set()
        kept: list[str] = []
        for line in lines:
            stripped = line.strip()
            # Rough heuristic: path/symbol lines are short and contain "/" or "."
            is_path = len(stripped) < 200 and ("/" in stripped or "." in stripped)
            if is_path:
                if stripped in seen_paths:
                    continue
                seen_paths.add(stripped)
            # Truncate long snippet lines
            if len(line) > 300:
                line = line[:297] + "…"
            kept.append(line)

        lines_removed = original_count - len(kept)

        # Apply top-N budget if still exceeding target
        target_chars = int(char_before * (1 - target_reduction_percent / 100))
        result_text = "\n".join(kept)
        if len(result_text) > target_chars:
            # Keep top N lines by position (ranked results)
            n = max(1, int(len(kept) * (1 - target_reduction_percent / 100)))
            result_text = "\n".join(kept[:n])
            omitted = len(kept) - n
            if omitted > 0:
                result_text += f"\n… [{omitted} result(s) omitted] …"

        char_after = len(result_text)
        quality_hint = 0.80
        return SmartCompressionResult(
            content=result_text,
            strategy_used="search_results",
            char_before=char_before,
            char_after=char_after,
            lines_removed=lines_removed,
            quality_hint=quality_hint,
        )

    # ------------------------------------------------------------------
    # Strategy: Generic (fallback)
    # ------------------------------------------------------------------

    def _compress_generic(
        self, content: str, target_reduction_percent: float
    ) -> SmartCompressionResult:
        char_before = len(content)
        lines = content.splitlines()
        original_count = len(lines)

        # 1. Collapse blank lines (max 1 consecutive)
        kept: list[str] = []
        blank_streak = 0
        for line in lines:
            if not line.strip():
                blank_streak += 1
                if blank_streak <= 1:
                    kept.append(line)
            else:
                blank_streak = 0
                kept.append(line)

        # 2. Deduplicate repeated paragraphs (≥3-line blocks)
        kept = self._dedup_paragraphs(kept)
        lines_removed = original_count - len(kept)

        result_text = "\n".join(kept)

        # 3. If still too long: keep first 40% + last 20%
        target_chars = int(char_before * (1 - target_reduction_percent / 100))
        if len(result_text) > target_chars:
            result_text = self._head_tail(kept, target_chars)

        char_after = len(result_text)
        quality_hint = 0.75
        return SmartCompressionResult(
            content=result_text,
            strategy_used="generic",
            char_before=char_before,
            char_after=char_after,
            lines_removed=lines_removed,
            quality_hint=quality_hint,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_critical_line(line: str) -> bool:
        return any(p.search(line) for p in _PRESERVE_PATTERNS)

    @staticmethod
    def _prune_json(obj: object, max_depth: int, max_str_len: int, _depth: int = 0) -> object:
        """Recursively prune JSON object: remove nulls/empties, truncate strings, cap depth."""
        if _depth >= max_depth:
            if isinstance(obj, (dict, list)):
                return f"… [depth>{max_depth} omitted] …"
            return obj

        if isinstance(obj, dict):
            pruned: dict = {}
            for k, v in obj.items():
                if v is None or v == "" or v == [] or v == {}:
                    continue
                pruned[k] = SmartCompressor._prune_json(v, max_depth, max_str_len, _depth + 1)
            return pruned

        if isinstance(obj, list):
            # Deduplicate simple scalar lists; for complex lists just prune each item
            pruned_list = [
                SmartCompressor._prune_json(item, max_depth, max_str_len, _depth + 1)
                for item in obj
            ]
            # Remove exact duplicates while preserving order
            seen: list = []
            for item in pruned_list:
                try:
                    if item not in seen:
                        seen.append(item)
                except TypeError:
                    seen.append(item)
            return seen

        if isinstance(obj, str) and len(obj) > max_str_len:
            half = max_str_len // 2
            return obj[:half] + "…"

        return obj

    @staticmethod
    def _head_tail(lines: list[str], target_chars: int) -> str:
        """Keep first 40% and last 20% of lines with omission marker."""
        total = len(lines)
        head_n = max(1, int(total * 0.40))
        tail_n = max(1, int(total * 0.20))
        # Avoid overlap
        if head_n + tail_n >= total:
            return "\n".join(lines)
        head = lines[:head_n]
        tail = lines[total - tail_n:]
        omitted = total - head_n - tail_n
        return "\n".join(head) + f"\n… [{omitted} lines omitted] …\n" + "\n".join(tail)

    @staticmethod
    def _dedup_paragraphs(lines: list[str]) -> list[str]:
        """Remove repeated paragraph blocks (≥3 identical consecutive lines)."""
        # Simple approach: detect and remove exact duplicate windows of 3+ lines
        result: list[str] = []
        seen_blocks: set[str] = set()
        i = 0
        while i < len(lines):
            # Try to detect block of 3 lines
            if i + 2 < len(lines):
                block = "\n".join(lines[i : i + 3])
                if block in seen_blocks:
                    i += 3
                    continue
                seen_blocks.add(block)
            result.append(lines[i])
            i += 1
        return result
