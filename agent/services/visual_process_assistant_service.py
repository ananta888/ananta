"""Persistent Hub orchestration for contextual Visual Process assistance.

This service is the control plane.  It owns conversations, immutable context
snapshots, task creation, evidence acceptance, cancellation and patch audit.
Delegable retrieval and model work is represented exclusively as central Hub
tasks and is never executed here.

The service composes narrow collaborators, each injectable through a
keyword-only constructor parameter:

* :class:`VisualProcessAssistantContextAdmission` - validation of the
  editor draft and Hub source-catalog authority for a new context;
* :mod:`visual_process_assistant_records` - tenant-scoped row lookups,
  context storage, rate limit and public projections;
* :class:`VisualProcessAssistantTaskDelegation` - Hub task envelopes and
  task status transitions;
* :class:`VisualProcessAssistantResultAcceptance` - validation of worker
  retrieval/inference results;
* :class:`VisualProcessAssistantPatchGovernance` - patch preview and
  decision audit.
"""

from __future__ import annotations

import copy
import hashlib
import time
from collections.abc import Mapping
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.config import settings
from agent.database import engine
from agent.db_models.visual_process_assistant import (
    VisualProcessAssistantContextDB,
    VisualProcessAssistantConversationDB,
    VisualProcessAssistantRequestDB,
)
from agent.metrics import (
    VISUAL_PROCESS_ASSISTANT_ACTIVE,
    VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL,
)
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.source_catalog_authority_service import (
    SourceCatalogAuthorityService,
    get_source_catalog_authority_service,
)
from agent.services.visual_process_assistant_context_admission import (
    ASSISTANT_CATALOG_TASK_KINDS as ASSISTANT_CATALOG_TASK_KINDS,
)
from agent.services.visual_process_assistant_context_admission import (
    ASSISTANT_CATALOG_TASK_SOURCES as ASSISTANT_CATALOG_TASK_SOURCES,
)
from agent.services.visual_process_assistant_context_admission import (
    VisualProcessAssistantContextAdmission,
)
from agent.services.visual_process_assistant_errors import (
    VisualProcessAssistantError as VisualProcessAssistantError,
)
from agent.services.visual_process_assistant_patch_governance import (
    VisualProcessAssistantPatchGovernance,
)
from agent.services.visual_process_assistant_records import (
    MAX_REQUESTS_PER_MINUTE as MAX_REQUESTS_PER_MINUTE,
)
from agent.services.visual_process_assistant_records import (
    consume_rate_limit,
    owned_context,
    owned_conversation,
    owned_graph,
    owned_request,
    public_context,
    public_conversation,
    public_request,
    store_context,
    validated_patch_draft,
)
from agent.services.visual_process_assistant_result_acceptance import (
    INFERENCE_RESULT_SCHEMA as INFERENCE_RESULT_SCHEMA,
)
from agent.services.visual_process_assistant_result_acceptance import (
    RETRIEVAL_RESULT_SCHEMA as RETRIEVAL_RESULT_SCHEMA,
)
from agent.services.visual_process_assistant_result_acceptance import (
    VisualProcessAssistantResultAcceptance,
)
from agent.services.visual_process_assistant_task_delegation import (
    INFERENCE_JOB_SCHEMA as INFERENCE_JOB_SCHEMA,
)
from agent.services.visual_process_assistant_task_delegation import (
    RETRIEVAL_JOB_SCHEMA as RETRIEVAL_JOB_SCHEMA,
)
from agent.services.visual_process_assistant_task_delegation import (
    VisualProcessAssistantTaskDelegation,
)
from agent.services.visual_process_assistant_validation import (
    bounded_identifier as _bounded_identifier,
)
from agent.services.visual_process_assistant_validation import (
    stable_hash as _stable_hash,
)
from agent.services.visual_process_context_service import (
    PROMPT_VERSION,
    VisualProcessContextService,
    VisualProcessPromptAssembly,
)
from agent.services.visual_process_patch_approval_policy import (
    VisualProcessPatchApprovalPolicy,
)
from agent.services.visual_process_patch_service import (
    VisualProcessPatchService,
)
from ananta_contracts.visual_process_assistant import (
    EditorContextEnvelope,
)

ACTIVE_REQUEST_STATUSES = frozenset({"queued_retrieval", "retrieving", "queued_inference", "inferencing"})
TERMINAL_REQUEST_STATUSES = frozenset({"completed", "failed", "cancelled", "timeout", "rejected"})
MAX_QUESTION_CHARS = 8_000
MAX_ACTIVE_PER_CONVERSATION = 2


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
        context_admission: VisualProcessAssistantContextAdmission | None = None,
        task_delegation: VisualProcessAssistantTaskDelegation | None = None,
        result_acceptance: VisualProcessAssistantResultAcceptance | None = None,
        patch_governance: VisualProcessAssistantPatchGovernance | None = None,
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
        self._context_admission = context_admission or VisualProcessAssistantContextAdmission(
            context_service=self._contexts,
            source_authority=self._source_authority,
        )
        self._tasks = task_delegation or VisualProcessAssistantTaskDelegation(
            context_service=self._contexts,
            clock=self._clock,
        )
        self._results = result_acceptance or VisualProcessAssistantResultAcceptance(
            context_service=self._contexts,
            clock=self._clock,
            model_timeout_ms=self._model_timeout_ms,
        )
        self._patch_governance = patch_governance or VisualProcessAssistantPatchGovernance(
            patch_service=self._patches,
            patch_approval_policy=self._patch_approval,
            clock=self._clock,
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
            envelope = self._context_admission.admit(principal=principal, graph=graph, body=body)
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
        return self._patch_governance.preview(
            principal=principal,
            request_id=request_id,
            patch_payload=patch_payload,
            patch_enabled=patch_enabled,
            draft_graph_payload=draft_graph_payload,
        )

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
        return self._patch_governance.decide(
            principal=principal,
            request_id=request_id,
            patch_hash=patch_hash,
            decision=decision,
            confirmed=confirmed,
            patch_enabled=patch_enabled,
            approval_mode=approval_mode,
            auto_approval_enabled=auto_approval_enabled,
            draft_graph_payload=draft_graph_payload,
        )

    # ── collaborator seams (kept as methods for existing call sites) ──

    def _accept_retrieval_result(
        self,
        db: Session,
        row: VisualProcessAssistantRequestDB,
        task_id: str,
        payload: dict[str, Any],
    ) -> VisualProcessPromptAssembly:
        return self._results.accept_retrieval(db, row, task_id, payload)

    def _accept_inference_result(
        self,
        db: Session,
        row: VisualProcessAssistantRequestDB,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        self._results.accept_inference(db, row, task_id, payload)

    def _queue_retrieval(
        self,
        request_row: VisualProcessAssistantRequestDB,
        context_row: VisualProcessAssistantContextDB,
    ) -> None:
        self._tasks.queue_retrieval(request_row, context_row)

    def _queue_inference_by_id(
        self,
        request_id: str,
        context_row: VisualProcessAssistantContextDB,
        *,
        prompt_assembly: VisualProcessPromptAssembly | None = None,
    ) -> None:
        self._tasks.queue_inference(request_id, context_row, prompt_assembly=prompt_assembly)

    def _task_record(self, task_id: str | None) -> dict[str, Any] | None:
        return self._tasks.task_record(task_id)

    def _cancel_task(self, task_id: str) -> None:
        self._tasks.cancel_task(task_id)

    def _complete_task(self, task_id: str, result: Mapping[str, Any]) -> None:
        self._tasks.complete_task(task_id, result)

    def _purge_failed_task(self, task_id: str, error_code: str) -> None:
        self._tasks.purge_failed_task(task_id, error_code)

    _store_context = staticmethod(store_context)
    _owned_graph = staticmethod(owned_graph)
    _validated_patch_draft = staticmethod(validated_patch_draft)
    _owned_context = staticmethod(owned_context)
    _owned_conversation = staticmethod(owned_conversation)
    _owned_request = staticmethod(owned_request)
    _consume_rate_limit = staticmethod(consume_rate_limit)
    _public_context = staticmethod(public_context)
    _public_conversation = staticmethod(public_conversation)
    _public_request = staticmethod(public_request)

    # ── request failure handling ──────────────────────────────────────

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


visual_process_assistant_service = VisualProcessAssistantService()


__all__ = [
    "ACTIVE_REQUEST_STATUSES",
    "TERMINAL_REQUEST_STATUSES",
    "VisualProcessAssistantError",
    "VisualProcessAssistantService",
    "visual_process_assistant_service",
]
