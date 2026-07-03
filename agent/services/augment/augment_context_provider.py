"""AugmentContextProvider — AUG-100 to AUG-104

Context provider backed by Augment MCP (codebase-retrieval tool).

Policy:
- AUG-100: Only active when config.mcp.enabled=True AND healthcheck.is_ready_for_context_provider()
- AUG-101: Default-deny on empty allowed_paths; denied_paths NEVER forwarded to MCP;
           allowed_paths hard-restrict workspace scope
- AUG-102: Routing modes: codecompass_only (default), hybrid_fallback, hybrid_parallel
- AUG-103: max_items, max_snippet_chars, max_total_chars enforced; truncated marked in
           redaction_state; no silent full-file dumps
- AUG-104: Stats (query, provider, routing_mode, hits, scores) stored per-call for
           reproducibility; local vs external origin distinguishable via source_kind
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.services.augment.augment_config import AugmentConfig
from agent.services.context_providers.context_item_normalizer import ContextItemNormalizer
from agent.services.context_providers.context_provider_port import (
    ContextItem,
    ContextProviderResult,
    ContextScope,
    ProviderCapabilities,
    ProviderHealth,
)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class RoutingMode(str, Enum):
    CODECOMPASS_ONLY = "codecompass_only"   # default — local only
    AUGMENT_ONLY = "augment_only"           # Augment MCP only
    HYBRID_FALLBACK = "hybrid_fallback"     # Augment only if local below threshold
    HYBRID_PARALLEL = "hybrid_parallel"     # merge both, score-sorted


ROUTING_MIN_QUALITY_THRESHOLD = 0.4        # below this → hybrid_fallback activates Augment


# ---------------------------------------------------------------------------
# Internal request/response types
# ---------------------------------------------------------------------------

@dataclass
class AugmentRetrievalRequest:
    """Validated MCP call parameters.  denied_paths are intentionally omitted from
    as_mcp_args — they are filtered locally and must never be forwarded (AUG-101)."""

    query: str
    scope: ContextScope | None
    allowed_paths: list[str]
    denied_paths: list[str]
    max_results: int
    tool_name: str

    def as_mcp_args(self) -> dict[str, Any]:
        """Args forwarded to the MCP tool.  denied_paths excluded by design."""
        return {
            "query": self.query,
            "max_results": self.max_results,
            "allowed_paths": self.allowed_paths,
        }


@dataclass
class AugmentRawResult:
    """Raw MCP response before normalization and policy checks."""

    path: str
    snippet: str
    score: float
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    source_kind: str = "augment_mcp"


@dataclass
class AugmentContextProviderStats:
    """Per-call audit record (AUG-104 reproducibility)."""

    query: str
    provider: str
    routing_mode: str
    items_retrieved: int
    items_blocked: int
    items_truncated: int
    total_chars: int
    scope_paths: list[str]
    created_at: float


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class AugmentContextProvider:
    """Context provider backed by the Augment MCP codebase-retrieval tool.

    source_kind="augment_mcp" on returned ContextItems marks them as external
    evidence (AUG-104 local vs external separation).
    """

    PROVIDER_ID = "augment_mcp"
    MAX_SNIPPET_CHARS = 2000
    MAX_TOTAL_CHARS = 30_000
    MAX_ITEMS = 12

    def __init__(
        self,
        *,
        config: AugmentConfig,
        health_status: Any = None,
        mcp_caller: Any = None,
    ) -> None:
        self._config = config
        self._health = health_status
        self._mcp_caller = mcp_caller       # injectable for tests / real MCP bridge
        self._normalizer = ContextItemNormalizer()
        self._stats: list[AugmentContextProviderStats] = []

    # ------------------------------------------------------------------
    # Activation guard (AUG-100)
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        if not self._config.mcp.enabled:
            return False
        if self._health is None:
            return False
        return self._health.is_ready_for_context_provider()

    # ------------------------------------------------------------------
    # Port surface
    # ------------------------------------------------------------------

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.PROVIDER_ID,
            supports_semantic_search=True,
            supports_symbol_lookup=False,
            supports_cross_repo=False,
            max_results=self._config.mcp.max_results,
            supports_streaming=False,
        )

    def health(self) -> ProviderHealth:
        if not self._config.mcp.enabled:
            return ProviderHealth(
                provider=self.PROVIDER_ID,
                status="disabled",
                message="mcp.enabled=False",
                checks={},
            )
        if self._health is None:
            return ProviderHealth(
                provider=self.PROVIDER_ID,
                status="unavailable",
                message="No health check performed",
                checks={},
            )
        ready = self._health.is_ready_for_context_provider()
        return ProviderHealth(
            provider=self.PROVIDER_ID,
            status="ok" if ready else "unavailable",
            message=str(self._health.overall),
            checks={"auggie_binary": ready},
        )

    def retrieve(
        self,
        query: str,
        *,
        scope: ContextScope | None = None,
        max_results: int | None = None,
    ) -> ContextProviderResult:
        """Retrieve context items from Augment MCP.

        Enforces:
        - Provider disabled guard (AUG-100)
        - Default-deny when scope provided with no allowed_paths (AUG-101)
        - denied_paths never forwarded to MCP (AUG-101)
        - Snippet and total-char limits (AUG-103)
        """
        call_id = str(uuid.uuid4())[:8]
        workspace_ref = scope.workspace_id if scope is not None else ""

        if not self.is_enabled():
            return ContextProviderResult(
                provider=self.PROVIDER_ID,
                query=query,
                workspace_ref=workspace_ref,
                items=[],
                provider_metadata={"call_id": call_id, "reason": "provider disabled or unhealthy"},
                truncated=False,
                error="provider_disabled",
            )

        allowed_paths, denied_paths = self._resolve_paths(scope)

        # AUG-101 Default-deny: scope given but no allowed paths resolved
        if not allowed_paths and scope is not None:
            return ContextProviderResult(
                provider=self.PROVIDER_ID,
                query=query,
                workspace_ref=workspace_ref,
                items=[],
                provider_metadata={
                    "call_id": call_id,
                    "reason": "default_deny: scope provided but no allowed_paths",
                },
                truncated=False,
                error="no_allowed_paths",
            )

        n = min(max_results or self.MAX_ITEMS, self._config.mcp.max_results)
        request = AugmentRetrievalRequest(
            query=query,
            scope=scope,
            allowed_paths=allowed_paths,
            denied_paths=denied_paths,
            max_results=n,
            tool_name=self._config.mcp.tool_name,
        )

        raw_results = self._call_mcp(request)
        items, blocked, truncated = self._process_results(raw_results, allowed_paths, denied_paths, scope)

        # AUG-104 per-call audit record
        self._stats.append(
            AugmentContextProviderStats(
                query=query,
                provider=self.PROVIDER_ID,
                routing_mode="direct",
                items_retrieved=len(items),
                items_blocked=blocked,
                items_truncated=truncated,
                total_chars=sum(len(i.snippet) for i in items),
                scope_paths=list(allowed_paths),
                created_at=time.time(),
            )
        )

        return ContextProviderResult(
            provider=self.PROVIDER_ID,
            query=query,
            workspace_ref=workspace_ref,
            items=items,
            provider_metadata={
                "call_id": call_id,
                "blocked": blocked,
                "truncated": truncated,
                "returned": len(items),
            },
            truncated=truncated > 0,
            error=None,
        )

    def last_stats(self) -> AugmentContextProviderStats | None:
        """Return most recent call stats (AUG-104 reproducibility)."""
        return self._stats[-1] if self._stats else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_paths(
        self, scope: ContextScope | None
    ) -> tuple[list[str], list[str]]:
        """Merge scope paths with config security paths.

        - denied_paths: union of scope.denied_paths and config.security.denied_paths
        - allowed_paths: scope.allowed_paths if provided, else config.security.allowed_paths
        """
        config_denied = list(self._config.security.denied_paths)
        config_allowed = list(self._config.security.allowed_paths)

        if scope is None:
            return config_allowed, config_denied

        allowed = list(scope.allowed_paths) or config_allowed
        denied = list(set(list(scope.denied_paths) + config_denied))
        return allowed, denied

    def _call_mcp(self, request: AugmentRetrievalRequest) -> list[AugmentRawResult]:
        """Invoke the MCP caller.  denied_paths are NOT in request.as_mcp_args()."""
        if self._mcp_caller is None:
            return []
        raw = self._mcp_caller(request.as_mcp_args())
        results: list[AugmentRawResult] = []
        for r in raw or []:
            if isinstance(r, dict):
                results.append(AugmentRawResult(**r))
            elif isinstance(r, AugmentRawResult):
                results.append(r)
        return results

    def _process_results(
        self,
        raw: list[AugmentRawResult],
        allowed_paths: list[str],
        denied_paths: list[str],
        scope: ContextScope | None,
    ) -> tuple[list[ContextItem], int, int]:
        """Normalize raw MCP results to ContextItems.

        - Path-filter via ContextItemNormalizer.apply_path_filter (uses always-blocked
          segments too — .env, .git, secrets, node_modules)
        - Snippets truncated to MAX_SNIPPET_CHARS; total budget MAX_TOTAL_CHARS
        - redaction_state set to "truncated" when clipped (AUG-103)
        - source_kind="augment_mcp" signals external origin (AUG-104)
        """
        # Build temporary scope for normalizer path filter
        filter_scope = ContextScope(
            workspace_id=scope.workspace_id if scope is not None else "augment",
            allowed_paths=allowed_paths,
            denied_paths=denied_paths,
        )

        items: list[ContextItem] = []
        blocked = 0
        truncated_count = 0
        total_chars = 0

        for r in raw:
            # AUG-101: apply path filter (denied_paths + always-blocked segments)
            path_allowed, _reason = self._normalizer.apply_path_filter(r.path, filter_scope)
            if not path_allowed:
                blocked += 1
                continue

            snippet = r.snippet or ""

            # AUG-103: snippet budget
            was_truncated = len(snippet) > self.MAX_SNIPPET_CHARS
            if was_truncated:
                snippet = snippet[: self.MAX_SNIPPET_CHARS]
                truncated_count += 1

            # AUG-103: total-chars budget — stop adding items when exceeded
            if total_chars + len(snippet) > self.MAX_TOTAL_CHARS:
                break

            total_chars += len(snippet)

            items.append(
                ContextItem(
                    item_id=f"aug:{r.path}:{r.line_start or 0}",
                    provider=self.PROVIDER_ID,
                    path=r.path,
                    symbol=r.symbol,
                    line_start=r.line_start,
                    line_end=r.line_end,
                    snippet=snippet,
                    score=max(0.0, min(1.0, float(r.score))),
                    reason="augment_mcp_retrieval",
                    # AUG-104: source_kind distinguishes external from local evidence
                    source_kind="augment_mcp",
                    # AUG-103: redaction_state marks truncated snippets
                    redaction_state="truncated" if was_truncated else "clean",
                    warnings=["snippet_truncated"] if was_truncated else [],
                    correlation_id=scope.correlation_id if scope is not None else None,
                    confidence=0.6,     # external provider — lower confidence than local
                    freshness=0.9,
                    policy_status="allowed",
                )
            )

        return items, blocked, truncated_count


# ---------------------------------------------------------------------------
# Router (AUG-102)
# ---------------------------------------------------------------------------

class ProviderRouter:
    """Routes context queries across providers per AUG-102.

    Modes:
    - codecompass_only  (default): local index only
    - augment_only:                MCP only
    - hybrid_fallback:             MCP only if local avg-score < threshold
    - hybrid_parallel:             both; merge-sorted by score, paths deduplicated
    """

    def __init__(
        self,
        *,
        codecompass_provider: Any,
        augment_provider: AugmentContextProvider | None = None,
        mode: RoutingMode = RoutingMode.CODECOMPASS_ONLY,
        min_quality_threshold: float = ROUTING_MIN_QUALITY_THRESHOLD,
    ) -> None:
        self._cc = codecompass_provider
        self._aug = augment_provider
        self._mode = mode
        self._threshold = min_quality_threshold

    def retrieve(
        self,
        query: str,
        *,
        scope: ContextScope | None = None,
        max_results: int | None = None,
    ) -> list[ContextItem]:
        if self._mode == RoutingMode.CODECOMPASS_ONLY:
            result = self._cc.retrieve(query, scope=scope, max_results=max_results)
            return result.items

        if self._mode == RoutingMode.AUGMENT_ONLY:
            if self._aug and self._aug.is_enabled():
                result = self._aug.retrieve(query, scope=scope, max_results=max_results)
                return result.items
            return []

        if self._mode == RoutingMode.HYBRID_FALLBACK:
            local_result = self._cc.retrieve(query, scope=scope, max_results=max_results)
            local_items = local_result.items
            if self._aug and self._aug.is_enabled():
                avg_score = (
                    sum(i.score for i in local_items) / len(local_items)
                    if local_items
                    else 0.0
                )
                if avg_score < self._threshold:
                    aug_result = self._aug.retrieve(query, scope=scope, max_results=max_results)
                    seen_paths = {i.path for i in local_items}
                    extra = [i for i in aug_result.items if i.path not in seen_paths]
                    return sorted(local_items + extra, key=lambda i: i.score, reverse=True)
            return local_items

        if self._mode == RoutingMode.HYBRID_PARALLEL:
            local_result = self._cc.retrieve(query, scope=scope, max_results=max_results)
            local_items = local_result.items
            aug_items: list[ContextItem] = []
            if self._aug and self._aug.is_enabled():
                aug_result = self._aug.retrieve(query, scope=scope, max_results=max_results)
                aug_items = aug_result.items
            # Merge-sort by score; deduplicate on path (keep higher-score hit)
            seen: set[str] = set()
            merged: list[ContextItem] = []
            for item in sorted(local_items + aug_items, key=lambda i: i.score, reverse=True):
                if item.path not in seen:
                    seen.add(item.path)
                    merged.append(item)
            return merged

        return []
