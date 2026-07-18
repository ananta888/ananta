from __future__ import annotations

import hashlib
import json
import os
import uuid

import pytest
from sqlmodel import Session

from agent.database import engine
from agent.db_models.visual_process_assistant import (
    VisualProcessAssistantContextDB,
    VisualProcessAssistantConversationDB,
    VisualProcessAssistantRequestDB,
)
from agent.services.chat_process_binding import bind_graph_owner
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.source_catalog_authority_service import ResolvedSourceCatalog
from agent.services.visual_process_assistant_service import VisualProcessAssistantError, VisualProcessAssistantService
from agent.services.visual_process_definition_service import VisualProcessDefinitionService
from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
from ananta_contracts.retrieval import SourceRef
from ananta_contracts.visual_process_assistant import EditorContextEnvelope
from worker.core.model_provider import DeterministicMockModelProvider
from worker.retrieval.codecompass_channel_providers import JsonlSymbolProvider
from worker.retrieval.codecompass_retriever import CodeCompassRetriever
from worker.visual_process_assistant.handlers import (
    VisualProcessAssistantInferenceHandler,
    VisualProcessAssistantRetrievalHandler,
)


def _persist_graph(principal: ChatSessionPrincipal) -> VisualProcessGraph:
    graph = VisualProcessGraph(
        id=f"vpa-test-{uuid.uuid4().hex}",
        name="Assistant lifecycle",
        steps=[VisualProcessStep(id="step-1", label="Analyse", kind="analysis")],
    )
    graph = VisualProcessGraph.model_validate(bind_graph_owner(graph.model_dump(), principal))
    with Session(engine) as db:
        write = VisualProcessDefinitionService().create(db, graph)
        db.commit()
    return write.graph


def test_persistent_two_phase_hub_worker_lifecycle_uses_real_codecompass_port(tmp_path) -> None:
    source_id = os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_ID", "").strip()
    if not source_id:
        pytest.skip("authoritative_source_evidence_unavailable")
    principal = ChatSessionPrincipal.from_values("admin", "admin")
    graph = _persist_graph(principal)
    repository_revision = "a" * 64
    manifest_hash = "b" * 64
    catalog_hash = "c" * 64
    provenance = {
        "source_id": source_id,
        "source_version": repository_revision,
        "provider": "rag-helper",
    }
    digest = hashlib.sha256(json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    class _Authority:
        @staticmethod
        def resolve(**_kwargs):
            return ResolvedSourceCatalog(
                catalog_task_id="catalog-task-1",
                catalog_id="catalog-1",
                catalog_hash=catalog_hash,
                repository_revision=repository_revision,
                manifest_hash=manifest_hash,
                source_allowlist_version=catalog_hash,
                source_refs=(
                    SourceRef(
                        source_id=source_id,
                        source_version=repository_revision,
                        tenant_id=principal.tenant_id,
                        scope="repository",
                        provenance_digest=digest,
                    ),
                ),
            )

    service = VisualProcessAssistantService(
        retrieval_timeout_ms=5_000,
        model_timeout_ms=120_000,
        source_authority=_Authority(),  # type: ignore[arg-type]
    )
    details = tmp_path / "details.jsonl"
    details.write_text(
        json.dumps(
            {
                "id": "symbol-visual-process-graph",
                "kind": "python_class",
                "name": "VisualProcessGraph",
                "file": "agent/visual_process/models.py",
                "summary": "class VisualProcessGraph stores the workflow definition",
                "content_hash": "content-visual-process-graph",
                "source_id": source_id,
                "source_version": repository_revision,
                "tenant_id": principal.tenant_id,
                "scope": "repository",
                "manifest_hash": manifest_hash,
                "provenance": provenance,
                "line_start": 130,
                "line_end": 180,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    context = service.create_context(
        principal=principal,
        graph_id=graph.id,
        payload={
            "graph_id": graph.id,
            "location": {"target_kind": "node", "graph_id": graph.id, "entity_id": "step-1"},
            "editor_mode": "editor",
            "repository_revision": repository_revision,
            "codecompass_manifest_hash": manifest_hash,
            "source_allowlist_version": catalog_hash,
            "source_scope": "repository",
            "catalog_task_id": "catalog-task-1",
            "catalog_id": "catalog-1",
            "catalog_hash": catalog_hash,
        },
    )
    conversation = service.create_conversation(
        principal=principal,
        context_id=context["context_id"],
    )
    submitted = service.submit_question(
        principal=principal,
        conversation_id=conversation["conversation_id"],
        question="Was macht VisualProcessGraph?",
        client_request_id=f"client-{uuid.uuid4().hex}",
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )

    from agent.repository import task_repo

    retrieval_task = task_repo.get_by_id(submitted["retrieval_task_id"])
    retrieval_envelope = retrieval_task.worker_execution_context["visual_process_assistant_job"]
    assert retrieval_envelope["editor_query"]["schema"] == "codecompass.editor_query.v1"
    assert retrieval_envelope["editor_query"]["intent"] == "node_explanation"
    assert retrieval_envelope["editor_query"]["registry_version"] == graph.node_registry_version
    assert retrieval_envelope["editor_query"]["node_kind"] == "analysis"
    assert retrieval_envelope["retrieval_intent"] == "node_explanation"
    assert retrieval_envelope["question"] != "Was macht VisualProcessGraph?"
    assert "intent:node_explanation" in retrieval_envelope["question"]
    assert "user_language:Was macht VisualProcessGraph?" in retrieval_envelope["question"]
    assert retrieval_envelope["max_evidence_items"] == 12
    retriever = CodeCompassRetriever(
        scope="visual_process_assistant",
        channel_providers={"symbol": JsonlSymbolProvider(paths=[details])},
    )
    retrieval_result = VisualProcessAssistantRetrievalHandler(retriever=retriever).execute(retrieval_envelope)
    after_retrieval = service.accept_worker_result(
        task_id=retrieval_task.id,
        result=retrieval_result,
    )
    assert after_retrieval["status"] == "queued_inference"
    assert after_retrieval["prompt_context_id"] != after_retrieval["context_id"]

    source_excerpt = "class VisualProcessGraph stores the workflow definition"
    with Session(engine) as db:
        prompt_context = db.get(
            VisualProcessAssistantContextDB,
            after_retrieval["prompt_context_id"],
        )
        request_row = db.get(VisualProcessAssistantRequestDB, after_retrieval["request_id"])
        assert prompt_context is not None
        assert request_row is not None
        assert source_excerpt not in json.dumps(prompt_context.context_json)
        assert source_excerpt not in json.dumps(request_row.accepted_evidence_json)
        assert all(item.get("excerpt") is None for item in request_row.accepted_evidence_json)
    assert task_repo.get_by_id(retrieval_task.id).worker_execution_context == {"assistant_payload_purged": True}

    inference_task = task_repo.get_by_id(after_retrieval["inference_task_id"])
    inference_envelope = inference_task.worker_execution_context["visual_process_assistant_job"]
    assert source_excerpt in inference_envelope["prompt"]
    evidence = inference_envelope["approved_evidence"]
    assert all(item.get("excerpt") is None for item in evidence)
    model_response = {
        "contract_version": "ananta.visual_process.help_response.v1",
        "context_id": inference_envelope["context_id"],
        "prompt_version": inference_envelope["prompt_version"],
        "summary": "Der Schritt analysiert den Eingang.",
        "location": inference_envelope["location"],
        "explanation": "Die Aussage ist an den Quellbeleg gebunden.",
        "options": [],
        "warnings": [],
        "next_actions": ["Konfiguration prüfen"],
        "evidence": evidence,
        "claims": [
            {
                "claim_id": "claim-1",
                "text": "Der Schritt ist eine Analyse.",
                "evidence_refs": [evidence[0]["evidence_id"]],
                "verification_status": "verified",
            }
        ],
        "workflow_patch": None,
        "extensions": {},
    }
    inference_result = VisualProcessAssistantInferenceHandler(
        DeterministicMockModelProvider(responses=[json.dumps(model_response)])
    ).execute(inference_envelope)
    completed = service.accept_worker_result(
        task_id=inference_task.id,
        result=inference_result,
    )
    assert completed["status"] == "completed"
    assert completed["response"]["evidence"][0]["source_id"] == source_id
    assert task_repo.get_by_id(inference_task.id).worker_execution_context == {"assistant_payload_purged": True}

    resumed = service.get_conversation(
        principal=principal,
        conversation_id=conversation["conversation_id"],
    )
    assert resumed["requests"][0]["status"] == "completed"


def test_patch_conflict_refresh_preserves_history_and_binds_new_request_to_current_draft(monkeypatch) -> None:
    principal = ChatSessionPrincipal.from_values("admin", "admin")
    graph = _persist_graph(principal)
    service = VisualProcessAssistantService()
    context = service.create_context(
        principal=principal,
        graph_id=graph.id,
        payload={
            "graph_id": graph.id,
            "location": {"target_kind": "node", "graph_id": graph.id, "entity_id": "step-1"},
            "editor_mode": "editor",
            "repository_revision": "repo-current",
            "codecompass_manifest_hash": "manifest-current",
            "source_allowlist_version": "allowlist-current",
            "source_scope": "repository",
        },
    )
    conversation = service.create_conversation(principal=principal, context_id=context["context_id"])
    previous_id = f"vpa-req-{uuid.uuid4().hex}"
    with Session(engine) as db:
        db.add(
            VisualProcessAssistantRequestDB(
                id=previous_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject_id,
                conversation_id=conversation["conversation_id"],
                context_id=context["context_id"],
                prompt_version="visual-process-assistant.v1",
                client_request_id=f"client-{uuid.uuid4().hex}",
                idempotency_key_hash=uuid.uuid4().hex,
                request_fingerprint=uuid.uuid4().hex,
                question_text="Passe den aktuellen Node an.",
                question_hash=uuid.uuid4().hex,
                status="completed",
                # Worker responses are contract-validated before persistence;
                # this focused orchestration test only needs the presence bit.
                response_json={"workflow_patch": {"stored_and_prevalidated": True}},
            )
        )
        db.commit()

    current_draft = graph.model_copy(update={"name": "Current unsaved draft"})
    captured = {}

    def _submit_question(**kwargs):
        captured.update(kwargs)
        return {
            "request_id": "new-hub-request",
            "conversation_id": kwargs["conversation_id"],
            "context_id": "set-by-refresh",
            "status": "queued_retrieval",
        }

    monkeypatch.setattr(service, "submit_question", _submit_question)
    stale_draft = current_draft.model_copy(update={"base_graph_hash": "f" * 64})
    with pytest.raises(VisualProcessAssistantError, match="assistant_patch_draft_base_conflict"):
        service.refresh_patch_request(
            principal=principal,
            request_id=previous_id,
            payload={"draft_graph": stale_draft.model_dump(mode="json")},
            client_request_id="stale-refresh-client",
            idempotency_key="stale-refresh-idempotency",
            patch_enabled=True,
        )
    with Session(engine) as db:
        unchanged_conversation = db.get(VisualProcessAssistantConversationDB, conversation["conversation_id"])
        assert unchanged_conversation is not None
        assert unchanged_conversation.active_context_id == context["context_id"]

    refreshed = service.refresh_patch_request(
        principal=principal,
        request_id=previous_id,
        payload={"draft_graph": current_draft.model_dump(mode="json")},
        client_request_id="refresh-client",
        idempotency_key="refresh-idempotency",
        patch_enabled=True,
    )

    assert refreshed["request_id"] == "new-hub-request"
    assert refreshed["refresh_of_request_id"] == previous_id
    assert captured["question"] == "Passe den aktuellen Node an."
    assert captured["conversation_id"] == conversation["conversation_id"]
    with Session(engine) as db:
        old_request = db.get(VisualProcessAssistantRequestDB, previous_id)
        refreshed_context = db.get(VisualProcessAssistantContextDB, refreshed["refresh_context_id"])
        stored_conversation = db.get(VisualProcessAssistantConversationDB, conversation["conversation_id"])
        assert old_request is not None
        assert old_request.status == "completed"
        assert old_request.response_json == {"workflow_patch": {"stored_and_prevalidated": True}}
        assert refreshed_context is not None
        envelope = EditorContextEnvelope.model_validate(refreshed_context.context_json)
        assert envelope.draft_hash == current_draft.definition_hash()
        assert envelope.extensions["ananta.patch_refresh"]["refresh_of_request_id"] == previous_id
        assert stored_conversation is not None
        assert stored_conversation.active_context_id == refreshed_context.context_id
