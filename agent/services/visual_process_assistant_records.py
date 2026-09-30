"""Tenant-scoped persistence helpers for the Visual Process Assistant.

Ownership lookups, immutable context storage, the per-principal rate limit
and the public (API) projections of assistant rows.  Every function takes an
explicit SQL session and principal; none of them commits, so transaction
boundaries stay with the orchestrating caller.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from sqlmodel import Session, select

from agent.db_models.visual_process import VisualProcessGraphDB
from agent.db_models.visual_process_assistant import (
    VisualProcessAssistantContextDB,
    VisualProcessAssistantConversationDB,
    VisualProcessAssistantRateLimitDB,
    VisualProcessAssistantRequestDB,
)
from agent.services.chat_process_binding import authorize_graph
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.visual_process_assistant_errors import VisualProcessAssistantError
from agent.services.visual_process_definition_service import VisualProcessDefinitionService
from agent.visual_process.models import VisualProcessGraph
from ananta_contracts.visual_process_assistant import EditorContextEnvelope

MAX_REQUESTS_PER_MINUTE = 20


def store_context(
    db: Session,
    principal: ChatSessionPrincipal,
    envelope: EditorContextEnvelope,
) -> VisualProcessAssistantContextDB:
    context_id = envelope.context_id()
    existing = db.get(VisualProcessAssistantContextDB, context_id)
    if existing is not None:
        if existing.tenant_id != principal.tenant_id or existing.owner_subject != principal.subject_id:
            raise VisualProcessAssistantError("assistant_context_id_unavailable", status_code=409)
        return existing
    row = VisualProcessAssistantContextDB(
        context_id=context_id,
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject_id,
        graph_id=envelope.graph_id,
        definition_revision=envelope.definition_revision,
        definition_hash=envelope.definition_hash,
        editor_mode=envelope.editor_mode,
        locale=envelope.locale,
        context_json=envelope.model_dump(mode="json"),
    )
    db.add(row)
    db.flush()
    return row


def owned_graph(
    db: Session,
    graph_id: str,
    principal: ChatSessionPrincipal,
) -> VisualProcessGraph:
    row = db.get(VisualProcessGraphDB, str(graph_id))
    if row is None:
        raise VisualProcessAssistantError("assistant_graph_not_found", status_code=404)
    try:
        raw = json.loads(row.graph_json)
    except (TypeError, ValueError) as exc:
        raise VisualProcessAssistantError("assistant_graph_corrupt", status_code=500) from exc
    authorized, migrated = authorize_graph(raw, principal)
    if not authorized:
        raise VisualProcessAssistantError("assistant_graph_not_found", status_code=404)
    if migrated:
        row.graph_json = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        db.add(row)
        db.flush()
    graph = VisualProcessGraph.model_validate(raw).model_copy(
        update={
            "definition_revision": int(row.definition_revision or 1),
            "base_graph_hash": str(row.base_graph_hash or ""),
            "graph_schema_version": str(row.graph_schema_version or "1"),
            "node_registry_version": str(row.node_registry_version or "1"),
        }
    )
    if not graph.base_graph_hash:
        graph = graph.model_copy(update={"base_graph_hash": graph.definition_hash()})
    return graph


def validated_patch_draft(
    *,
    definition: VisualProcessGraph,
    payload: Any,
    required: bool = False,
) -> VisualProcessGraph:
    if payload is None and not required:
        return definition
    if not isinstance(payload, Mapping):
        raise VisualProcessAssistantError("assistant_patch_draft_required", status_code=422)
    draft = VisualProcessGraph.model_validate(dict(payload))
    if draft.id != definition.id:
        raise VisualProcessAssistantError("assistant_patch_draft_graph_mismatch", status_code=409)
    if draft.definition_revision != definition.definition_revision:
        raise VisualProcessAssistantError(
            "assistant_patch_draft_revision_conflict",
            status_code=409,
            details={
                "expected_revision": definition.definition_revision,
                "actual_revision": draft.definition_revision,
            },
        )
    draft_base = str(draft.base_graph_hash or "").removeprefix("sha256:")
    definition_base = str(definition.base_graph_hash or definition.definition_hash()).removeprefix("sha256:")
    if draft_base != definition_base:
        raise VisualProcessAssistantError("assistant_patch_draft_base_conflict", status_code=409)
    VisualProcessDefinitionService.validate_writable_definition(draft)
    return draft


def owned_context(
    db: Session,
    context_id: str,
    principal: ChatSessionPrincipal,
) -> VisualProcessAssistantContextDB:
    row = db.get(VisualProcessAssistantContextDB, str(context_id))
    if row is None or row.tenant_id != principal.tenant_id or row.owner_subject != principal.subject_id:
        raise VisualProcessAssistantError("assistant_context_not_found", status_code=404)
    return row


def owned_conversation(
    db: Session,
    conversation_id: str,
    principal: ChatSessionPrincipal,
    *,
    for_update: bool = False,
) -> VisualProcessAssistantConversationDB:
    statement = select(VisualProcessAssistantConversationDB).where(
        VisualProcessAssistantConversationDB.id == str(conversation_id),
        VisualProcessAssistantConversationDB.tenant_id == principal.tenant_id,
        VisualProcessAssistantConversationDB.owner_subject == principal.subject_id,
    )
    if for_update:
        statement = statement.with_for_update()
    row = db.exec(statement).first()
    if row is None:
        raise VisualProcessAssistantError("assistant_conversation_not_found", status_code=404)
    return row


def owned_request(
    db: Session,
    request_id: str,
    principal: ChatSessionPrincipal,
    *,
    for_update: bool = False,
) -> VisualProcessAssistantRequestDB:
    statement = select(VisualProcessAssistantRequestDB).where(
        VisualProcessAssistantRequestDB.id == str(request_id),
        VisualProcessAssistantRequestDB.tenant_id == principal.tenant_id,
        VisualProcessAssistantRequestDB.owner_subject == principal.subject_id,
    )
    if for_update:
        statement = statement.with_for_update()
    row = db.exec(statement).first()
    if row is None:
        raise VisualProcessAssistantError("assistant_request_not_found", status_code=404)
    return row


def consume_rate_limit(
    db: Session,
    principal: ChatSessionPrincipal,
    now: float,
) -> None:
    window = math.floor(now / 60.0) * 60.0
    bucket_key = hashlib.sha256(
        f"{principal.tenant_id}\0{principal.subject_id}\0{int(window)}".encode("utf-8")
    ).hexdigest()
    row = db.exec(
        select(VisualProcessAssistantRateLimitDB)
        .where(VisualProcessAssistantRateLimitDB.bucket_key == bucket_key)
        .with_for_update()
    ).first()
    if row is None:
        row = VisualProcessAssistantRateLimitDB(
            bucket_key=bucket_key,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject_id,
            window_started_at=window,
            request_count=0,
            updated_at=now,
        )
    if row.request_count >= MAX_REQUESTS_PER_MINUTE:
        raise VisualProcessAssistantError(
            "assistant_principal_rate_limit",
            status_code=429,
            retry_after=max(1, int(window + 60.0 - now)),
        )
    row.request_count += 1
    row.updated_at = now
    db.add(row)
    db.flush()


def public_context(row: VisualProcessAssistantContextDB) -> dict[str, Any]:
    return {
        "context_id": row.context_id,
        "graph_id": row.graph_id,
        "definition_revision": row.definition_revision,
        "definition_hash": row.definition_hash,
        "editor_mode": row.editor_mode,
        "locale": row.locale,
        "context": copy.deepcopy(row.context_json),
        "created_at": row.created_at,
    }


def public_conversation(row: VisualProcessAssistantConversationDB) -> dict[str, Any]:
    return {
        "conversation_id": row.id,
        "graph_id": row.graph_id,
        "status": row.status,
        "active_context_id": row.active_context_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def public_request(row: VisualProcessAssistantRequestDB) -> dict[str, Any]:
    return {
        "request_id": row.id,
        "conversation_id": row.conversation_id,
        "context_id": row.context_id,
        "prompt_context_id": row.prompt_context_id,
        "prompt_version": row.prompt_version,
        "client_request_id": row.client_request_id,
        "status": row.status,
        "retrieval_task_id": row.retrieval_task_id,
        "inference_task_id": row.inference_task_id,
        "response": copy.deepcopy(row.response_json),
        "error_code": row.error_code,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "cancelled_at": row.cancelled_at,
    }


__all__ = [
    "MAX_REQUESTS_PER_MINUTE",
    "consume_rate_limit",
    "owned_context",
    "owned_conversation",
    "owned_graph",
    "owned_request",
    "public_context",
    "public_conversation",
    "public_request",
    "store_context",
    "validated_patch_draft",
]
