"""OHA-008: MemoryTreeStoreService — CRUD layer for Memory Tree entities.

Provides content-addressed chunk storage, lifecycle management and
basic node (source/topic/global) tree operations. No LLM dependencies.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

import agent.database as _db_module
from agent.db_models import MemoryTreeChunkDB, MemoryTreeJobDB, MemoryTreeNodeDB

logger = logging.getLogger(__name__)

_LIFECYCLE_ORDER = [
    "pending_extraction",
    "admitted",
    "buffered",
    "sealed",
    "dropped",
]


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def chunk_id(source_id: str, label: str, content: str) -> str:
    """Deterministic, content-addressed chunk ID (SHA-256 hex, first 32 chars)."""
    raw = f"{source_id}\x00{label}\x00{content}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


def node_id(node_type: str, label: str) -> str:
    """Deterministic node ID."""
    raw = f"{node_type}\x00{label}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# MemoryTreeStoreService
# ---------------------------------------------------------------------------

class MemoryTreeStoreService:
    """Database operations for MemoryTree chunks, nodes and jobs."""

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------

    def ingest_chunk(
        self,
        *,
        source_id: str,
        source_type: str,
        label: str,
        content: str,
        scope: str = "source",
        kind: str = "leaf",
        sensitivity: str = "internal",
        provenance_ref: str | None = None,
        original_ref: str | None = None,
        created_by: str | None = None,
        metadata: dict | None = None,
    ) -> tuple[MemoryTreeChunkDB, bool]:
        """
        Upsert a chunk. Returns (chunk, created).
        created=False when an identical chunk already exists (idempotent).
        """
        cid = chunk_id(source_id, label, content)
        try:
            with Session(_db_module.engine) as session:
                existing = session.get(MemoryTreeChunkDB, cid)
                if existing is not None:
                    return existing, False
                chunk = MemoryTreeChunkDB(
                    id=cid,
                    source_id=source_id,
                    source_type=source_type,
                    scope=scope,
                    kind=kind,
                    sensitivity=sensitivity,
                    lifecycle="admitted",
                    label=label,
                    content=content,
                    provenance_ref=provenance_ref,
                    original_ref=original_ref,
                    created_by=created_by,
                    chunk_metadata=dict(metadata or {}),
                )
                session.add(chunk)
                session.commit()
                session.refresh(chunk)
                return chunk, True
        except OperationalError as exc:
            if "memory_tree_chunks" in str(exc).lower():
                logger.warning("memory_tree_chunks table not yet created — skipping ingest")
                return MemoryTreeChunkDB(
                    id=cid, source_id=source_id, source_type=source_type,
                    label=label, content=content,
                ), False
            raise

    def get_chunks_by_source(
        self,
        source_id: str,
        *,
        lifecycle: str | None = None,
        limit: int = 500,
    ) -> list[MemoryTreeChunkDB]:
        try:
            with Session(_db_module.engine) as session:
                stmt = select(MemoryTreeChunkDB).where(
                    MemoryTreeChunkDB.source_id == source_id
                )
                if lifecycle:
                    stmt = stmt.where(MemoryTreeChunkDB.lifecycle == lifecycle)
                stmt = stmt.limit(limit)
                return list(session.exec(stmt).all())
        except OperationalError:
            return []

    def update_lifecycle(self, chunk_id_val: str, lifecycle: str) -> bool:
        """Advance or drop chunk lifecycle. Returns True if updated."""
        if lifecycle not in _LIFECYCLE_ORDER:
            logger.warning("update_lifecycle: unknown lifecycle %r", lifecycle)
            return False
        try:
            with Session(_db_module.engine) as session:
                chunk = session.get(MemoryTreeChunkDB, chunk_id_val)
                if chunk is None:
                    return False
                chunk.lifecycle = lifecycle
                if lifecycle == "sealed":
                    chunk.sealed_at = time.time()
                elif lifecycle == "dropped":
                    chunk.dropped_at = time.time()
                session.add(chunk)
                session.commit()
                return True
        except OperationalError:
            return False

    def seal_source(self, source_id: str) -> int:
        """Seal all buffered chunks for a source. Returns count sealed."""
        chunks = self.get_chunks_by_source(source_id, lifecycle="buffered")
        count = 0
        for c in chunks:
            if self.update_lifecycle(c.id, "sealed"):
                count += 1
        return count

    def count_chunks(self, source_id: str, lifecycle: str | None = None) -> int:
        return len(self.get_chunks_by_source(source_id, lifecycle=lifecycle))

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def upsert_node(
        self,
        *,
        node_type: str,
        label: str,
        summary: str | None = None,
        provenance_refs: list[str] | None = None,
        child_chunk_ids: list[str] | None = None,
        metadata: dict | None = None,
    ) -> MemoryTreeNodeDB:
        nid = node_id(node_type, label)
        try:
            with Session(_db_module.engine) as session:
                node = session.get(MemoryTreeNodeDB, nid)
                if node is None:
                    node = MemoryTreeNodeDB(
                        id=nid,
                        node_type=node_type,
                        label=label,
                        summary=summary,
                        provenance_refs=list(provenance_refs or []),
                        child_chunk_ids=list(child_chunk_ids or []),
                        node_metadata=dict(metadata or {}),
                    )
                else:
                    if summary is not None:
                        node.summary = summary
                    if provenance_refs is not None:
                        existing_refs = list(node.provenance_refs or [])
                        for r in provenance_refs:
                            if r not in existing_refs:
                                existing_refs.append(r)
                        node.provenance_refs = existing_refs
                    if child_chunk_ids is not None:
                        existing_ids = list(node.child_chunk_ids or [])
                        for cid in child_chunk_ids:
                            if cid not in existing_ids:
                                existing_ids.append(cid)
                        node.child_chunk_ids = existing_ids
                session.add(node)
                session.commit()
                session.refresh(node)
                return node
        except OperationalError as exc:
            if "memory_tree_nodes" in str(exc).lower():
                logger.warning("memory_tree_nodes table not yet created — returning transient node")
                return MemoryTreeNodeDB(id=nid, node_type=node_type, label=label)
            raise

    def get_node(self, node_type: str, label: str) -> Optional[MemoryTreeNodeDB]:
        nid = node_id(node_type, label)
        try:
            with Session(_db_module.engine) as session:
                return session.get(MemoryTreeNodeDB, nid)
        except OperationalError:
            return None

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def enqueue_job(
        self,
        *,
        kind: str,
        payload: dict,
        dedupe_key: str | None = None,
    ) -> MemoryTreeJobDB:
        try:
            with Session(_db_module.engine) as session:
                if dedupe_key:
                    existing = session.exec(
                        select(MemoryTreeJobDB).where(
                            MemoryTreeJobDB.dedupe_key == dedupe_key,
                            MemoryTreeJobDB.status == "pending",
                        )
                    ).first()
                    if existing is not None:
                        return existing
                job = MemoryTreeJobDB(
                    kind=kind,
                    payload=payload,
                    dedupe_key=dedupe_key,
                )
                session.add(job)
                session.commit()
                session.refresh(job)
                return job
        except OperationalError as exc:
            if "memory_tree_jobs" in str(exc).lower():
                logger.warning("memory_tree_jobs table not yet created — returning transient job")
                return MemoryTreeJobDB(kind=kind, payload=payload)
            raise

    def complete_job(self, job_id: str, *, status: str = "done") -> bool:
        try:
            with Session(_db_module.engine) as session:
                job = session.get(MemoryTreeJobDB, job_id)
                if job is None:
                    return False
                job.status = status
                job.completed_at = time.time()
                session.add(job)
                session.commit()
                return True
        except OperationalError:
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_memory_tree_store_service: MemoryTreeStoreService | None = None


def get_memory_tree_store_service() -> MemoryTreeStoreService:
    global _memory_tree_store_service
    if _memory_tree_store_service is None:
        _memory_tree_store_service = MemoryTreeStoreService()
    return _memory_tree_store_service
