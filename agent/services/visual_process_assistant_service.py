"""Persistent Hub orchestration for contextual Visual Process assistance.

This service is the control plane.  It owns conversations, immutable context
snapshots, task creation, evidence acceptance, cancellation and patch audit.
Delegable retrieval and model work is represented exclusively as central Hub
tasks and is never executed here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from collections.abc import Mapping
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.config import settings
from agent.database import engine
from agent.db_models.visual_process import VisualProcessGraphDB
from agent.db_models.visual_process_assistant import (
    VisualProcessAssistantContextDB,
    VisualProcessAssistantConversationDB,
    VisualProcessAssistantRateLimitDB,
    VisualProcessAssistantRequestDB,
    VisualProcessPatchAuditDB,
)
from agent.metrics import (
    VISUAL_PROCESS_ASSISTANT_ACTIVE,
    VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL,
)
from agent.services.chat_process_binding import authorize_graph
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.codecompass_editor_context_contract import (
    CodeCompassEditorQueryInput,
)
from agent.services.source_catalog_authority_service import (
    SourceCatalogAuthorityError,
    SourceCatalogAuthorityService,
    get_source_catalog_authority_service,
)
from agent.services.visual_process_assistant_errors import (
    VisualProcessAssistantError as VisualProcessAssistantError,
)
from agent.services.visual_process_assistant_validation import (
    bounded_identifier as _bounded_identifier,
)
from agent.services.visual_process_assistant_validation import (
    envelope_hash as _envelope_hash,
)
from agent.services.visual_process_assistant_validation import (
    required_text as _required_text,
)
from agent.services.visual_process_assistant_validation import (
    stable_hash as _stable_hash,
)
from agent.services.visual_process_context_service import (
    PROMPT_VERSION,
    VisualProcessContextService,
    VisualProcessPromptAssembly,
)
from agent.services.visual_process_definition_service import VisualProcessDefinitionService
from agent.services.visual_process_patch_approval_policy import (
    VisualProcessPatchApprovalError,
    VisualProcessPatchApprovalPolicy,
)
from agent.services.visual_process_patch_service import (
    VisualProcessPatchService,
)
from agent.visual_process.models import VisualProcessGraph
from ananta_contracts.visual_process_assistant import (
    ASSISTANT_CONTEXT_POLICY_VERSION,
    ASSISTANT_INFERENCE_JOB_VERSION,
    ASSISTANT_INFERENCE_RESULT_VERSION,
    ASSISTANT_RETRIEVAL_JOB_VERSION,
    ASSISTANT_RETRIEVAL_RESULT_VERSION,
    EditorContextEnvelope,
    EvidenceRef,
    HelpResponse,
    TrustLevel,
    VerificationStatus,
    WorkflowPatch,
)

INFERENCE_JOB_SCHEMA = ASSISTANT_INFERENCE_JOB_VERSION
INFERENCE_RESULT_SCHEMA = ASSISTANT_INFERENCE_RESULT_VERSION
RETRIEVAL_JOB_SCHEMA = ASSISTANT_RETRIEVAL_JOB_VERSION
RETRIEVAL_RESULT_SCHEMA = ASSISTANT_RETRIEVAL_RESULT_VERSION

ACTIVE_REQUEST_STATUSES = frozenset({"queued_retrieval", "retrieving", "queued_inference", "inferencing"})
TERMINAL_REQUEST_STATUSES = frozenset({"completed", "failed", "cancelled", "timeout", "rejected"})
MAX_QUESTION_CHARS = 8_000
MAX_ACTIVE_PER_CONVERSATION = 2
MAX_REQUESTS_PER_MINUTE = 20
ASSISTANT_CATALOG_TASK_SOURCES = frozenset({"api", "visual_process", "visual_process_assistant"})
ASSISTANT_CATALOG_TASK_KINDS = frozenset(
    {
        "codecompass_fts_search",
        "codecompass_graph_expand",
        "codecompass_vector_search",
        "rag_retrieve",
        "research_limited",
        "review",
        "summarize",
    }
)


class VisualProcessAssistantService:
    """Coordinate the two-phase retrieval/inference lifecycle through Hub tasks."""

    def __init__(
        self,
        *,
        context_service: VisualProcessContextService | None = None,
        patch_service: VisualProcessPatchService | None = None,
        patch_approval_policy: VisualProcessPatchApprovalPolicy | None = None,
        source_authority: SourceCatalogAuthorityService | None = None,
        clock=time.time,
        retrieval_timeout_ms: int | None = None,
        model_timeout_ms: int | None = None,
    ) -> None:
        self._contexts = context_service or VisualProcessContextService()
        self._patches = patch_service or VisualProcessPatchService()
        self._patch_approval = patch_approval_policy or VisualProcessPatchApprovalPolicy()
        self._source_authority = source_authority or get_source_catalog_authority_service()
        self._clock = clock
        self._retrieval_timeout_ms = int(
            retrieval_timeout_ms
            if retrieval_timeout_ms is not None
            else settings.visual_process_assistant_retrieval_timeout_ms
        )
        self._model_timeout_ms = int(
            model_timeout_ms if model_timeout_ms is not None else settings.visual_process_assistant_model_timeout_ms
        )

    # ── immutable context and conversation lifecycle ──────────────────

    def create_context(
        self,
        *,
        principal: ChatSessionPrincipal,
        graph_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = dict(payload or {})
        with Session(engine) as db:
            graph = self._owned_graph(db, graph_id, principal)
            draft = body.get("draft_graph")
            draft_graph = VisualProcessGraph.model_validate(draft) if isinstance(draft, Mapping) else graph
            if draft_graph.id != graph.id:
                raise VisualProcessAssistantError("assistant_draft_graph_mismatch", status_code=409)
            if draft_graph.definition_revision != graph.definition_revision:
                raise VisualProcessAssistantError(
                    "assistant_context_definition_stale",
                    status_code=409,
                    details={
                        "expected_revision": graph.definition_revision,
                        "actual_revision": draft_graph.definition_revision,
                    },
                )
            supplied_base = str(draft_graph.base_graph_hash or body.get("base_graph_hash") or "")
            if supplied_base and supplied_base.removeprefix("sha256:") != graph.base_graph_hash.removeprefix("sha256:"):
                raise VisualProcessAssistantError("assistant_context_definition_stale", status_code=409)
            VisualProcessDefinitionService.validate_writable_definition(draft_graph)

            repository_revision = _required_text(body, "repository_revision", max_length=256)
            manifest_hash = _required_text(body, "codecompass_manifest_hash", max_length=256)
            allowlist_version = _required_text(body, "source_allowlist_version", max_length=256)
            source_scope = _required_text(body, "source_scope", max_length=256)
            if "source_refs" in body:
                raise VisualProcessAssistantError(
                    "assistant_client_source_refs_forbidden",
                    status_code=403,
                )
            catalog_fields = {
                "catalog_task_id": str(body.get("catalog_task_id") or "").strip(),
                "catalog_id": str(body.get("catalog_id") or "").strip(),
                "catalog_hash": str(body.get("catalog_hash") or "").strip(),
            }
            if any(catalog_fields.values()) and not all(catalog_fields.values()):
                raise VisualProcessAssistantError(
                    "assistant_source_catalog_reference_incomplete",
                    status_code=422,
                )
            source_refs: list[EvidenceRef] = []
            context_extensions: dict[str, Any] = {"ananta.source_scope": source_scope}
            if all(catalog_fields.values()):
                try:
                    resolved_catalog = self._source_authority.resolve(
                        principal=principal,
                        repository_revision=repository_revision,
                        manifest_hash=manifest_hash,
                        source_allowlist_version=allowlist_version,
                        source_scope=source_scope,
                        allowed_task_sources=ASSISTANT_CATALOG_TASK_SOURCES,
                        allowed_task_kinds=ASSISTANT_CATALOG_TASK_KINDS,
                        expected_task_tenant_id=principal.tenant_id,
                        **catalog_fields,
                    )
                except SourceCatalogAuthorityError as exc:
                    status_code = 404 if exc.reason_code.endswith("not_found") else 403
                    raise VisualProcessAssistantError(
                        f"assistant_{exc.reason_code}",
                        status_code=status_code,
                    ) from exc
                source_refs = [
                    EvidenceRef(
                        # Claims cite the exact Hub-authorized identity.  The
                        # assistant must never mint a parallel evidence id.
                        evidence_id=reference.source_id,
                        source_id=reference.source_id,
                        source_version=reference.source_version,
                        tenant_id=reference.tenant_id,
                        scope=reference.scope,
                        provenance_digest=reference.provenance_digest,
                        trust_level=TrustLevel.declared,
                        verification_status=VerificationStatus.verified,
                    )
                    for reference in resolved_catalog.source_refs
                ]
                context_extensions["ananta.source_catalog"] = {
                    "catalog_task_id": resolved_catalog.catalog_task_id,
                    "catalog_id": resolved_catalog.catalog_id,
                    "catalog_hash": resolved_catalog.catalog_hash,
                }
            editor_mode = str(body.get("editor_mode") or "editor").strip().lower()
            allowed_mutations = (
                []
                if editor_mode == "read_only"
                else [
                    "add_step",
                    "remove_step",
                    "update_step_field",
                    "add_edge",
                    "remove_edge",
                    "update_edge_condition",
                ]
            )
            envelope = self._contexts.build_context(
                graph=graph,
                draft_graph=draft_graph,
                location=dict(body.get("location") or {}),
                editor_mode=editor_mode,
                locale=str(body.get("locale") or "de"),
                repository_revision=repository_revision,
                codecompass_manifest_hash=manifest_hash,
                source_allowlist_version=allowlist_version,
                prompt_version=PROMPT_VERSION,
                runtime_overlay=(
                    dict(body.get("runtime_overlay") or {})
                    if isinstance(body.get("runtime_overlay"), Mapping)
                    else None
                ),
                validation_issues=[
                    dict(item) for item in list(body.get("validation_issues") or []) if isinstance(item, Mapping)
                ],
                evidence_refs=source_refs,
                allowed_mutations=allowed_mutations,
                extensions=context_extensions,
            )
            context_cache_status = (
                "cache_hit"
                if db.get(VisualProcessAssistantContextDB, envelope.context_id()) is not None
                else "cache_miss"
            )
            row = self._store_context(db, principal, envelope)
            db.commit()
            db.refresh(row)
            result = self._public_context(row)
        VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status=context_cache_status).inc()
        return result

    def get_context(
        self,
        *,
        principal: ChatSessionPrincipal,
        context_id: str,
    ) -> dict[str, Any]:
        with Session(engine) as db:
            row = self._owned_context(db, context_id, principal)
            return self._public_context(row)

    def create_conversation(
        self,
        *,
        principal: ChatSessionPrincipal,
        context_id: str,
    ) -> dict[str, Any]:
        now = float(self._clock())
        with Session(engine) as db:
            context = self._owned_context(db, context_id, principal)
            self._owned_graph(db, context.graph_id, principal)
            row = VisualProcessAssistantConversationDB(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject_id,
                graph_id=context.graph_id,
                active_context_id=context.context_id,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._public_conversation(row)

    def get_conversation(
        self,
        *,
        principal: ChatSessionPrincipal,
        conversation_id: str,
    ) -> dict[str, Any]:
        with Session(engine) as db:
            row = self._owned_conversation(db, conversation_id, principal)
            requests = db.exec(
                select(VisualProcessAssistantRequestDB)
                .where(VisualProcessAssistantRequestDB.conversation_id == row.id)
                .order_by(VisualProcessAssistantRequestDB.created_at)
            ).all()
            return {
                **self._public_conversation(row),
                "requests": [self._public_request(item) for item in requests],
            }

    def switch_context(
        self,
        *,
        principal: ChatSessionPrincipal,
        conversation_id: str,
        context_id: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise VisualProcessAssistantError("assistant_context_switch_confirmation_required", status_code=428)
        with Session(engine) as db:
            conversation = self._owned_conversation(db, conversation_id, principal, for_update=True)
            context = self._owned_context(db, context_id, principal)
            if context.graph_id != conversation.graph_id:
                raise VisualProcessAssistantError("assistant_context_graph_mismatch", status_code=409)
            conversation.active_context_id = context.context_id
            conversation.updated_at = float(self._clock())
            db.add(conversation)
            db.commit()
            return self._public_conversation(conversation)

    # ── Hub-owned request/task orchestration ──────────────────────────

    def submit_question(
        self,
        *,
        principal: ChatSessionPrincipal,
        conversation_id: str,
        question: str,
        client_request_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_question = str(question or "").strip()
        if not normalized_question or len(normalized_question) > MAX_QUESTION_CHARS:
            raise VisualProcessAssistantError("assistant_question_invalid")
        client_id = _bounded_identifier(client_request_id, "assistant_client_request_id")
        idem = _bounded_identifier(idempotency_key, "assistant_idempotency_key")
        now = float(self._clock())
        key_hash = hashlib.sha256(idem.encode("utf-8")).hexdigest()
        question_hash = hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()

        with Session(engine) as db:
            conversation = self._owned_conversation(db, conversation_id, principal, for_update=True)
            if conversation.status != "active" or not conversation.active_context_id:
                raise VisualProcessAssistantError("assistant_conversation_not_active", status_code=409)
            context = self._owned_context(db, conversation.active_context_id, principal)
            fingerprint = _stable_hash(
                {
                    "conversation_id": conversation.id,
                    "context_id": context.context_id,
                    "question_hash": question_hash,
                    "prompt_version": PROMPT_VERSION,
                }
            )
            existing = db.exec(
                select(VisualProcessAssistantRequestDB).where(
                    VisualProcessAssistantRequestDB.tenant_id == principal.tenant_id,
                    VisualProcessAssistantRequestDB.owner_subject == principal.subject_id,
                    VisualProcessAssistantRequestDB.idempotency_key_hash == key_hash,
                )
            ).first()
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise VisualProcessAssistantError("assistant_idempotency_conflict", status_code=409)
                return self._public_request(existing)
            duplicate_client = db.exec(
                select(VisualProcessAssistantRequestDB).where(
                    VisualProcessAssistantRequestDB.conversation_id == conversation.id,
                    VisualProcessAssistantRequestDB.client_request_id == client_id,
                )
            ).first()
            if duplicate_client is not None:
                if duplicate_client.request_fingerprint != fingerprint:
                    raise VisualProcessAssistantError("assistant_client_request_conflict", status_code=409)
                return self._public_request(duplicate_client)

            self._consume_rate_limit(db, principal, now)
            active_count = len(
                db.exec(
                    select(VisualProcessAssistantRequestDB.id).where(
                        VisualProcessAssistantRequestDB.conversation_id == conversation.id,
                        VisualProcessAssistantRequestDB.status.in_(ACTIVE_REQUEST_STATUSES),
                    )
                ).all()
            )
            if active_count >= MAX_ACTIVE_PER_CONVERSATION:
                raise VisualProcessAssistantError(
                    "assistant_conversation_in_flight_limit",
                    status_code=429,
                    retry_after=1,
                )
            request_row = VisualProcessAssistantRequestDB(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject_id,
                conversation_id=conversation.id,
                context_id=context.context_id,
                prompt_version=PROMPT_VERSION,
                client_request_id=client_id,
                idempotency_key_hash=key_hash,
                request_fingerprint=fingerprint,
                question_text=normalized_question,
                question_hash=question_hash,
                status="queued_retrieval",
                retrieval_deadline_at=now + self._retrieval_timeout_ms / 1000.0,
                created_at=now,
                updated_at=now,
            )
            db.add(request_row)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise VisualProcessAssistantError("assistant_request_conflict", status_code=409) from exc
            db.refresh(request_row)
            queued_request_id = request_row.id
            queued_context_id = context.context_id

        try:
            with Session(engine) as queue_db:
                queue_request = queue_db.get(VisualProcessAssistantRequestDB, queued_request_id)
                queue_context = queue_db.get(VisualProcessAssistantContextDB, queued_context_id)
                if queue_request is None or queue_context is None:
                    raise RuntimeError("assistant_queue_state_missing")
                self._queue_retrieval(queue_request, queue_context)
        except Exception as exc:
            self._fail_request(queued_request_id, f"retrieval_queue_failed:{type(exc).__name__}")
            raise VisualProcessAssistantError("assistant_task_queue_unavailable", status_code=503) from exc
        VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status="queued").inc()
        self._refresh_active_metric()
        return self.get_request(principal=principal, request_id=queued_request_id, reconcile=False)

    def get_request(
        self,
        *,
        principal: ChatSessionPrincipal,
        request_id: str,
        reconcile: bool = True,
    ) -> dict[str, Any]:
        if reconcile:
            self.reconcile_request(request_id=request_id, principal=principal)
        with Session(engine) as db:
            row = self._owned_request(db, request_id, principal)
            return self._public_request(row)

    def cancel_request(
        self,
        *,
        principal: ChatSessionPrincipal,
        request_id: str,
    ) -> dict[str, Any]:
        now = float(self._clock())
        task_ids: list[str] = []
        with Session(engine) as db:
            row = self._owned_request(db, request_id, principal, for_update=True)
            if row.status in TERMINAL_REQUEST_STATUSES:
                return self._public_request(row)
            row.status = "cancelled"
            row.error_code = "assistant_cancelled_by_user"
            row.cancelled_at = now
            row.updated_at = now
            task_ids = [item for item in (row.retrieval_task_id, row.inference_task_id) if item]
            db.add(row)
            db.commit()
        for task_id in task_ids:
            self._cancel_task(task_id)
        VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status="cancelled").inc()
        self._refresh_active_metric()
        return self.get_request(principal=principal, request_id=request_id, reconcile=False)

    def retry_request(
        self,
        *,
        principal: ChatSessionPrincipal,
        request_id: str,
        client_request_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        with Session(engine) as db:
            previous = self._owned_request(db, request_id, principal)
            if previous.status not in TERMINAL_REQUEST_STATUSES - {"completed"}:
                raise VisualProcessAssistantError("assistant_request_not_retryable", status_code=409)
            conversation_id = previous.conversation_id
            question = previous.question_text
        return self.submit_question(
            principal=principal,
            conversation_id=conversation_id,
            question=question,
            client_request_id=client_request_id,
            idempotency_key=idempotency_key,
        )

    def refresh_patch_request(
        self,
        *,
        principal: ChatSessionPrincipal,
        request_id: str,
        payload: Mapping[str, Any],
        client_request_id: str,
        idempotency_key: str,
        patch_enabled: bool,
    ) -> dict[str, Any]:
        """Create a new Hub request bound to the current, unsaved editor draft.

        A patch conflict is never repaired in place.  The previous request and
        its audit history stay immutable while the Hub creates a new context,
        switches the Hub-owned conversation to it and delegates retrieval and
        inference again through the normal task queue.
        """

        if not patch_enabled:
            raise VisualProcessAssistantError("assistant_patch_feature_disabled", status_code=404)
        body = dict(payload or {})
        client_id = _bounded_identifier(client_request_id, "assistant_client_request_id")
        idem = _bounded_identifier(idempotency_key, "assistant_idempotency_key")
        with Session(engine) as db:
            previous = self._owned_request(db, request_id, principal)
            if previous.status != "completed" or not isinstance(previous.response_json, Mapping):
                raise VisualProcessAssistantError("assistant_patch_response_unavailable", status_code=409)
            if not isinstance(previous.response_json.get("workflow_patch"), Mapping):
                raise VisualProcessAssistantError("assistant_patch_missing", status_code=404)

            conversation = self._owned_conversation(
                db,
                previous.conversation_id,
                principal,
                for_update=True,
            )
            if conversation.status != "active":
                raise VisualProcessAssistantError("assistant_conversation_not_active", status_code=409)
            source_context = self._owned_context(db, previous.context_id, principal)
            envelope = EditorContextEnvelope.model_validate(source_context.context_json)
            definition = self._owned_graph(db, conversation.graph_id, principal)
            draft = self._validated_patch_draft(
                definition=definition,
                payload=body.get("draft_graph"),
                required=True,
            )

            extensions = copy.deepcopy(envelope.extensions)
            extensions["ananta.patch_refresh"] = {
                "refresh_of_request_id": previous.id,
                "reason_code": "assistant_patch_conflict_refresh",
            }
            refreshed_envelope = self._contexts.build_context(
                graph=definition,
                draft_graph=draft,
                location=envelope.location,
                editor_mode=envelope.editor_mode,
                repository_revision=envelope.repository_revision,
                codecompass_manifest_hash=envelope.codecompass_manifest_hash,
                source_allowlist_version=envelope.source_allowlist_version,
                prompt_version=PROMPT_VERSION,
                locale=envelope.locale,
                runtime_overlay=(
                    dict(body["runtime_overlay"])
                    if isinstance(body.get("runtime_overlay"), Mapping)
                    else copy.deepcopy(envelope.runtime_overlay)
                ),
                validation_issues=[
                    dict(item) for item in list(body.get("validation_issues") or []) if isinstance(item, Mapping)
                ],
                evidence_refs=envelope.evidence_refs,
                allowed_mutations=envelope.allowed_mutations,
                extensions=extensions,
            )
            refreshed_context = self._store_context(db, principal, refreshed_envelope)
            conversation.active_context_id = refreshed_context.context_id
            conversation.updated_at = float(self._clock())
            db.add(conversation)
            db.commit()
            refreshed_context_id = refreshed_context.context_id
            conversation_id = conversation.id
            question = previous.question_text

        refreshed_request = self.submit_question(
            principal=principal,
            conversation_id=conversation_id,
            question=question,
            client_request_id=client_id,
            idempotency_key=idem,
        )
        return {
            **refreshed_request,
            "refresh_of_request_id": request_id,
            "refresh_context_id": refreshed_context_id,
        }

    def accept_worker_result(
        self,
        *,
        task_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            return self._accept_worker_result(task_id=task_id, result=result)
        except VisualProcessAssistantError as exc:
            self._reject_worker_result(task_id, exc.reason_code)
            raise

    def _accept_worker_result(
        self,
        *,
        task_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = dict(result or {})
        with Session(engine) as db:
            row = db.exec(
                select(VisualProcessAssistantRequestDB).where(
                    (VisualProcessAssistantRequestDB.retrieval_task_id == task_id)
                    | (VisualProcessAssistantRequestDB.inference_task_id == task_id)
                )
            ).first()
            if row is None:
                raise VisualProcessAssistantError("assistant_worker_task_not_found", status_code=404)
            expected_statuses = (
                {"queued_retrieval", "retrieving"}
                if row.retrieval_task_id == task_id
                else {"queued_inference", "inferencing"}
            )
            if row.status not in expected_statuses:
                raise VisualProcessAssistantError(
                    "assistant_worker_result_state_conflict",
                    status_code=409,
                    details={"request_status": row.status},
                )
            if row.retrieval_task_id == task_id:
                request_id = row.id
                prompt_assembly = self._accept_retrieval_result(db, row, task_id, payload)
                db.commit()
                db.refresh(row)
                context = db.get(VisualProcessAssistantContextDB, row.prompt_context_id)
                assert context is not None
                queue_inference = True
            else:
                request_id = row.id
                self._accept_inference_result(db, row, task_id, payload)
                db.commit()
                queue_inference = False
                context = None
                prompt_assembly = None
        self._complete_task(task_id, payload)
        if queue_inference:
            assert context is not None
            try:
                self._queue_inference_by_id(
                    request_id,
                    context,
                    prompt_assembly=prompt_assembly,
                )
            except Exception as exc:
                self._fail_request(request_id, f"inference_queue_failed:{type(exc).__name__}")
                raise VisualProcessAssistantError("assistant_task_queue_unavailable", status_code=503) from exc
        self._refresh_active_metric()
        with Session(engine) as db:
            current = db.get(VisualProcessAssistantRequestDB, request_id)
            assert current is not None
            return self._public_request(current)

    def reconcile_request(
        self,
        *,
        request_id: str,
        principal: ChatSessionPrincipal,
    ) -> None:
        now = float(self._clock())
        task_to_requeue: tuple[str, str] | None = None
        with Session(engine) as db:
            row = self._owned_request(db, request_id, principal, for_update=True)
            if row.status in TERMINAL_REQUEST_STATUSES:
                return
            deadline = (
                row.retrieval_deadline_at
                if row.status in {"queued_retrieval", "retrieving"}
                else row.inference_deadline_at
            )
            if deadline is not None and now > deadline:
                timed_out_phase = "retrieval" if row.status in {"queued_retrieval", "retrieving"} else "inference"
                row.status = "timeout"
                row.error_code = (
                    "assistant_retrieval_timeout" if timed_out_phase == "retrieval" else "assistant_model_timeout"
                )
                row.updated_at = now
                db.add(row)
                db.commit()
                for task_id in (row.retrieval_task_id, row.inference_task_id):
                    if task_id:
                        self._cancel_task(task_id)
                VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status="timeout").inc()
                self._refresh_active_metric()
                return
            task_id = (
                row.retrieval_task_id if row.status in {"queued_retrieval", "retrieving"} else row.inference_task_id
            )
            task = self._task_record(task_id) if task_id else None
            if task is None:
                context_id = (
                    row.context_id if row.status in {"queued_retrieval", "retrieving"} else row.prompt_context_id
                )
                if context_id:
                    task_to_requeue = (
                        "retrieval" if row.status.startswith(("queued_r", "retrieving")) else "inference",
                        context_id,
                    )
            elif str(task.get("status") or "") == "failed":
                row.status = "failed"
                row.error_code = str(task.get("status_reason_code") or "assistant_worker_failed")
                row.updated_at = now
                db.add(row)
                db.commit()
                self._purge_failed_task(task_id, row.error_code)
            elif str(task.get("status") or "") in {"assigned", "in_progress", "running"}:
                row.status = "retrieving" if row.status in {"queued_retrieval", "retrieving"} else "inferencing"
                row.updated_at = now
                db.add(row)
                db.commit()
        if task_to_requeue:
            phase, context_id = task_to_requeue
            with Session(engine) as db:
                context = db.get(VisualProcessAssistantContextDB, context_id)
                row = db.get(VisualProcessAssistantRequestDB, request_id)
                if context is None or row is None:
                    return
                if phase == "retrieval":
                    self._queue_retrieval(row, context)
                else:
                    try:
                        self._queue_inference_by_id(request_id, context)
                    except VisualProcessAssistantError as exc:
                        if exc.reason_code != "assistant_prompt_material_expired":
                            raise
                        self._fail_request(request_id, exc.reason_code)

    # ── patch governance and decision audit ───────────────────────────

    def preview_patch(
        self,
        *,
        principal: ChatSessionPrincipal,
        request_id: str,
        patch_payload: Mapping[str, Any] | None,
        patch_enabled: bool,
        draft_graph_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not patch_enabled:
            raise VisualProcessAssistantError("assistant_patch_feature_disabled", status_code=404)
        with Session(engine) as db:
            request_row = self._owned_request(db, request_id, principal)
            if request_row.status != "completed" or not request_row.response_json:
                raise VisualProcessAssistantError("assistant_patch_response_unavailable", status_code=409)
            response = HelpResponse.model_validate(request_row.response_json)
            candidate = (
                dict(patch_payload or {})
                if patch_payload
                else (response.workflow_patch.model_dump(mode="json") if response.workflow_patch is not None else None)
            )
            if candidate is None:
                raise VisualProcessAssistantError("assistant_patch_missing", status_code=404)
            patch = WorkflowPatch.model_validate(candidate)
            definition = self._owned_graph(db, patch.graph_id, principal)
            context = self._owned_context(db, request_row.prompt_context_id or request_row.context_id, principal)
            envelope = EditorContextEnvelope.model_validate(context.context_json)
            draft = self._validated_patch_draft(
                definition=definition,
                payload=draft_graph_payload,
            )
            if draft.definition_hash() != envelope.draft_hash:
                raise VisualProcessAssistantError(
                    "assistant_patch_context_draft_conflict",
                    status_code=409,
                )
            preview = self._patches.preview(
                graph=draft,
                patch=patch,
                allowed_operations=envelope.allowed_mutations,
            )
            audit = db.exec(
                select(VisualProcessPatchAuditDB).where(
                    VisualProcessPatchAuditDB.request_id == request_row.id,
                    VisualProcessPatchAuditDB.patch_hash == preview.patch_hash,
                )
            ).first()
            if audit is None:
                audit = VisualProcessPatchAuditDB(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject_id,
                    request_id=request_row.id,
                    graph_id=definition.id,
                    context_id=context.context_id,
                    prompt_version=request_row.prompt_version,
                    patch_hash=preview.patch_hash,
                    decision="previewed",
                    reason_codes=list(preview.policy_reason_codes),
                    result_json=preview.as_dict(),
                )
                db.add(audit)
                db.commit()
                db.refresh(audit)
            return {
                **preview.as_dict(),
                "audit_id": audit.id,
                "decision": audit.decision,
                "audit_reason_codes": list(audit.reason_codes),
            }

    def decide_patch(
        self,
        *,
        principal: ChatSessionPrincipal,
        request_id: str,
        patch_hash: str,
        decision: str,
        confirmed: bool,
        patch_enabled: bool,
        approval_mode: str = "interactive",
        auto_approval_enabled: bool = False,
        draft_graph_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = str(decision or "").strip().lower()
        if normalized not in {"accepted", "rejected"}:
            raise VisualProcessAssistantError("assistant_patch_decision_invalid")
        if not patch_enabled:
            raise VisualProcessAssistantError("assistant_patch_feature_disabled", status_code=404)
        approval = None
        if normalized == "accepted":
            try:
                approval = self._patch_approval.authorize_acceptance(
                    mode=approval_mode,
                    confirmed=confirmed,
                    hub_auto_enabled=auto_approval_enabled,
                )
            except VisualProcessPatchApprovalError as exc:
                raise VisualProcessAssistantError(exc.reason_code, status_code=exc.status_code) from exc
        with Session(engine) as db:
            self._owned_request(db, request_id, principal)
            audit = db.exec(
                select(VisualProcessPatchAuditDB)
                .where(
                    VisualProcessPatchAuditDB.request_id == request_id,
                    VisualProcessPatchAuditDB.patch_hash == str(patch_hash),
                    VisualProcessPatchAuditDB.tenant_id == principal.tenant_id,
                    VisualProcessPatchAuditDB.owner_subject == principal.subject_id,
                )
                .with_for_update()
            ).first()
            if audit is None:
                raise VisualProcessAssistantError("assistant_patch_preview_not_found", status_code=404)
            if audit.decision not in {"previewed", normalized}:
                raise VisualProcessAssistantError("assistant_patch_decision_conflict", status_code=409)
            if normalized == "accepted":
                request_row = db.get(VisualProcessAssistantRequestDB, request_id)
                assert request_row is not None and request_row.response_json is not None
                response = HelpResponse.model_validate(request_row.response_json)
                if response.workflow_patch is None:
                    raise VisualProcessAssistantError("assistant_patch_missing", status_code=404)
                definition = self._owned_graph(db, audit.graph_id, principal)
                context = self._owned_context(db, audit.context_id, principal)
                envelope = EditorContextEnvelope.model_validate(context.context_json)
                draft = self._validated_patch_draft(
                    definition=definition,
                    payload=draft_graph_payload,
                )
                expected_draft_hash = str(audit.result_json.get("input_draft_hash") or "")
                if (
                    not expected_draft_hash
                    or draft.definition_hash() != expected_draft_hash
                    or draft.definition_hash() != envelope.draft_hash
                ):
                    raise VisualProcessAssistantError(
                        "assistant_patch_decision_draft_conflict",
                        status_code=409,
                    )
                current_preview = self._patches.preview(
                    graph=draft,
                    patch=response.workflow_patch,
                    allowed_operations=envelope.allowed_mutations,
                )
                if current_preview.patch_hash != audit.patch_hash:
                    raise VisualProcessAssistantError("assistant_patch_hash_conflict", status_code=409)
                audit.result_json = current_preview.as_dict()
                audit.reason_codes = sorted(
                    {
                        *current_preview.policy_reason_codes,
                        approval.reason_code,
                    }
                )
            else:
                audit.reason_codes = sorted({*audit.reason_codes, "patch_user_rejected"})
            audit.decision = normalized
            audit.decided_at = float(self._clock())
            db.add(audit)
            db.commit()
            return {
                "audit_id": audit.id,
                "request_id": audit.request_id,
                "patch_hash": audit.patch_hash,
                "decision": audit.decision,
                "reason_codes": list(audit.reason_codes),
                "approval_mode": approval.mode if approval is not None else "none",
                "human_intervention_required": (
                    approval.human_intervention_required if approval is not None else False
                ),
                "apply_mode": "local_editor_command_only" if normalized == "accepted" else "none",
                "preview": copy.deepcopy(audit.result_json),
            }

    # ── private persistence/policy helpers ────────────────────────────

    def _accept_retrieval_result(
        self,
        db: Session,
        row: VisualProcessAssistantRequestDB,
        task_id: str,
        payload: dict[str, Any],
    ) -> VisualProcessPromptAssembly:
        if str(payload.get("schema") or "") != RETRIEVAL_RESULT_SCHEMA:
            raise VisualProcessAssistantError("assistant_retrieval_result_schema_invalid")
        if str(payload.get("status") or "") != "completed":
            raise VisualProcessAssistantError("assistant_retrieval_result_status_invalid")
        self._validate_worker_binding(row, task_id, payload)
        source_context = self._owned_context(
            db,
            row.context_id,
            ChatSessionPrincipal.from_values(row.tenant_id, row.owner_subject),
        )
        context = EditorContextEnvelope.model_validate(source_context.context_json)
        allowed = {
            (
                item.source_id,
                item.source_version,
                item.tenant_id,
                item.scope,
                item.provenance_digest.removeprefix("sha256:") if item.provenance_digest else None,
            )
            for item in context.evidence_refs
            if item.verification_status == VerificationStatus.verified
        }
        allowed_source_ids = {str(identity[0]) for identity in allowed if identity[0]}
        consistency = str(payload.get("consistency_state") or "degraded")
        if consistency not in {
            "current",
            "degraded",
            "stale",
            "stale_context",
            "no_results",
            "rejected",
            "conflict",
        }:
            raise VisualProcessAssistantError("assistant_retrieval_consistency_state_invalid")
        rejection_reasons = sorted({str(item) for item in list(payload.get("rejection_reasons") or []) if str(item)})
        rejected_count = int(payload.get("rejected_count") or 0)
        blocked_sources = _validated_blocked_source_audit(
            payload.get("blocked_stubs"),
            allowed_source_ids=allowed_source_ids,
        )
        evidence_conflicts = _validated_evidence_conflicts(
            payload.get("evidence_conflicts"),
            allowed_source_ids=allowed_source_ids,
        )
        if bool(evidence_conflicts) != (consistency == "conflict"):
            raise VisualProcessAssistantError("assistant_retrieval_conflict_state_invalid")
        if consistency != "current":
            lifecycle_status = (
                "stale"
                if consistency in {"stale", "stale_context"}
                or any("stale" in reason or "revision_mismatch" in reason for reason in rejection_reasons)
                else "rejected"
            )
            VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status=lifecycle_status).inc()
        if rejected_count > 0:
            VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status="rejected").inc(rejected_count)
        accepted: list[EvidenceRef] = []
        if consistency in {"current", "conflict"}:
            for raw in list(payload.get("evidence") or []):
                try:
                    evidence = EvidenceRef.model_validate(raw)
                except (TypeError, ValueError) as exc:
                    raise VisualProcessAssistantError("assistant_retrieval_evidence_invalid") from exc
                identity = (
                    evidence.source_id,
                    evidence.source_version,
                    evidence.tenant_id,
                    evidence.scope,
                    evidence.provenance_digest.removeprefix("sha256:") if evidence.provenance_digest else None,
                )
                if identity not in allowed:
                    raise VisualProcessAssistantError("assistant_retrieval_evidence_not_allowed", status_code=403)
                if evidence.verification_status != VerificationStatus.verified:
                    raise VisualProcessAssistantError("assistant_retrieval_evidence_unverified")
                accepted.append(evidence)
        enriched = self._contexts.with_projected_evidence(
            context,
            accepted,
            budget_profile="conversation",
        )
        transient_evidence = list(enriched.evidence_refs)
        reference_evidence = [item.model_copy(update={"excerpt": None}) for item in transient_evidence]
        reference_context = enriched.model_copy(update={"evidence_refs": reference_evidence})
        reference_context.canonical_bytes()
        prompt_context_row = self._store_context(
            db,
            ChatSessionPrincipal.from_values(row.tenant_id, row.owner_subject),
            reference_context,
        )
        assembly = self._contexts.assemble_prompt(
            reference_context,
            question_text=row.question_text,
            evidence_override=transient_evidence,
        )
        if assembly.context_id != prompt_context_row.context_id:
            raise VisualProcessAssistantError("assistant_prompt_context_binding_invalid")
        prompt_evidence_ids = set(assembly.approved_evidence_refs)
        accepted_references = [item for item in reference_evidence if item.evidence_id in prompt_evidence_ids]
        row.prompt_context_id = prompt_context_row.context_id
        row.accepted_evidence_json = [item.model_dump(mode="json") for item in accepted_references]
        row.prompt_snapshot_json = {
            **assembly.as_dict(include_prompt=False),
            "retrieval_consistency_state": consistency,
            "retrieval_rejected_count": rejected_count,
            "retrieval_rejection_reasons": rejection_reasons,
            "retrieval_blocked_sources": blocked_sources,
            "retrieval_evidence_conflicts": evidence_conflicts,
        }
        row.status = "queued_inference"
        row.error_code = _retrieval_error_code(
            consistency=consistency,
            rejection_reasons=rejection_reasons,
            accepted_count=len(accepted_references),
        )
        row.inference_deadline_at = float(self._clock()) + self._model_timeout_ms / 1000.0
        row.updated_at = float(self._clock())
        db.add(row)
        return assembly

    def _accept_inference_result(
        self,
        db: Session,
        row: VisualProcessAssistantRequestDB,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        if str(payload.get("schema") or "") != INFERENCE_RESULT_SCHEMA:
            raise VisualProcessAssistantError("assistant_inference_result_schema_invalid")
        if str(payload.get("status") or "") != "completed":
            raise VisualProcessAssistantError("assistant_inference_result_status_invalid")
        self._validate_worker_binding(row, task_id, payload)
        prompt_hash = str((row.prompt_snapshot_json or {}).get("prompt_hash") or "")
        if str(payload.get("prompt_hash") or "") != prompt_hash:
            raise VisualProcessAssistantError("assistant_inference_prompt_hash_mismatch", status_code=409)
        response = HelpResponse.model_validate(payload.get("response") or {})
        if response.context_id != row.prompt_context_id or response.prompt_version != row.prompt_version:
            raise VisualProcessAssistantError("assistant_inference_context_mismatch", status_code=409)
        accepted_evidence = {
            str(item.get("evidence_id") or ""): EvidenceRef.model_validate(item) for item in row.accepted_evidence_json
        }
        if any(
            item.evidence_id not in accepted_evidence
            or item.model_dump(mode="json") != accepted_evidence[item.evidence_id].model_dump(mode="json")
            for item in response.evidence
        ):
            raise VisualProcessAssistantError("assistant_inference_evidence_forged", status_code=403)
        if response.workflow_patch is not None:
            if not settings.visual_process_ai_patches_enabled:
                raise VisualProcessAssistantError("assistant_patch_feature_disabled_in_result", status_code=403)
            consistency = str((row.prompt_snapshot_json or {}).get("retrieval_consistency_state") or "degraded")
            if consistency != "current":
                raise VisualProcessAssistantError(
                    "assistant_patch_evidence_not_current",
                    status_code=409,
                )
        row.response_json = response.model_dump(mode="json")
        row.status = "completed"
        # A successful inference does not make a degraded retrieval healthy.
        # Preserve the content-free retrieval state unless the Worker returns a
        # more specific inference diagnostic such as ``model_output_invalid``.
        row.error_code = str(payload.get("reason_code") or "") or row.error_code
        row.updated_at = float(self._clock())
        db.add(row)
        VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status="completed").inc()

    @staticmethod
    def _validate_worker_binding(
        row: VisualProcessAssistantRequestDB,
        task_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if str(payload.get("task_id") or "") != task_id:
            raise VisualProcessAssistantError("assistant_worker_task_binding_mismatch", status_code=409)
        if str(payload.get("request_id") or "") != row.id:
            raise VisualProcessAssistantError("assistant_worker_request_binding_mismatch", status_code=409)
        expected_context = row.context_id if row.retrieval_task_id == task_id else row.prompt_context_id
        if str(payload.get("context_id") or "") != str(expected_context or ""):
            raise VisualProcessAssistantError("assistant_worker_context_binding_mismatch", status_code=409)

    def _queue_retrieval(
        self,
        request_row: VisualProcessAssistantRequestDB,
        context_row: VisualProcessAssistantContextDB,
    ) -> None:
        context = EditorContextEnvelope.model_validate(context_row.context_json)
        editor_query = CodeCompassEditorQueryInput.from_editor_context(
            context,
            user_language=request_row.question_text,
        )
        retrieval_budget = self._contexts.context_budget(editor_query.detail_level.value)
        task_id = request_row.retrieval_task_id or f"vpa-retrieval-{request_row.id.removeprefix('vpa-req-')}"
        source_scope = str(context.extensions.get("ananta.source_scope") or "")
        source_refs = [
            {
                "schema": "ananta.source_ref.v2",
                "source_id": item.source_id,
                "source_version": item.source_version,
                "tenant_id": item.tenant_id,
                "scope": item.scope,
                "provenance_digest": item.provenance_digest,
            }
            for item in context.evidence_refs
            if item.verification_status == VerificationStatus.verified
        ]
        envelope = {
            "schema": RETRIEVAL_JOB_SCHEMA,
            "request_id": request_row.id,
            "context_id": context_row.context_id,
            "tenant_id": request_row.tenant_id,
            "source_scope": source_scope,
            "question": editor_query.retrieval_query(),
            "editor_query": editor_query.as_dict(),
            "retrieval_intent": editor_query.intent.value,
            "repository_revision": context.repository_revision,
            "codecompass_manifest_hash": context.codecompass_manifest_hash,
            "source_allowlist_version": context.source_allowlist_version,
            "model_scope": "local_model",
            "context_policy_version": ASSISTANT_CONTEXT_POLICY_VERSION,
            "allowed_source_refs": source_refs,
            "max_evidence_items": retrieval_budget.max_evidence_items,
            "deadline_at": request_row.retrieval_deadline_at,
            "hub_authorization": {
                "issuer": "ananta-hub",
                "transport": "authenticated_hub_task_queue",
                "task_id": task_id,
            },
        }
        envelope["envelope_hash"] = _envelope_hash(envelope)
        self._ingest_task(
            task_id=task_id,
            request_id=request_row.id,
            task_kind="visual_process_assistant_retrieval",
            title="Visual Process Assistant: Evidence abrufen",
            envelope=envelope,
            required_capabilities=["retrieval", "codecompass"],
            verification_schema=RETRIEVAL_RESULT_SCHEMA,
        )
        with Session(engine) as db:
            row = db.get(VisualProcessAssistantRequestDB, request_row.id)
            if row is not None and row.status in {"queued_retrieval", "retrieving"}:
                row.retrieval_task_id = task_id
                row.status = "queued_retrieval"
                row.updated_at = float(self._clock())
                db.add(row)
                db.commit()

    def _queue_inference_by_id(
        self,
        request_id: str,
        context_row: VisualProcessAssistantContextDB,
        *,
        prompt_assembly: VisualProcessPromptAssembly | None = None,
    ) -> None:
        with Session(engine) as db:
            request_row = db.get(VisualProcessAssistantRequestDB, request_id)
            if request_row is None:
                raise VisualProcessAssistantError("assistant_request_not_found", status_code=404)
            context = EditorContextEnvelope.model_validate(context_row.context_json)
            if prompt_assembly is None:
                if request_row.accepted_evidence_json:
                    # Repository excerpts are deliberately ephemeral.  A lost
                    # inference task cannot be recreated after a Hub restart;
                    # the user can retry, which performs fresh retrieval.
                    raise VisualProcessAssistantError(
                        "assistant_prompt_material_expired",
                        status_code=409,
                    )
                prompt_assembly = self._contexts.assemble_prompt(
                    context,
                    question_text=request_row.question_text,
                )
            if prompt_assembly.context_id != context_row.context_id:
                raise VisualProcessAssistantError("assistant_prompt_context_binding_invalid")
            task_id = request_row.inference_task_id or f"vpa-inference-{request_row.id.removeprefix('vpa-req-')}"
            envelope = {
                "schema": INFERENCE_JOB_SCHEMA,
                "request_id": request_row.id,
                "context_id": context_row.context_id,
                "prompt_version": request_row.prompt_version,
                "prompt": prompt_assembly.prompt_text,
                "prompt_hash": prompt_assembly.prompt_hash,
                "estimated_prompt_tokens": prompt_assembly.estimated_prompt_tokens,
                "max_prompt_tokens": prompt_assembly.max_prompt_tokens,
                "location": context.location.model_dump(mode="json"),
                "approved_evidence": copy.deepcopy(request_row.accepted_evidence_json),
                "repository_revision": context.repository_revision,
                "codecompass_manifest_hash": context.codecompass_manifest_hash,
                "source_allowlist_version": context.source_allowlist_version,
                "model_scope": "local_model",
                "context_policy_version": ASSISTANT_CONTEXT_POLICY_VERSION,
                "deadline_at": request_row.inference_deadline_at,
                "hub_authorization": {
                    "issuer": "ananta-hub",
                    "transport": "authenticated_hub_task_queue",
                    "task_id": task_id,
                },
            }
            envelope["envelope_hash"] = _envelope_hash(envelope)
        self._ingest_task(
            task_id=task_id,
            request_id=request_id,
            task_kind="visual_process_assistant_inference",
            title="Visual Process Assistant: belegte Antwort erzeugen",
            envelope=envelope,
            required_capabilities=["llm", "structured_output"],
            verification_schema=INFERENCE_RESULT_SCHEMA,
        )
        with Session(engine) as db:
            row = db.get(VisualProcessAssistantRequestDB, request_id)
            if row is not None and row.status in {"queued_inference", "inferencing"}:
                row.inference_task_id = task_id
                row.status = "queued_inference"
                row.updated_at = float(self._clock())
                db.add(row)
                db.commit()

    @staticmethod
    def _ingest_task(
        *,
        task_id: str,
        request_id: str,
        task_kind: str,
        title: str,
        envelope: Mapping[str, Any],
        required_capabilities: list[str],
        verification_schema: str,
    ) -> None:
        from agent.services.task_queue_service import get_task_queue_service

        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="todo",
            title=title,
            description="Worker-delegated Visual Process Assistant phase.",
            priority="medium",
            created_by="visual-process-assistant-hub",
            source="visual_process_assistant",
            tags=["visual_process_assistant", "hub_delegated", "persistent_job"],
            event_type="visual_process_assistant_task_queued",
            event_channel="hub_task_queue",
            event_details={"request_id": request_id, "task_kind": task_kind},
            extra_fields={
                "task_kind": task_kind,
                "retrieval_intent": "grounded_editor_help",
                "required_context_scope": "visual_process_editor",
                "required_capabilities": required_capabilities,
                "worker_execution_context": {"visual_process_assistant_job": dict(envelope)},
                "verification_spec": {
                    "schema": verification_schema,
                    "request_id": request_id,
                    "hub_result_acceptance_required": True,
                },
            },
        )

    @staticmethod
    def _store_context(
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

    @staticmethod
    def _owned_graph(
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

    @staticmethod
    def _validated_patch_draft(
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

    @staticmethod
    def _owned_context(
        db: Session,
        context_id: str,
        principal: ChatSessionPrincipal,
    ) -> VisualProcessAssistantContextDB:
        row = db.get(VisualProcessAssistantContextDB, str(context_id))
        if row is None or row.tenant_id != principal.tenant_id or row.owner_subject != principal.subject_id:
            raise VisualProcessAssistantError("assistant_context_not_found", status_code=404)
        return row

    @staticmethod
    def _owned_conversation(
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

    @staticmethod
    def _owned_request(
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

    def _consume_rate_limit(
        self,
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

    @staticmethod
    def _public_context(row: VisualProcessAssistantContextDB) -> dict[str, Any]:
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

    @staticmethod
    def _public_conversation(row: VisualProcessAssistantConversationDB) -> dict[str, Any]:
        return {
            "conversation_id": row.id,
            "graph_id": row.graph_id,
            "status": row.status,
            "active_context_id": row.active_context_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _public_request(row: VisualProcessAssistantRequestDB) -> dict[str, Any]:
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

    @staticmethod
    def _task_record(task_id: str | None) -> dict[str, Any] | None:
        if not task_id:
            return None
        from agent.repository import task_repo

        row = task_repo.get_by_id(task_id)
        return row.model_dump() if row is not None else None

    @classmethod
    def _cancel_task(cls, task_id: str) -> None:
        from agent.services.task_runtime_service import update_local_task_status

        current = cls._task_record(task_id)
        if current is not None and str(current.get("status") or "") in {"completed", "cancelled"}:
            return
        update_local_task_status(
            task_id,
            "cancelled",
            force=True,
            worker_execution_context={"assistant_payload_purged": True},
            event_type="visual_process_assistant_cancelled",
            event_actor="visual-process-assistant-hub",
        )

    @staticmethod
    def _complete_task(task_id: str, result: Mapping[str, Any]) -> None:
        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status(
            task_id,
            "completed",
            force=True,
            worker_execution_context={"assistant_payload_purged": True},
            verification_status={
                "visual_process_assistant_result": {
                    "schema": result.get("schema"),
                    "request_id": result.get("request_id"),
                    "context_id": result.get("context_id"),
                    "status": result.get("status"),
                    "reason_code": result.get("reason_code"),
                }
            },
            event_type="visual_process_assistant_worker_result_accepted",
            event_actor="visual-process-assistant-hub",
        )

    @classmethod
    def _purge_failed_task(cls, task_id: str, error_code: str) -> None:
        """Remove transient prompt material while preserving terminal truth."""

        from agent.services.task_runtime_service import update_local_task_status

        task = cls._task_record(task_id)
        if task is None or str(task.get("status") or "") in {"completed", "cancelled"}:
            return
        update_local_task_status(
            task_id,
            "failed",
            force=True,
            status_reason_code=str(error_code)[:500],
            worker_execution_context={"assistant_payload_purged": True},
            event_type="visual_process_assistant_failed_payload_purged",
            event_actor="visual-process-assistant-hub",
        )

    def _reject_worker_result(self, task_id: str, error_code: str) -> None:
        """Fail an active request and erase a rejected worker envelope."""

        with Session(engine) as db:
            row = db.exec(
                select(VisualProcessAssistantRequestDB).where(
                    (VisualProcessAssistantRequestDB.retrieval_task_id == task_id)
                    | (VisualProcessAssistantRequestDB.inference_task_id == task_id)
                )
            ).first()
            request_id = row.id if row is not None else None
        if request_id is not None:
            self._fail_request(request_id, error_code)
        else:
            self._purge_failed_task(task_id, error_code)

    def _fail_request(self, request_id: str, error_code: str) -> None:
        task_ids: tuple[str | None, str | None] = (None, None)
        with Session(engine) as db:
            row = db.get(VisualProcessAssistantRequestDB, request_id)
            if row is None or row.status in TERMINAL_REQUEST_STATUSES:
                return
            task_ids = (row.retrieval_task_id, row.inference_task_id)
            row.status = "failed"
            row.error_code = str(error_code)[:500]
            row.updated_at = float(self._clock())
            db.add(row)
            db.commit()
        for task_id in task_ids:
            if task_id:
                self._purge_failed_task(task_id, error_code)
        VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status="failed").inc()
        self._refresh_active_metric()

    @staticmethod
    def _refresh_active_metric() -> None:
        try:
            with Session(engine) as db:
                count = len(
                    db.exec(
                        select(VisualProcessAssistantRequestDB.id).where(
                            VisualProcessAssistantRequestDB.status.in_(ACTIVE_REQUEST_STATUSES)
                        )
                    ).all()
                )
            VISUAL_PROCESS_ASSISTANT_ACTIVE.set(count)
        except Exception:
            # Metrics are advisory and must never alter the request lifecycle.
            return


def _retrieval_error_code(
    *,
    consistency: str,
    rejection_reasons: list[str],
    accepted_count: int,
) -> str | None:
    """Project retrieval diagnostics to stable, content-free UI states."""

    state = str(consistency or "degraded").strip().lower()
    reasons = {str(reason or "").strip().lower() for reason in rejection_reasons}
    if state == "current" and accepted_count > 0:
        return None
    if state in {"stale", "stale_context"} or any(
        "stale" in reason or "revision_mismatch" in reason or "manifest_mismatch" in reason for reason in reasons
    ):
        return "assistant_stale_context"
    if state == "conflict" or "evidence_conflict" in reasons:
        return "assistant_evidence_conflict"
    if (
        state == "no_results"
        or not accepted_count
        and any(
            reason
            in {
                "no_results",
                "production_channel_empty",
                "retrieval_provider_unconfigured",
            }
            for reason in reasons
        )
    ):
        return "assistant_no_results"
    if state == "rejected" or reasons:
        return "assistant_evidence_rejected"
    return "assistant_no_results" if accepted_count == 0 else "assistant_evidence_degraded"


def _validated_blocked_source_audit(
    raw_value: Any,
    *,
    allowed_source_ids: set[str],
) -> list[dict[str, Any]]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        raise VisualProcessAssistantError("assistant_retrieval_blocked_stubs_invalid")
    audit: list[dict[str, Any]] = []
    for raw in raw_value:
        if not isinstance(raw, Mapping) or set(raw) != {"source_id", "reason_codes", "safe_stub"}:
            raise VisualProcessAssistantError("assistant_retrieval_blocked_stub_invalid")
        source_id = str(raw.get("source_id") or "")
        reasons = sorted({str(item) for item in list(raw.get("reason_codes") or []) if str(item)})
        expected_stub = f"[REPOSITORY EVIDENCE BLOCKED] source_id={source_id} reasons={','.join(reasons)}"
        if (
            source_id not in allowed_source_ids
            or not reasons
            or len(reasons) > 20
            or str(raw.get("safe_stub") or "") != expected_stub
        ):
            raise VisualProcessAssistantError("assistant_retrieval_blocked_stub_invalid")
        audit.append({"source_id": source_id, "reason_codes": reasons})
    return sorted(audit, key=lambda item: item["source_id"])


def _validated_evidence_conflicts(
    raw_value: Any,
    *,
    allowed_source_ids: set[str],
) -> list[dict[str, Any]]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        raise VisualProcessAssistantError("assistant_retrieval_evidence_conflicts_invalid")
    conflicts: list[dict[str, Any]] = []
    for raw in raw_value:
        if not isinstance(raw, Mapping) or set(raw) != {"conflict_key", "source_ids", "reason_code"}:
            raise VisualProcessAssistantError("assistant_retrieval_evidence_conflict_invalid")
        conflict_key = str(raw.get("conflict_key") or "").strip()
        source_ids = sorted({str(item) for item in list(raw.get("source_ids") or []) if str(item)})
        if (
            not conflict_key
            or len(conflict_key) > 200
            or any(not char.isalnum() and char not in "._:/-" for char in conflict_key)
            or len(source_ids) < 2
            or not set(source_ids).issubset(allowed_source_ids)
            or str(raw.get("reason_code") or "") != "evidence_conflict"
        ):
            raise VisualProcessAssistantError("assistant_retrieval_evidence_conflict_invalid")
        conflicts.append(
            {
                "conflict_key": conflict_key,
                "source_ids": source_ids,
                "reason_code": "evidence_conflict",
            }
        )
    return sorted(conflicts, key=lambda item: (item["conflict_key"], item["source_ids"]))


visual_process_assistant_service = VisualProcessAssistantService()


__all__ = [
    "ACTIVE_REQUEST_STATUSES",
    "TERMINAL_REQUEST_STATUSES",
    "VisualProcessAssistantError",
    "VisualProcessAssistantService",
    "visual_process_assistant_service",
]
