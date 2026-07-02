"""ContextProvider Port — AUG-001

Neutral interface for context retrieval providers. No Augment-specific types.
Supports CodeCompass, Augment, and Fake/test providers transparently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ContextScope:
    """Policy-controlled scope for a retrieval request."""

    workspace_id: str
    allowed_paths: list[str]
    denied_paths: list[str]
    max_results: int = 10
    timeout_seconds: int = 30
    correlation_id: str | None = None


@dataclass
class ContextItem:
    """Single context hit from any provider. Policy-controlled."""

    item_id: str
    provider: str
    path: str                   # relative path in workspace
    symbol: str | None          # function/class name if applicable
    line_start: int | None
    line_end: int | None
    snippet: str                # max 2000 chars, may be truncated
    score: float                # 0.0-1.0 relevance score
    reason: str                 # why this is relevant
    source_kind: str            # "ast_call" | "import" | "doc" | "keyword" | "semantic"
    redaction_state: str        # "clean" | "truncated" | "redacted" | "blocked"
    warnings: list[str]
    correlation_id: str | None

    # Evidence/Confidence (extended for COSMOS-014 compatibility)
    confidence: float = 0.5     # 0.0-1.0
    freshness: float = 1.0      # 0.0-1.0
    policy_status: str = "allowed"  # "allowed" | "denied" | "uncertain"


@dataclass
class ContextProviderResult:
    """Normalized result from any context provider."""

    provider: str               # "codecompass" | "augment" | "fake"
    query: str
    workspace_ref: str
    items: list[ContextItem]
    provider_metadata: dict[str, Any]
    truncated: bool
    error: str | None


@dataclass
class ProviderHealth:
    provider: str
    status: str                 # "ok" | "degraded" | "unavailable" | "misconfigured"
    message: str
    checks: dict[str, bool]     # {check_name: passed}


@dataclass
class ProviderCapabilities:
    provider: str
    supports_semantic_search: bool
    supports_symbol_lookup: bool
    supports_cross_repo: bool
    max_results: int
    supports_streaming: bool


@runtime_checkable
class ContextProvider(Protocol):
    def retrieve(self, query: str, scope: ContextScope) -> ContextProviderResult: ...
    def healthcheck(self) -> ProviderHealth: ...
    def capabilities(self) -> ProviderCapabilities: ...
