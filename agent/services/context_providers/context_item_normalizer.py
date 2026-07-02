"""ContextItemNormalizer — AUG-002

Normalizes raw provider results to canonical ContextItem format.
Also contains FakeContextProvider for unit tests.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from agent.services.context_providers.context_provider_port import (
    ContextItem,
    ContextProviderResult,
    ContextScope,
    ProviderCapabilities,
    ProviderHealth,
)

MAX_SNIPPET_CHARS = 2000
MAX_ITEMS = 20

# Paths that are always blocked regardless of scope configuration.
# Checked as path segments (any component in the path hierarchy).
_ALWAYS_BLOCKED_SEGMENTS: frozenset[str] = frozenset({
    ".env",
    ".git",
    "secrets",
    "node_modules",
})

# Patterns used to detect and redact secrets from snippets.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                                           # OpenAI-style keys
    re.compile(r"AKIA[0-9A-Z]{16}"),                                               # AWS access keys
    re.compile(r"-----BEGIN [A-Z ]+KEY-----.*?-----END [A-Z ]+KEY-----", re.DOTALL),  # PEM keys
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                                            # GitHub PAT
    re.compile(r"xoxb-[0-9A-Za-z-]+"),                                            # Slack bot token
    re.compile(r"(?i)(?:password|passwd|secret|token|api_key)\s*[=:]\s*\S+"),     # key=value patterns
]


class ContextItemNormalizer:
    """Normalizes raw provider results to canonical ContextItem format."""

    def normalize_item(
        self,
        raw: dict[str, Any],
        *,
        provider: str,
        query: str,
        scope: ContextScope,
    ) -> ContextItem | None:
        """Normalize a raw dict from any provider to ContextItem.

        Returns None if the item should be excluded (denied path, no snippet, etc.)
        """
        path = (
            raw.get("path")
            or raw.get("file")
            or raw.get("filepath")
            or ""
        )
        if not path:
            return None

        allowed, _reason = self.apply_path_filter(str(path), scope)
        if not allowed:
            return None

        snippet = (
            raw.get("snippet")
            or raw.get("content")
            or raw.get("text")
            or ""
        )
        if not snippet:
            return None

        snippet, was_truncated = self.truncate_snippet(str(snippet))
        snippet, was_redacted = self.redact_sensitive_content(snippet)

        if was_redacted:
            redaction_state = "redacted"
        elif was_truncated:
            redaction_state = "truncated"
        else:
            redaction_state = "clean"

        line_start = _safe_int(raw.get("line_start") or raw.get("start_line") or raw.get("line"))
        line_end = _safe_int(raw.get("line_end") or raw.get("end_line"))

        score = float(raw.get("score", 0.5))
        score = max(0.0, min(1.0, score))

        confidence = float(raw.get("confidence", 0.5))
        freshness = float(raw.get("freshness", 1.0))

        warnings: list[str] = []
        if was_truncated:
            warnings.append("snippet_truncated")
        if was_redacted:
            warnings.append("content_redacted")

        return ContextItem(
            item_id=self.build_item_id(provider, str(path), line_start),
            provider=provider,
            path=str(path),
            symbol=raw.get("symbol") or raw.get("function") or raw.get("class_name"),
            line_start=line_start,
            line_end=line_end,
            snippet=snippet,
            score=score,
            reason=raw.get("reason") or raw.get("explanation") or "",
            source_kind=raw.get("source_kind") or "keyword",
            redaction_state=redaction_state,
            warnings=warnings,
            correlation_id=scope.correlation_id,
            confidence=confidence,
            freshness=freshness,
            policy_status="allowed",
        )

    def apply_path_filter(self, path: str, scope: ContextScope) -> tuple[bool, str]:
        """Returns (allowed: bool, reason: str).

        Rules (in order of precedence):
        1. Always-blocked segments (.env, .git, secrets, node_modules) override everything.
        2. scope.denied_paths override scope.allowed_paths.
        3. If scope.allowed_paths is non-empty, path must match at least one allowed prefix.
        4. If scope.allowed_paths is empty, all remaining paths are allowed.
        """
        norm = path.replace("\\", "/")
        segments = [s for s in norm.split("/") if s]

        # Rule 1: always-blocked by segment name
        for segment in segments:
            if segment in _ALWAYS_BLOCKED_SEGMENTS:
                return False, f"always_blocked:{segment}"

        # Rule 2: scope.denied_paths
        for denied in scope.denied_paths:
            denied_norm = denied.replace("\\", "/").rstrip("/")
            if norm == denied_norm or norm.startswith(denied_norm + "/"):
                return False, f"denied_path:{denied}"
            # Also block if the denied segment appears as a path component
            denied_seg = denied_norm.lstrip("./")
            if denied_seg and denied_seg in segments:
                return False, f"denied_path:{denied}"

        # Rule 3: allowed_paths restriction
        if scope.allowed_paths:
            for allowed in scope.allowed_paths:
                allowed_norm = allowed.replace("\\", "/").rstrip("/")
                if norm == allowed_norm or norm.startswith(allowed_norm + "/"):
                    return True, "allowed"
            return False, "not_in_allowed_paths"

        return True, "allowed"

    def truncate_snippet(self, snippet: str, max_chars: int = MAX_SNIPPET_CHARS) -> tuple[str, bool]:
        """Returns (truncated_snippet, was_truncated)."""
        if len(snippet) <= max_chars:
            return snippet, False
        return snippet[:max_chars], True

    def redact_sensitive_content(self, snippet: str) -> tuple[str, bool]:
        """Detects and redacts potential secrets. Returns (redacted_snippet, was_redacted)."""
        was_redacted = False
        for pattern in _SECRET_PATTERNS:
            new_snippet = pattern.sub("[REDACTED]", snippet)
            if new_snippet != snippet:
                was_redacted = True
                snippet = new_snippet
        return snippet, was_redacted

    def build_item_id(self, provider: str, path: str, line_start: int | None) -> str:
        """Deterministic item_id based on provider + path + line."""
        key = f"{provider}:{path}:{line_start!s}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]

    def deduplicate(self, items: list[ContextItem]) -> list[ContextItem]:
        """Remove duplicate items (same path + line range from same provider). Keep highest score."""
        seen: dict[str, ContextItem] = {}
        for item in items:
            key = f"{item.provider}:{item.path}:{item.line_start}:{item.line_end}"
            if key not in seen or item.score > seen[key].score:
                seen[key] = item
        return list(seen.values())

    def sort_by_score(self, items: list[ContextItem]) -> list[ContextItem]:
        """Sort descending by score, stable."""
        return sorted(items, key=lambda x: x.score, reverse=True)


class FakeContextProvider:
    """Test provider that returns configurable fake results."""

    def __init__(
        self,
        name: str = "fake",
        items: list[ContextItem] | None = None,
        health_status: str = "ok",
    ) -> None:
        self._name = name
        self._items = items or []
        self._health_status = health_status
        self._calls: list[dict[str, Any]] = []

    def retrieve(self, query: str, scope: ContextScope) -> ContextProviderResult:
        self._calls.append({"query": query, "scope": scope})
        return ContextProviderResult(
            provider=self._name,
            query=query,
            workspace_ref=scope.workspace_id,
            items=list(self._items),
            provider_metadata={},
            truncated=False,
            error=None,
        )

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self._name,
            status=self._health_status,
            message="fake",
            checks={"ready": self._health_status == "ok"},
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self._name,
            supports_semantic_search=True,
            supports_symbol_lookup=False,
            supports_cross_repo=False,
            max_results=10,
            supports_streaming=False,
        )

    def get_calls(self) -> list[dict[str, Any]]:
        return list(self._calls)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
