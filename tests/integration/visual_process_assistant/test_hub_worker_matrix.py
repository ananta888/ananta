from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from agent.database import engine
from agent.db_models.visual_process_assistant import VisualProcessAssistantRequestDB
from agent.repository import task_repo
from agent.services.chat_process_binding import bind_graph_owner
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.source_catalog_service import (
    calculate_source_catalog_hash,
    calculate_source_catalog_id,
)
from agent.services.task_queue_service import get_task_queue_service
from agent.services.task_runtime_service import update_local_task_status
from agent.services.visual_process_assistant_service import (
    VisualProcessAssistantError,
    VisualProcessAssistantService,
)
from agent.services.visual_process_definition_service import VisualProcessDefinitionService
from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
from ananta_contracts.retrieval import SourceRef
from worker.core.model_provider import ModelProviderResult
from worker.retrieval.codecompass_channel_providers import JsonlSymbolProvider
from worker.retrieval.codecompass_retriever import CodeCompassRetriever
from worker.visual_process_assistant.handlers import (
    VisualProcessAssistantInferenceHandler,
    VisualProcessAssistantRetrievalHandler,
)

pytestmark = pytest.mark.integration

REPOSITORY_REVISION = "a" * 64
MANIFEST_HASH = "b" * 64
EMPTY_ALLOWLIST_VERSION = "c" * 64


class _Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class _DeterministicLocalModelProvider:
    """Deterministic seam at the Worker-local inference boundary only."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def complete(self, *, prompt: str, prompt_template_version: str) -> ModelProviderResult:
        assert prompt
        return ModelProviderResult(
            text=self._response_text,
            metadata={
                "provider": "local-integration",
                "model": "deterministic-contract-model",
                "base_url_label": "local://integration-boundary",
                "timeout_seconds": 1,
                "prompt_template_version": prompt_template_version,
                "llm_used": True,
            },
        )


def _persist_graph(principal: ChatSessionPrincipal) -> VisualProcessGraph:
    graph = VisualProcessGraph(
        id=f"vpa-matrix-{uuid.uuid4().hex}",
        name="Hub worker integration matrix",
        steps=[VisualProcessStep(id="step-1", label="Analyse", kind="analysis")],
    )
    graph = VisualProcessGraph.model_validate(bind_graph_owner(graph.model_dump(), principal))
    with Session(engine) as db:
        result = VisualProcessDefinitionService().create(db, graph)
        db.commit()
    return result.graph


def _external_authorized_source_id() -> str:
    singular = str(os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_ID") or "").strip()
    plural = str(os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_IDS") or "")
    candidates = [singular] if singular else [item.strip() for item in plural.split(",") if item.strip()]
    if not candidates:
        pytest.skip("authoritative_source_evidence_unavailable")
    # The contract validates the externally supplied identity.  The test never
    # substitutes a generated identifier when authority is unavailable.
    SourceRef(
        source_id=candidates[0],
        source_version=REPOSITORY_REVISION,
        tenant_id="authority-validation",
        scope="repository",
        provenance_digest="d" * 64,
    )
    return candidates[0]


def _persist_source_catalog(
    *,
    principal: ChatSessionPrincipal,
    source_id: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    catalog_task_id = f"catalog-task-{uuid.uuid4().hex}"
    provenance = {
        "source_id": source_id,
        "source_version": REPOSITORY_REVISION,
        "tenant_id": principal.tenant_id,
        "scope": "repository",
        "provider": "codecompass-symbol-index",
    }
    provenance_digest = hashlib.sha256(
        json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    source = {
        "source_ref": {
            "schema": "ananta.source_ref.v2",
            "source_id": source_id,
            "source_version": REPOSITORY_REVISION,
            "tenant_id": principal.tenant_id,
            "scope": "repository",
            "provenance_digest": provenance_digest,
        },
        "source_id": source_id,
        "source_version": REPOSITORY_REVISION,
        "tenant_id": principal.tenant_id,
        "scope": "repository",
        "provenance_digest": provenance_digest,
        "source_type": "repo_file",
        "path": "agent/visual_process/models.py",
        "record_id": "visual-process-graph",
        "line_start": 1,
        "line_end": 40,
        "content_hash": hashlib.sha256(b"VisualProcessGraph").hexdigest(),
        "manifest_hash": MANIFEST_HASH,
        "sensitivity": "internal",
        "allowed_for_llm_scope": True,
        "task_id": catalog_task_id,
    }
    trace_id = f"trace-{uuid.uuid4().hex}"
    context_hash = hashlib.sha256(b"vpa-matrix-context").hexdigest()
    catalog_hash = calculate_source_catalog_hash(
        {
            "task_id": catalog_task_id,
            "retrieval_trace_id": trace_id,
            "retrieval_context_hash": context_hash,
            "retrieval_manifest_hash": MANIFEST_HASH,
            "sources": [source],
            "rejected_candidates": [],
        }
    )
    catalog_id = calculate_source_catalog_id(catalog_hash)
    catalog = {
        "schema": "source_catalog.v2",
        "source_catalog_id": catalog_id,
        "source_catalog_hash": catalog_hash,
        "catalog_state": "current",
        "source_count": 1,
        "rejected_count": 0,
        "retrieval_trace_id": trace_id,
        "retrieval_context_hash": context_hash,
        "retrieval_manifest_hash": MANIFEST_HASH,
        "sources": [source],
    }
    get_task_queue_service().ingest_task(
        task_id=catalog_task_id,
        status="todo",
        title="Persisted source authority for the integration gate",
        created_by=principal.subject_id,
        source="visual_process",
        event_type="task_ingested",
        event_channel="hub_task_queue",
        extra_fields={
            "task_kind": "codecompass_fts_search",
            "tenant_id": principal.tenant_id,
        },
    )
    update_local_task_status(
        catalog_task_id,
        "completed",
        force=True,
        verification_status={"source_catalog": catalog},
        event_type="source_catalog_published",
        event_actor="ananta-hub",
    )
    return (
        {
            "catalog_task_id": catalog_task_id,
            "catalog_id": catalog_id,
            "catalog_hash": catalog_hash,
            "source_allowlist_version": catalog_hash,
        },
        {
            "id": "visual-process-graph",
            "kind": "python_class",
            "name": "VisualProcessGraph",
            "file": "agent/visual_process/models.py",
            "summary": "VisualProcessGraph stores one workflow definition.",
            "content_hash": source["content_hash"],
            "source_id": source_id,
            "source_version": REPOSITORY_REVISION,
            "tenant_id": principal.tenant_id,
            "scope": "repository",
            "manifest_hash": MANIFEST_HASH,
            "provenance": provenance,
            "line_start": 1,
            "line_end": 40,
        },
    )


def _start_request(
    *,
    service: VisualProcessAssistantService,
    principal: ChatSessionPrincipal,
    graph: VisualProcessGraph,
    catalog: dict[str, str] | None = None,
) -> dict[str, Any]:
    context = service.create_context(
        principal=principal,
        graph_id=graph.id,
        payload={
            "graph_id": graph.id,
            "location": {
                "target_kind": "node",
                "graph_id": graph.id,
                "entity_id": "step-1",
            },
            "editor_mode": "editor",
            "repository_revision": REPOSITORY_REVISION,
            "codecompass_manifest_hash": MANIFEST_HASH,
            "source_allowlist_version": (catalog["source_allowlist_version"] if catalog else EMPTY_ALLOWLIST_VERSION),
            "source_scope": "repository",
            **(catalog or {}),
        },
    )
    conversation = service.create_conversation(
        principal=principal,
        context_id=context["context_id"],
    )
    return service.submit_question(
        principal=principal,
        conversation_id=conversation["conversation_id"],
        question="Erkläre VisualProcessGraph im gewählten Analyseschritt.",
        client_request_id=f"client-{uuid.uuid4().hex}",
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )


def _write_symbol_index(path: Path, record: dict[str, Any] | None) -> None:
    path.write_text(
        "" if record is None else json.dumps(record, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _rehash_envelope(envelope: dict[str, Any]) -> None:
    canonical = dict(envelope)
    canonical.pop("envelope_hash", None)
    envelope["envelope_hash"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _retrieval_result(
    *,
    submitted: dict[str, Any],
    index_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    task = task_repo.get_by_id(str(submitted["retrieval_task_id"]))
    assert task is not None
    task_payload = task.model_dump()
    handler = VisualProcessAssistantRetrievalHandler(
        retriever=CodeCompassRetriever(
            scope="visual_process_assistant",
            channel_providers={"symbol": JsonlSymbolProvider(paths=[index_path])},
        )
    )

    replay = copy.deepcopy(task_payload)
    replay["id"] = f"replayed-{task.id}"
    with pytest.raises(ValueError, match="assistant_worker_task_binding_mismatch"):
        handler.propose(task=replay)

    wrong_handler = copy.deepcopy(task_payload)
    wrong_handler["task_kind"] = "visual_process_assistant_inference"
    with pytest.raises(ValueError, match="assistant_worker_task_kind_mismatch"):
        handler.propose(task=wrong_handler)

    unauthenticated = copy.deepcopy(task_payload)
    unauthenticated_envelope = unauthenticated["worker_execution_context"]["visual_process_assistant_job"]
    unauthenticated_envelope["hub_authorization"]["transport"] = "direct_call"
    _rehash_envelope(unauthenticated_envelope)
    with pytest.raises(ValueError, match="assistant_worker_hub_authorization_required"):
        handler.propose(task=unauthenticated)

    tampered = copy.deepcopy(task_payload)
    tampered["worker_execution_context"]["visual_process_assistant_job"]["question"] += " tampered"
    with pytest.raises(ValueError, match="assistant_worker_envelope_hash_mismatch"):
        handler.propose(task=tampered)

    proposal = handler.propose(task=task_payload)
    return task_payload, dict(proposal["worker_result"])


def _valid_model_response(inference_envelope: dict[str, Any]) -> str:
    evidence = list(inference_envelope.get("approved_evidence") or [])
    claims = []
    if evidence:
        claims = [
            {
                "claim_id": "verified-claim",
                "text": "Der ausgewählte Schritt nutzt die Workflowdefinition.",
                "evidence_refs": [evidence[0]["evidence_id"]],
                "verification_status": "verified",
            }
        ]
    return json.dumps(
        {
            "contract_version": "ananta.visual_process.help_response.v1",
            "context_id": inference_envelope["context_id"],
            "prompt_version": inference_envelope["prompt_version"],
            "summary": "Der ausgewählte Schritt wird sicher erklärt.",
            "location": inference_envelope["location"],
            "explanation": "Es wurde keine Workflowmutation ausgeführt.",
            "options": [],
            "warnings": [] if evidence else ["Keine freigegebene Repository-Evidence verfügbar."],
            "next_actions": ["Kontext prüfen"],
            "evidence": evidence,
            "claims": claims,
            "workflow_patch": None,
            "extensions": {},
        },
        sort_keys=True,
    )


def _inference_result(
    *,
    request_state: dict[str, Any],
    model_output: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    task = task_repo.get_by_id(str(request_state["inference_task_id"]))
    assert task is not None
    task_payload = task.model_dump()
    envelope = task_payload["worker_execution_context"]["visual_process_assistant_job"]
    handler = VisualProcessAssistantInferenceHandler(
        _DeterministicLocalModelProvider(model_output if model_output is not None else _valid_model_response(envelope))
    )

    replay = copy.deepcopy(task_payload)
    replay["id"] = f"replayed-{task.id}"
    with pytest.raises(ValueError, match="assistant_worker_task_binding_mismatch"):
        handler.propose(task=replay)

    proposal = handler.propose(task=task_payload)
    return task_payload, dict(proposal["worker_result"])


def _assert_persisted_terminal_state(
    *,
    principal: ChatSessionPrincipal,
    request_id: str,
    expected_status: str,
    expected_error: str | None,
) -> dict[str, Any]:
    restarted_service = VisualProcessAssistantService()
    state = restarted_service.get_request(
        principal=principal,
        request_id=request_id,
        reconcile=False,
    )
    assert state["status"] == expected_status
    assert state["error_code"] == expected_error
    with Session(engine) as db:
        persisted = db.get(VisualProcessAssistantRequestDB, request_id)
        assert persisted is not None
        assert persisted.status == expected_status
        assert persisted.error_code == expected_error
    return state


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "no_results",
        "timeout",
        "cancellation",
        "worker_failure",
        "invalid_evidence",
        "invalid_model_output",
    ],
)
def test_persisted_hub_worker_outcome_matrix(
    scenario: str,
    tmp_path: Path,
) -> None:
    principal = ChatSessionPrincipal.from_values("admin", "admin")
    graph = _persist_graph(principal)
    clock = _Clock()
    service = VisualProcessAssistantService(
        clock=clock,
        retrieval_timeout_ms=25 if scenario == "timeout" else 5_000,
        model_timeout_ms=5_000,
    )
    catalog = None
    symbol_record = None
    authorized_source_id = None
    if scenario == "success":
        authorized_source_id = _external_authorized_source_id()
        catalog, symbol_record = _persist_source_catalog(
            principal=principal,
            source_id=authorized_source_id,
        )
    elif scenario == "invalid_evidence":
        # A real CodeCompass symbol record without authority identity is
        # rejected before content release.  No placeholder source ID is minted.
        symbol_record = {
            "id": "record-without-authority",
            "kind": "python_class",
            "name": "VisualProcessGraph",
            "file": "agent/visual_process/models.py",
            "summary": "This content has no authority binding.",
            "content_hash": hashlib.sha256(b"unbound-record").hexdigest(),
            "manifest_hash": MANIFEST_HASH,
        }

    submitted = _start_request(
        service=service,
        principal=principal,
        graph=graph,
        catalog=catalog,
    )
    request_id = str(submitted["request_id"])
    retrieval_task_id = str(submitted["retrieval_task_id"])

    if scenario == "timeout":
        clock.advance(1)
        timed_out = service.get_request(principal=principal, request_id=request_id)
        assert timed_out["status"] == "timeout"
        task = task_repo.get_by_id(retrieval_task_id)
        assert task is not None
        assert task.status == "cancelled"
        assert task.worker_execution_context == {"assistant_payload_purged": True}
        _assert_persisted_terminal_state(
            principal=principal,
            request_id=request_id,
            expected_status="timeout",
            expected_error="assistant_retrieval_timeout",
        )
        return

    if scenario == "cancellation":
        cancelled = service.cancel_request(principal=principal, request_id=request_id)
        assert cancelled["status"] == "cancelled"
        task = task_repo.get_by_id(retrieval_task_id)
        assert task is not None
        assert task.status == "cancelled"
        assert task.worker_execution_context == {"assistant_payload_purged": True}
        _assert_persisted_terminal_state(
            principal=principal,
            request_id=request_id,
            expected_status="cancelled",
            expected_error="assistant_cancelled_by_user",
        )
        return

    if scenario == "worker_failure":
        update_local_task_status(
            retrieval_task_id,
            "failed",
            force=True,
            status_reason_code="assistant_worker_unavailable",
            event_type="worker_execution_failed",
            event_actor="delegated-worker",
        )
        failed = service.get_request(principal=principal, request_id=request_id)
        assert failed["status"] == "failed"
        task = task_repo.get_by_id(retrieval_task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.worker_execution_context == {"assistant_payload_purged": True}
        _assert_persisted_terminal_state(
            principal=principal,
            request_id=request_id,
            expected_status="failed",
            expected_error="assistant_worker_unavailable",
        )
        return

    index_path = tmp_path / "symbols.jsonl"
    _write_symbol_index(index_path, symbol_record)
    retrieval_task, retrieval_result = _retrieval_result(
        submitted=submitted,
        index_path=index_path,
    )
    assert retrieval_task["task_kind"] == "visual_process_assistant_retrieval"
    assert retrieval_result["task_id"] == retrieval_task_id

    if scenario == "invalid_evidence":
        assert retrieval_result["evidence"] == []
        assert "source_id_missing" in retrieval_result["rejection_reasons"]
        malformed_result = {
            **retrieval_result,
            "consistency_state": "current",
            "evidence": [{}],
        }
        with pytest.raises(
            VisualProcessAssistantError,
            match="assistant_retrieval_evidence_invalid",
        ):
            service.accept_worker_result(
                task_id=retrieval_task_id,
                result=malformed_result,
            )
        task = task_repo.get_by_id(retrieval_task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.worker_execution_context == {"assistant_payload_purged": True}
        _assert_persisted_terminal_state(
            principal=principal,
            request_id=request_id,
            expected_status="failed",
            expected_error="assistant_retrieval_evidence_invalid",
        )
        return

    after_retrieval = service.accept_worker_result(
        task_id=retrieval_task_id,
        result=retrieval_result,
    )
    assert after_retrieval["status"] == "queued_inference"
    retrieval_task_after = task_repo.get_by_id(retrieval_task_id)
    assert retrieval_task_after is not None
    assert retrieval_task_after.status == "completed"
    assert retrieval_task_after.worker_execution_context == {"assistant_payload_purged": True}

    if scenario == "success":
        assert retrieval_result["consistency_state"] == "current"
        assert [item["source_id"] for item in retrieval_result["evidence"]] == [authorized_source_id]
        assert after_retrieval["error_code"] is None
    else:
        assert retrieval_result["consistency_state"] == "no_results"
        assert retrieval_result["evidence"] == []
        assert after_retrieval["error_code"] == "assistant_no_results"

    inference_task, inference_result = _inference_result(
        request_state=after_retrieval,
        model_output="not-json" if scenario == "invalid_model_output" else None,
    )
    assert inference_task["task_kind"] == "visual_process_assistant_inference"
    completed = service.accept_worker_result(
        task_id=str(after_retrieval["inference_task_id"]),
        result=inference_result,
    )
    assert completed["status"] == "completed"
    assert completed["response"]["workflow_patch"] is None
    inference_task_after = task_repo.get_by_id(str(after_retrieval["inference_task_id"]))
    assert inference_task_after is not None
    assert inference_task_after.status == "completed"
    assert inference_task_after.worker_execution_context == {"assistant_payload_purged": True}

    if scenario == "invalid_model_output":
        expected_error = "model_output_invalid"
        assert completed["response"]["evidence"] == []
        assert "model_output_invalid" in completed["response"]["warnings"]
    elif scenario == "no_results":
        expected_error = "assistant_no_results"
        assert completed["response"]["evidence"] == []
    else:
        expected_error = None
        assert completed["response"]["evidence"][0]["source_id"] == authorized_source_id

    persisted = _assert_persisted_terminal_state(
        principal=principal,
        request_id=request_id,
        expected_status="completed",
        expected_error=expected_error,
    )
    assert persisted["response"] == completed["response"]
