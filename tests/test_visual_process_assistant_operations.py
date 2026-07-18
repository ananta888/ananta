from __future__ import annotations

import uuid

import pytest
from sqlmodel import Session

from agent.database import engine
from agent.services.chat_process_binding import bind_graph_owner
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.visual_process_assistant_service import (
    VisualProcessAssistantError,
    VisualProcessAssistantService,
)
from agent.services.visual_process_definition_service import VisualProcessDefinitionService
from agent.visual_process.models import VisualProcessGraph, VisualProcessStep


class _Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _principal() -> ChatSessionPrincipal:
    suffix = uuid.uuid4().hex
    return ChatSessionPrincipal.from_values(f"tenant-{suffix}", f"subject-{suffix}")


def _graph(principal: ChatSessionPrincipal) -> VisualProcessGraph:
    graph = VisualProcessGraph(
        id=f"vpa-ops-{uuid.uuid4().hex}",
        name="Assistant operational controls",
        steps=[VisualProcessStep(id="step-1", label="Analyse", kind="analysis")],
    )
    owned = VisualProcessGraph.model_validate(bind_graph_owner(graph.model_dump(), principal))
    with Session(engine) as db:
        result = VisualProcessDefinitionService().create(db, owned)
        db.commit()
    return result.graph


def _context_and_conversation(
    service: VisualProcessAssistantService,
    principal: ChatSessionPrincipal,
    graph: VisualProcessGraph,
) -> tuple[dict, dict]:
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
            "repository_revision": "revision-ops-1",
            "codecompass_manifest_hash": "manifest-ops-1",
            "source_allowlist_version": "allowlist-ops-1",
            "source_scope": "repository",
        },
    )
    conversation = service.create_conversation(
        principal=principal,
        context_id=context["context_id"],
    )
    return context, conversation


def _submit(
    service: VisualProcessAssistantService,
    principal: ChatSessionPrincipal,
    conversation_id: str,
    sequence: int,
) -> dict:
    return service.submit_question(
        principal=principal,
        conversation_id=conversation_id,
        question=f"Frage {sequence}",
        client_request_id=f"client-{sequence}-{uuid.uuid4().hex}",
        idempotency_key=f"idem-{sequence}-{uuid.uuid4().hex}",
    )


def test_assistant_limits_two_active_requests_per_conversation() -> None:
    principal = _principal()
    clock = _Clock(4_000_001.0)
    service = VisualProcessAssistantService(clock=clock)
    service._queue_retrieval = lambda _request, _context: None  # type: ignore[method-assign]
    _context, conversation = _context_and_conversation(service, principal, _graph(principal))

    assert _submit(service, principal, conversation["conversation_id"], 1)["status"] == "queued_retrieval"
    assert _submit(service, principal, conversation["conversation_id"], 2)["status"] == "queued_retrieval"
    with pytest.raises(VisualProcessAssistantError) as raised:
        _submit(service, principal, conversation["conversation_id"], 3)

    assert raised.value.status_code == 429
    assert raised.value.reason_code == "assistant_conversation_in_flight_limit"
    assert raised.value.retry_after == 1


def test_assistant_limits_principal_to_twenty_requests_per_minute() -> None:
    principal = _principal()
    clock = _Clock(4_100_001.0)
    service = VisualProcessAssistantService(clock=clock)
    service._queue_retrieval = lambda _request, _context: None  # type: ignore[method-assign]
    graph = _graph(principal)
    context, first = _context_and_conversation(service, principal, graph)
    conversations = [first]
    for _ in range(10):
        conversations.append(service.create_conversation(principal=principal, context_id=context["context_id"]))

    for sequence in range(20):
        conversation = conversations[sequence // 2]
        _submit(service, principal, conversation["conversation_id"], sequence)

    with pytest.raises(VisualProcessAssistantError) as raised:
        _submit(service, principal, conversations[10]["conversation_id"], 21)

    assert raised.value.status_code == 429
    assert raised.value.reason_code == "assistant_principal_rate_limit"
    assert raised.value.retry_after == 39


def test_assistant_timeout_is_persistent_and_cancels_hub_task(monkeypatch) -> None:
    principal = _principal()
    clock = _Clock(4_200_001.0)
    service = VisualProcessAssistantService(clock=clock, retrieval_timeout_ms=5_000)
    _context, conversation = _context_and_conversation(service, principal, _graph(principal))
    request = _submit(service, principal, conversation["conversation_id"], 1)
    cancelled: list[str] = []
    monkeypatch.setattr(service, "_cancel_task", cancelled.append)

    clock.value += 5.001
    service.reconcile_request(request_id=request["request_id"], principal=principal)
    restarted_service = VisualProcessAssistantService(clock=clock)
    persisted = restarted_service.get_request(
        principal=principal,
        request_id=request["request_id"],
        reconcile=False,
    )

    assert persisted["status"] == "timeout"
    assert persisted["error_code"] == "assistant_retrieval_timeout"
    assert cancelled == [request["retrieval_task_id"]]


def test_assistant_cancel_is_idempotent_and_propagates_to_hub_task(monkeypatch) -> None:
    principal = _principal()
    service = VisualProcessAssistantService(clock=_Clock(4_300_001.0))
    _context, conversation = _context_and_conversation(service, principal, _graph(principal))
    request = _submit(service, principal, conversation["conversation_id"], 1)
    cancelled: list[str] = []
    monkeypatch.setattr(service, "_cancel_task", cancelled.append)

    first = service.cancel_request(principal=principal, request_id=request["request_id"])
    second = service.cancel_request(principal=principal, request_id=request["request_id"])

    assert first["status"] == second["status"] == "cancelled"
    assert first["error_code"] == "assistant_cancelled_by_user"
    assert cancelled == [request["retrieval_task_id"]]


def test_assistant_context_rejects_browser_authorized_source_ids() -> None:
    principal = _principal()
    service = VisualProcessAssistantService()
    graph = _graph(principal)

    with pytest.raises(VisualProcessAssistantError) as raised:
        service.create_context(
            principal=principal,
            graph_id=graph.id,
            payload={
                "graph_id": graph.id,
                "location": {
                    "target_kind": "node",
                    "graph_id": graph.id,
                    "entity_id": "step-1",
                },
                "repository_revision": "revision-1",
                "codecompass_manifest_hash": "manifest-1",
                "source_allowlist_version": "allowlist-1",
                "source_scope": "repository",
                "source_refs": [],
            },
        )

    assert raised.value.status_code == 403
    assert raised.value.reason_code == "assistant_client_source_refs_forbidden"


def test_rejected_worker_result_fails_request_and_purges_task_payload() -> None:
    principal = _principal()
    service = VisualProcessAssistantService(clock=_Clock(4_400_001.0))
    _context, conversation = _context_and_conversation(service, principal, _graph(principal))
    request = _submit(service, principal, conversation["conversation_id"], 1)

    with pytest.raises(VisualProcessAssistantError, match="assistant_retrieval_result_schema_invalid"):
        service.accept_worker_result(
            task_id=request["retrieval_task_id"],
            result={"schema": "invalid", "status": "completed"},
        )

    persisted = service.get_request(
        principal=principal,
        request_id=request["request_id"],
        reconcile=False,
    )
    assert persisted["status"] == "failed"
    assert persisted["error_code"] == "assistant_retrieval_result_schema_invalid"

    from agent.repository import task_repo

    task = task_repo.get_by_id(request["retrieval_task_id"])
    assert task is not None
    assert task.status == "failed"
    assert task.worker_execution_context == {"assistant_payload_purged": True}
