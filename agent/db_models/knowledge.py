from __future__ import annotations

import time
import uuid
from typing import Optional

from sqlmodel import JSON, Column, Field, SQLModel


class KnowledgeCollectionDB(SQLModel, table=True):
    __tablename__ = "knowledge_collections"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    created_by: Optional[str] = None
    collection_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class KnowledgeLinkDB(SQLModel, table=True):
    __tablename__ = "knowledge_links"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    collection_id: str = Field(index=True)
    artifact_id: str = Field(index=True)
    extracted_document_id: Optional[str] = Field(default=None, index=True)
    link_type: str = "artifact"
    link_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class KnowledgeIndexDB(SQLModel, table=True):
    __tablename__ = "knowledge_indices"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    artifact_id: Optional[str] = Field(default=None, index=True)
    collection_id: Optional[str] = Field(default=None, index=True)
    latest_run_id: Optional[str] = Field(default=None, index=True)
    source_scope: str = "artifact"
    profile_name: str = "default"
    status: str = "pending"
    output_dir: Optional[str] = None
    manifest_path: Optional[str] = None
    index_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_by: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class KnowledgeIndexRunDB(SQLModel, table=True):
    __tablename__ = "knowledge_index_runs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    knowledge_index_id: str = Field(index=True)
    artifact_id: Optional[str] = Field(default=None, index=True)
    collection_id: Optional[str] = Field(default=None, index=True)
    profile_name: str = "default"
    status: str = "pending"
    source_path: Optional[str] = None
    output_dir: Optional[str] = None
    manifest_path: Optional[str] = None
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    run_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
