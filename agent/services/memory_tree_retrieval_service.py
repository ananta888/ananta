"""OHA-011: MemoryTreeRetrievalService — retrieval over Memory Tree scopes.

Provides query-driven access to the source/topic/global tree without
replacing the existing KnowledgeIndexRetrievalService. Both services
can be used side-by-side; this one answers from the Memory Tree's own
chunk/node tables.

Sensitivity policy is enforced before any chunk or summary is returned.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.services.memory_tree_store_service import (
    MemoryTreeChunkDB,
    MemoryTreeNodeDB,
    MemoryTreeStoreService,
    get_memory_tree_store_service,
)

logger = logging.getLogger(__name__)

_SENSITIVITY_ORDER = ["public", "internal", "internal_high", "secret", "credential", "security_sensitive"]

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class MemoryChunkResult:
    chunk_id: str
    source_id: str
    source_type: str
    label: str
    content: str
    sensitivity: str
    lifecycle: str
    scope: str
    provenance_ref: Optional[str]
    score: float = 1.0


@dataclass
class MemoryNodeResult:
    node_id: str
    node_type: str
    label: str
    summary: Optional[str]
    provenance_refs: list[str]
    child_chunk_ids: list[str]
    leaf_count: int


@dataclass
class MemoryRetrievalResult:
    query: str
    scope: str                          # "source" | "topic" | "global" | "any"
    chunks: list[MemoryChunkResult] = field(default_factory=list)
    summary_node: Optional[MemoryNodeResult] = None
    drilldown_refs: list[str] = field(default_factory=list)
    total_chunks: int = 0
    filtered_by_policy: int = 0
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sensitivity_idx(label: str) -> int:
    try:
        return _SENSITIVITY_ORDER.index(label)
    except ValueError:
        return 1  # treat unknowns as "internal"


def _policy_allows(chunk_sensitivity: str, ceiling: str) -> bool:
    return _sensitivity_idx(chunk_sensitivity) <= _sensitivity_idx(ceiling)


def _chunk_to_result(chunk: MemoryTreeChunkDB, score: float = 1.0) -> MemoryChunkResult:
    return MemoryChunkResult(
        chunk_id=chunk.id,
        source_id=chunk.source_id,
        source_type=chunk.source_type,
        label=chunk.label,
        content=chunk.content,
        sensitivity=chunk.sensitivity,
        lifecycle=chunk.lifecycle,
        scope=chunk.scope,
        provenance_ref=chunk.provenance_ref,
        score=score,
    )


def _node_to_result(node: MemoryTreeNodeDB) -> MemoryNodeResult:
    return MemoryNodeResult(
        node_id=node.id,
        node_type=node.node_type,
        label=node.label,
        summary=node.summary,
        provenance_refs=list(node.provenance_refs or []),
        child_chunk_ids=list(node.child_chunk_ids or []),
        leaf_count=len(node.child_chunk_ids or []),
    )


def _keyword_score(chunk: MemoryTreeChunkDB, tokens: list[str]) -> float:
    if not tokens:
        return 1.0
    text = (chunk.label + " " + chunk.content).lower()
    hits = sum(1 for t in tokens if t in text)
    return round(hits / len(tokens), 3)


# ---------------------------------------------------------------------------
# MemoryTreeRetrievalService
# ---------------------------------------------------------------------------

class MemoryTreeRetrievalService:
    """Query the Memory Tree across source/topic/global scopes."""

    def __init__(
        self,
        store: MemoryTreeStoreService | None = None,
        sensitivity_ceiling: str = "internal_high",
        max_chunks_per_query: int = 40,
    ) -> None:
        self._store = store or get_memory_tree_store_service()
        self._ceiling = sensitivity_ceiling
        self._max_chunks = max_chunks_per_query

    # ------------------------------------------------------------------
    # Source scope — retrieve from a single known source_id
    # ------------------------------------------------------------------

    def retrieve_source(
        self,
        source_id: str,
        *,
        query: str = "",
        lifecycle: str | None = "sealed",
        sensitivity_ceiling: str | None = None,
        with_summary: bool = True,
        limit: int | None = None,
    ) -> MemoryRetrievalResult:
        ceiling = sensitivity_ceiling or self._ceiling
        tokens = query.lower().split() if query else []
        max_n = limit or self._max_chunks

        raw_chunks = self._store.get_chunks_by_source(
            source_id, lifecycle=lifecycle, limit=max_n * 4
        )

        result = MemoryRetrievalResult(query=query, scope="source")
        for chunk in raw_chunks:
            if not _policy_allows(chunk.sensitivity, ceiling):
                result.filtered_by_policy += 1
                continue
            score = _keyword_score(chunk, tokens)
            if tokens and score == 0.0:
                continue
            result.chunks.append(_chunk_to_result(chunk, score))

        result.chunks.sort(key=lambda c: c.score, reverse=True)
        result.chunks = result.chunks[:max_n]
        result.total_chunks = len(result.chunks)

        if with_summary:
            node = self._store.get_node("source", f"source:{source_id}")
            if node is not None:
                result.summary_node = _node_to_result(node)
                result.drilldown_refs = list(node.child_chunk_ids or [])[:20]

        return result

    # ------------------------------------------------------------------
    # Topic scope — retrieve from a named topic node
    # ------------------------------------------------------------------

    def retrieve_topic(
        self,
        topic_label: str,
        *,
        query: str = "",
        sensitivity_ceiling: str | None = None,
        with_summary: bool = True,
        limit: int | None = None,
    ) -> MemoryRetrievalResult:
        ceiling = sensitivity_ceiling or self._ceiling
        tokens = query.lower().split() if query else []
        max_n = limit or self._max_chunks

        result = MemoryRetrievalResult(query=query, scope="topic")

        node = self._store.get_node("topic", f"topic:{topic_label}")
        if node is None:
            result.meta["reason"] = "topic_node_not_found"
            return result

        if with_summary:
            result.summary_node = _node_to_result(node)

        # Expand child chunk IDs into actual chunk objects via source lookup
        source_ids = list(node.provenance_refs or [])
        for sid in source_ids:
            raw_chunks = self._store.get_chunks_by_source(sid, limit=100)
            for chunk in raw_chunks:
                if not _policy_allows(chunk.sensitivity, ceiling):
                    result.filtered_by_policy += 1
                    continue
                score = _keyword_score(chunk, tokens)
                if tokens and score == 0.0:
                    continue
                result.chunks.append(_chunk_to_result(chunk, score))

        result.chunks.sort(key=lambda c: c.score, reverse=True)
        result.chunks = result.chunks[:max_n]
        result.total_chunks = len(result.chunks)
        result.drilldown_refs = source_ids[:10]

        return result

    # ------------------------------------------------------------------
    # Global scope — retrieve from a global digest node
    # ------------------------------------------------------------------

    def retrieve_global(
        self,
        scope_label: str,
        *,
        query: str = "",
        sensitivity_ceiling: str | None = None,
        with_summary: bool = True,
        limit: int | None = None,
    ) -> MemoryRetrievalResult:
        ceiling = sensitivity_ceiling or self._ceiling
        tokens = query.lower().split() if query else []
        max_n = limit or self._max_chunks

        result = MemoryRetrievalResult(query=query, scope="global")

        node = self._store.get_node("global", f"global:{scope_label}")
        if node is None:
            result.meta["reason"] = "global_node_not_found"
            return result

        if with_summary:
            result.summary_node = _node_to_result(node)

        source_ids = list(node.provenance_refs or [])
        for sid in source_ids:
            raw_chunks = self._store.get_chunks_by_source(sid, limit=50)
            for chunk in raw_chunks:
                if not _policy_allows(chunk.sensitivity, ceiling):
                    result.filtered_by_policy += 1
                    continue
                score = _keyword_score(chunk, tokens)
                if tokens and score == 0.0:
                    continue
                result.chunks.append(_chunk_to_result(chunk, score))
                if len(result.chunks) >= max_n:
                    break
            if len(result.chunks) >= max_n:
                break

        result.chunks.sort(key=lambda c: c.score, reverse=True)
        result.total_chunks = len(result.chunks)
        result.drilldown_refs = source_ids[:10]

        return result

    # ------------------------------------------------------------------
    # Cross-scope search — intent-based dispatcher
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        source_ids: list[str] | None = None,
        topic_labels: list[str] | None = None,
        global_scope: str | None = None,
        sensitivity_ceiling: str | None = None,
        limit: int | None = None,
    ) -> MemoryRetrievalResult:
        """Unified entry point: tries source → topic → global in priority order.

        Architecture and bugfix intents map well to source scope (specific
        source_id). Config retrieval intents map to topic scope.
        Global scope gives a wide but shallow overview.
        """
        ceiling = sensitivity_ceiling or self._ceiling
        max_n = limit or self._max_chunks
        combined = MemoryRetrievalResult(query=query, scope="any")

        if source_ids:
            for sid in source_ids:
                r = self.retrieve_source(
                    sid, query=query, lifecycle=None,
                    sensitivity_ceiling=ceiling, limit=max_n
                )
                combined.chunks.extend(r.chunks)
                combined.filtered_by_policy += r.filtered_by_policy
                if r.summary_node and combined.summary_node is None:
                    combined.summary_node = r.summary_node

        if topic_labels:
            for tl in topic_labels:
                r = self.retrieve_topic(
                    tl, query=query, sensitivity_ceiling=ceiling, limit=max_n
                )
                combined.chunks.extend(r.chunks)
                combined.filtered_by_policy += r.filtered_by_policy

        if global_scope:
            r = self.retrieve_global(
                global_scope, query=query, sensitivity_ceiling=ceiling, limit=max_n
            )
            combined.chunks.extend(r.chunks)
            combined.filtered_by_policy += r.filtered_by_policy

        # Deduplicate by chunk_id and re-rank
        seen: set[str] = set()
        deduped: list[MemoryChunkResult] = []
        for c in combined.chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                deduped.append(c)

        deduped.sort(key=lambda c: c.score, reverse=True)
        combined.chunks = deduped[:max_n]
        combined.total_chunks = len(combined.chunks)

        return combined


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_memory_tree_retrieval_service: MemoryTreeRetrievalService | None = None


def get_memory_tree_retrieval_service(cfg: dict | None = None) -> MemoryTreeRetrievalService:
    global _memory_tree_retrieval_service
    if _memory_tree_retrieval_service is None:
        mt_cfg = (cfg or {}).get("memory_tree", {})
        _memory_tree_retrieval_service = MemoryTreeRetrievalService(
            sensitivity_ceiling=mt_cfg.get("sensitivity_ceiling", "internal_high"),
            max_chunks_per_query=mt_cfg.get("max_chunks_per_query", 40),
        )
    return _memory_tree_retrieval_service
