from __future__ import annotations

import time
import uuid

from sqlalchemy import UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel


class KnowledgeClaimDB(SQLModel, table=True):
    __tablename__ = "knowledge_hygiene_claims"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key"),)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    claim_id: str = Field(index=True)
    project_id: str = Field(index=True)
    revision: int = Field(default=1, index=True)
    idempotency_key: str = Field(index=True)
    source_id: str = Field(index=True)
    source_revision: str = Field(index=True)
    source_locator: str
    record_digest: str
    payload: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)


class KnowledgeConflictDB(SQLModel, table=True):
    __tablename__ = "knowledge_hygiene_conflicts"
    id: str = Field(primary_key=True)
    project_id: str = Field(index=True)
    pair_key: str = Field(index=True, unique=True)
    state: str = Field(default="open", index=True)
    severity: str = Field(default="unknown", index=True)
    version: int = Field(default=1)
    basis_digest: str
    payload: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time, index=True)


class KnowledgeConflictDecisionDB(SQLModel, table=True):
    __tablename__ = "knowledge_hygiene_decisions"
    id: str = Field(primary_key=True)
    decision_id: str = Field(index=True)
    project_id: str = Field(index=True)
    conflict_id: str = Field(index=True)
    actor_id: str = Field(index=True)
    basis_digest: str
    payload: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)


class CuratedWikiPageDB(SQLModel, table=True):
    __tablename__ = "knowledge_hygiene_wiki_pages"
    __table_args__ = (UniqueConstraint("project_id", "slug", "revision"),)
    id: str = Field(primary_key=True)
    page_id: str = Field(index=True)
    project_id: str = Field(index=True)
    slug: str = Field(index=True)
    revision: int = Field(index=True)
    content_hash: str = Field(index=True)
    coverage: str = Field(index=True)
    payload: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)


class KnowledgeHygieneRunDB(SQLModel, table=True):
    __tablename__ = "knowledge_hygiene_runs"
    id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    project_id: str = Field(index=True)
    state: str = Field(index=True)
    assignment_digest: str = Field(index=True)
    result_digest: str | None = Field(default=None, index=True)
    checkpoint: int = 0
    payload: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time, index=True)


class KnowledgeHealthSnapshotDB(SQLModel, table=True):
    __tablename__ = "knowledge_hygiene_health_snapshots"
    id: str = Field(primary_key=True)
    project_id: str = Field(index=True)
    scope_version: str = Field(index=True)
    coverage: str = Field(index=True)
    basis_digest: str = Field(index=True)
    payload: dict = Field(default={}, sa_column=Column(JSON))
    as_of: float = Field(index=True)


class KnowledgeCorrectionDB(SQLModel, table=True):
    __tablename__ = "knowledge_hygiene_corrections"
    id: str = Field(primary_key=True)
    correction_id: str = Field(index=True)
    project_id: str = Field(index=True)
    conflict_id: str = Field(index=True)
    source_id: str = Field(index=True)
    proposal_digest: str = Field(index=True)
    state: str = Field(default="proposed", index=True)
    payload: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time, index=True)


class KnowledgeHygieneAuditEventDB(SQLModel, table=True):
    __tablename__ = "knowledge_hygiene_audit_events"
    id: str = Field(primary_key=True)
    project_id: str = Field(index=True)
    aggregate_type: str = Field(index=True)
    aggregate_id: str = Field(index=True)
    event_type: str = Field(index=True)
    actor_id: str = Field(index=True)
    payload: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
