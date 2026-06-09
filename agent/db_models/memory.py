from __future__ import annotations

import time
import uuid
from typing import List, Optional

from sqlmodel import JSON, Column, Field, SQLModel


class MemoryEntryDB(SQLModel, table=True):
    __tablename__ = "memory_entries"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    task_id: Optional[str] = Field(default=None, index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)
    worker_job_id: Optional[str] = Field(default=None, index=True)
    entry_type: str = "worker_result"
    title: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    artifact_refs: List[dict] = Field(default=[], sa_column=Column(JSON))
    retrieval_tags: List[str] = Field(default=[], sa_column=Column(JSON))
    memory_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class MemoryTreeChunkDB(SQLModel, table=True):
    """Individual content unit in the Memory Tree (a leaf or summary chunk)."""
    __tablename__ = "memory_tree_chunks"
    id: str = Field(primary_key=True)
    source_id: str = Field(index=True)
    source_type: str = "knowledge_index"
    scope: str = "source"
    kind: str = "leaf"
    sensitivity: str = "internal"
    lifecycle: str = "admitted"
    label: str = ""
    content: str = ""
    provenance_ref: Optional[str] = None
    original_ref: Optional[str] = None
    created_by: Optional[str] = None
    chunk_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
    sealed_at: Optional[float] = None
    dropped_at: Optional[float] = None


class MemoryTreeNodeDB(SQLModel, table=True):
    """Summary node in the source/topic/global tree hierarchy."""
    __tablename__ = "memory_tree_nodes"
    id: str = Field(primary_key=True)
    node_type: str = "source"
    label: str = ""
    summary: Optional[str] = None
    provenance_refs: List[str] = Field(default=[], sa_column=Column(JSON))
    child_chunk_ids: List[str] = Field(default=[], sa_column=Column(JSON))
    sealed: bool = False
    node_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    sealed_at: Optional[float] = None


class MemoryTreeJobDB(SQLModel, table=True):
    """Durable ingest/seal/digest job queue entry."""
    __tablename__ = "memory_tree_jobs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    kind: str = "ingest_source"
    status: str = "pending"
    payload: dict = Field(default={}, sa_column=Column(JSON))
    dedupe_key: Optional[str] = Field(default=None, index=True)
    retry_count: int = 0
    lease_until: Optional[float] = None
    created_at: float = Field(default_factory=time.time, index=True)
    completed_at: Optional[float] = None
