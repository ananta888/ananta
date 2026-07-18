"""Persistent Hub control-plane state for Visual Process assistance."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel


class VisualProcessAssistantContextDB(SQLModel, table=True):
    __tablename__ = "visual_process_assistant_contexts"

    context_id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    graph_id: str = Field(index=True)
    definition_revision: int = Field(index=True)
    definition_hash: str = Field(index=True)
    editor_mode: str
    locale: str
    context_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: float = Field(default_factory=time.time)


class VisualProcessAssistantConversationDB(SQLModel, table=True):
    __tablename__ = "visual_process_assistant_conversations"

    id: str = Field(
        default_factory=lambda: f"vpa-conv-{uuid.uuid4().hex}",
        primary_key=True,
    )
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    graph_id: str = Field(index=True)
    status: str = Field(default="active", index=True)
    active_context_id: str | None = Field(
        default=None, foreign_key="visual_process_assistant_contexts.context_id", index=True
    )
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class VisualProcessAssistantRequestDB(SQLModel, table=True):
    __tablename__ = "visual_process_assistant_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "conversation_id",
            "client_request_id",
            name="uq_vpa_request_client_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "idempotency_key_hash",
            name="uq_vpa_request_idempotency_scope",
        ),
    )

    id: str = Field(
        default_factory=lambda: f"vpa-req-{uuid.uuid4().hex}",
        primary_key=True,
    )
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    conversation_id: str = Field(foreign_key="visual_process_assistant_conversations.id", index=True)
    context_id: str = Field(foreign_key="visual_process_assistant_contexts.context_id", index=True)
    prompt_context_id: str | None = Field(
        default=None,
        foreign_key="visual_process_assistant_contexts.context_id",
        index=True,
    )
    prompt_version: str
    client_request_id: str = Field(index=True)
    idempotency_key_hash: str = Field(index=True)
    request_fingerprint: str = Field(index=True)
    question_text: str
    question_hash: str
    status: str = Field(default="queued_retrieval", index=True)
    retrieval_task_id: str | None = Field(default=None, index=True)
    inference_task_id: str | None = Field(default=None, index=True)
    accepted_evidence_json: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    prompt_snapshot_json: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    response_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    error_code: str | None = None
    retry_count: int = Field(default=0)
    retrieval_deadline_at: float | None = None
    inference_deadline_at: float | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    cancelled_at: float | None = None


class VisualProcessAssistantRateLimitDB(SQLModel, table=True):
    """Persistent fixed-window limiter; one row is locked per principal/minute."""

    __tablename__ = "visual_process_assistant_rate_limits"

    bucket_key: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    window_started_at: float = Field(index=True)
    request_count: int = Field(default=0)
    updated_at: float = Field(default_factory=time.time)


class VisualProcessPatchAuditDB(SQLModel, table=True):
    __tablename__ = "visual_process_patch_audits"
    __table_args__ = (UniqueConstraint("request_id", "patch_hash", name="uq_vpa_patch_request_hash"),)

    id: str = Field(
        default_factory=lambda: f"vpa-patch-audit-{uuid.uuid4().hex}",
        primary_key=True,
    )
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    request_id: str = Field(foreign_key="visual_process_assistant_requests.id", index=True)
    graph_id: str = Field(index=True)
    context_id: str = Field(index=True)
    prompt_version: str
    patch_hash: str = Field(index=True)
    decision: str = Field(default="previewed", index=True)
    reason_codes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: float = Field(default_factory=time.time)
    decided_at: float | None = None


__all__ = [
    "VisualProcessAssistantContextDB",
    "VisualProcessAssistantConversationDB",
    "VisualProcessAssistantRequestDB",
    "VisualProcessAssistantRateLimitDB",
    "VisualProcessPatchAuditDB",
]
