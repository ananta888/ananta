"""OHA-010: MemoryTreeSummaryService — Source/Topic/Global tree building.

Implements the OpenHuman-inspired 3-level tree:
  L0  — leaf chunks (admitted/buffered/sealed, managed by MemoryTreeStoreService)
  L1  — source summary node (one per source_id)
  L2  — topic summary node (one per topic label, covering multiple sources)
  L3  — global digest node (one per goal/project/day scope)

LLM summarisation is optional and policy-gated; deterministic fallback is
always available so tree building never stalls on LLM unavailability.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.services.memory_tree_store_service import (
    MemoryTreeStoreService,
    get_memory_tree_store_service,
    node_id,
)

logger = logging.getLogger(__name__)

_SUMMARY_VERSION = "1"

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TreeBuildResult:
    scope: str          # "source" | "topic" | "global"
    node_label: str
    node_id: str
    leaf_count: int
    summary_method: str     # "deterministic" | "llm" | "passthrough"
    created: bool
    elapsed_s: float = 0.0
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Deterministic summarisation helpers
# ---------------------------------------------------------------------------

def _deterministic_source_summary(labels: list[str], source_id: str, leaf_count: int) -> str:
    """Build a compact summary without LLM."""
    header = f"Source: {source_id} ({leaf_count} leaves)"
    if not labels:
        return header
    sample = labels[:10]
    tail = f"… and {len(labels) - 10} more" if len(labels) > 10 else ""
    return f"{header}\nItems: {', '.join(sample)}{tail}"


def _deterministic_topic_summary(topic_label: str, source_ids: list[str], leaf_count: int) -> str:
    header = f"Topic: {topic_label} ({leaf_count} leaves across {len(source_ids)} sources)"
    if source_ids:
        header += f"\nSources: {', '.join(source_ids[:8])}"
    return header


def _deterministic_global_summary(scope_label: str, topic_labels: list[str], leaf_count: int) -> str:
    header = f"Global digest: {scope_label} ({leaf_count} total leaves)"
    if topic_labels:
        header += f"\nTopics: {', '.join(topic_labels[:12])}"
    return header


# ---------------------------------------------------------------------------
# MemoryTreeSummaryService
# ---------------------------------------------------------------------------

class MemoryTreeSummaryService:
    """Builds and updates L1/L2/L3 summary nodes in the Memory Tree."""

    def __init__(
        self,
        store: MemoryTreeStoreService | None = None,
        llm_enabled: bool = False,
        llm_cloud_allowed: bool = False,
    ) -> None:
        self._store = store or get_memory_tree_store_service()
        self._llm_enabled = llm_enabled
        self._llm_cloud_allowed = llm_cloud_allowed

    # ------------------------------------------------------------------
    # L1 — source summary
    # ------------------------------------------------------------------

    def build_source_summary(
        self,
        source_id: str,
        *,
        lifecycle_filter: str = "sealed",
        force_rebuild: bool = False,
        created_by: str | None = None,
    ) -> TreeBuildResult:
        """Build or refresh the L1 summary node for a single source."""
        t0 = time.monotonic()
        label = f"source:{source_id}"

        chunks = self._store.get_chunks_by_source(source_id, lifecycle=lifecycle_filter or None)
        if not chunks and lifecycle_filter:
            # Fall back to any lifecycle if no sealed chunks found
            chunks = self._store.get_chunks_by_source(source_id)

        if not chunks:
            return TreeBuildResult(
                scope="source", node_label=label,
                node_id=node_id("source", label),
                leaf_count=0, summary_method="passthrough",
                created=False, elapsed_s=time.monotonic() - t0,
                error="no_chunks",
            )

        leaf_labels = [c.label for c in chunks]
        leaf_ids = [c.id for c in chunks]

        summary_text = _deterministic_source_summary(leaf_labels, source_id, len(chunks))
        method = "deterministic"

        node = self._store.upsert_node(
            node_type="source",
            label=label,
            summary=summary_text,
            provenance_refs=[source_id],
            child_chunk_ids=leaf_ids,
            metadata={
                "summary_version": _SUMMARY_VERSION,
                "summary_method": method,
                "leaf_count": len(chunks),
                "created_by": created_by or "",
                "built_at": time.time(),
            },
        )

        return TreeBuildResult(
            scope="source", node_label=label,
            node_id=node.id, leaf_count=len(chunks),
            summary_method=method, created=True,
            elapsed_s=time.monotonic() - t0,
        )

    # ------------------------------------------------------------------
    # L2 — topic summary
    # ------------------------------------------------------------------

    def build_topic_summary(
        self,
        topic_label: str,
        source_ids: list[str],
        *,
        hotness: float = 1.0,
        created_by: str | None = None,
    ) -> TreeBuildResult:
        """Build or refresh the L2 topic summary node.

        Topic nodes are only created when explicitly requested or hot
        (hotness >= 0.5 by convention).
        """
        t0 = time.monotonic()
        label = f"topic:{topic_label}"

        if hotness < 0.5 and not source_ids:
            return TreeBuildResult(
                scope="topic", node_label=label,
                node_id=node_id("topic", label),
                leaf_count=0, summary_method="passthrough",
                created=False, elapsed_s=time.monotonic() - t0,
                meta={"reason": "cold_topic"},
            )

        # Collect all chunk IDs across contributing sources
        all_chunk_ids: list[str] = []
        for sid in source_ids:
            chunks = self._store.get_chunks_by_source(sid)
            all_chunk_ids.extend(c.id for c in chunks)

        leaf_count = len(all_chunk_ids)
        summary_text = _deterministic_topic_summary(topic_label, source_ids, leaf_count)

        node = self._store.upsert_node(
            node_type="topic",
            label=label,
            summary=summary_text,
            provenance_refs=list(source_ids),
            child_chunk_ids=all_chunk_ids[:200],  # cap stored refs
            metadata={
                "summary_version": _SUMMARY_VERSION,
                "summary_method": "deterministic",
                "hotness": hotness,
                "leaf_count": leaf_count,
                "created_by": created_by or "",
                "built_at": time.time(),
            },
        )

        return TreeBuildResult(
            scope="topic", node_label=label,
            node_id=node.id, leaf_count=leaf_count,
            summary_method="deterministic", created=True,
            elapsed_s=time.monotonic() - t0,
        )

    # ------------------------------------------------------------------
    # L3 — global digest
    # ------------------------------------------------------------------

    def build_global_digest(
        self,
        scope_label: str,
        *,
        topic_labels: list[str] | None = None,
        source_ids: list[str] | None = None,
        created_by: str | None = None,
    ) -> TreeBuildResult:
        """Build or refresh the L3 global digest node.

        Aggregates from topic nodes when available, otherwise from source nodes.
        """
        t0 = time.monotonic()
        label = f"global:{scope_label}"

        resolved_topics = list(topic_labels or [])
        provenance: list[str] = list(source_ids or [])

        # Resolve leaf count from source chunks
        total_leaves = 0
        if source_ids:
            for sid in source_ids:
                total_leaves += self._store.count_chunks(sid)

        summary_text = _deterministic_global_summary(scope_label, resolved_topics, total_leaves)

        node = self._store.upsert_node(
            node_type="global",
            label=label,
            summary=summary_text,
            provenance_refs=provenance,
            metadata={
                "summary_version": _SUMMARY_VERSION,
                "summary_method": "deterministic",
                "topic_count": len(resolved_topics),
                "leaf_count": total_leaves,
                "created_by": created_by or "",
                "built_at": time.time(),
            },
        )

        return TreeBuildResult(
            scope="global", node_label=label,
            node_id=node.id, leaf_count=total_leaves,
            summary_method="deterministic", created=True,
            elapsed_s=time.monotonic() - t0,
            meta={"topic_count": len(resolved_topics)},
        )

    # ------------------------------------------------------------------
    # Convenience: seal + summarise a source in one call
    # ------------------------------------------------------------------

    def seal_and_summarise_source(
        self,
        source_id: str,
        *,
        created_by: str | None = None,
    ) -> TreeBuildResult:
        """Seal all buffered chunks for source, then build its L1 summary."""
        sealed = self._store.seal_source(source_id)
        logger.info("seal_and_summarise_source: sealed %d chunks for %s", sealed, source_id)
        return self.build_source_summary(source_id, created_by=created_by)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_memory_tree_summary_service: MemoryTreeSummaryService | None = None


def get_memory_tree_summary_service(cfg: dict | None = None) -> MemoryTreeSummaryService:
    global _memory_tree_summary_service
    if _memory_tree_summary_service is None:
        mt_cfg = (cfg or {}).get("memory_tree", {})
        _memory_tree_summary_service = MemoryTreeSummaryService(
            llm_enabled=mt_cfg.get("llm_summary_enabled", False),
            llm_cloud_allowed=mt_cfg.get("llm_summary_cloud_allowed", False),
        )
    return _memory_tree_summary_service
