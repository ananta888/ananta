"""Hub task-queue delegation for the Visual Process Assistant.

Builds the signed-by-hash retrieval and inference job envelopes, ingests them
as central Hub tasks and drives the Hub-side task status transitions
(cancel, accept, purge).  Workers only ever see these tasks through the
normal Hub queue; nothing here executes retrieval or model work.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from sqlmodel import Session

from agent.database import engine
from agent.db_models.visual_process_assistant import (
    VisualProcessAssistantContextDB,
    VisualProcessAssistantRequestDB,
)
from agent.services.codecompass_editor_context_contract import (
    CodeCompassEditorQueryInput,
)
from agent.services.visual_process_assistant_errors import VisualProcessAssistantError
from agent.services.visual_process_assistant_validation import (
    envelope_hash as _envelope_hash,
)
from agent.services.visual_process_context_service import (
    VisualProcessContextService,
    VisualProcessPromptAssembly,
)
from ananta_contracts.visual_process_assistant import (
    ASSISTANT_CONTEXT_POLICY_VERSION,
    ASSISTANT_INFERENCE_JOB_VERSION,
    ASSISTANT_INFERENCE_RESULT_VERSION,
    ASSISTANT_RETRIEVAL_JOB_VERSION,
    ASSISTANT_RETRIEVAL_RESULT_VERSION,
    EditorContextEnvelope,
    VerificationStatus,
)

INFERENCE_JOB_SCHEMA = ASSISTANT_INFERENCE_JOB_VERSION
INFERENCE_RESULT_SCHEMA = ASSISTANT_INFERENCE_RESULT_VERSION
RETRIEVAL_JOB_SCHEMA = ASSISTANT_RETRIEVAL_JOB_VERSION
RETRIEVAL_RESULT_SCHEMA = ASSISTANT_RETRIEVAL_RESULT_VERSION


class VisualProcessAssistantTaskDelegation:
    """Queue assistant phases as Hub tasks and control their lifecycle."""

    def __init__(
        self,
        *,
        context_service: VisualProcessContextService,
        clock: Callable[[], float],
    ) -> None:
        self._contexts = context_service
        self._clock = clock

    def queue_retrieval(
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
        self.ingest_task(
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

    def queue_inference(
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
        self.ingest_task(
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
    def ingest_task(
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
    def task_record(task_id: str | None) -> dict[str, Any] | None:
        if not task_id:
            return None
        from agent.repository import task_repo

        row = task_repo.get_by_id(task_id)
        return row.model_dump() if row is not None else None

    def cancel_task(self, task_id: str) -> None:
        from agent.services.task_runtime_service import update_local_task_status

        current = self.task_record(task_id)
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
    def complete_task(task_id: str, result: Mapping[str, Any]) -> None:
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

    def purge_failed_task(self, task_id: str, error_code: str) -> None:
        """Remove transient prompt material while preserving terminal truth."""

        from agent.services.task_runtime_service import update_local_task_status

        task = self.task_record(task_id)
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


__all__ = [
    "INFERENCE_JOB_SCHEMA",
    "RETRIEVAL_JOB_SCHEMA",
    "VisualProcessAssistantTaskDelegation",
]
