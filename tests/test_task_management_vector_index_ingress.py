from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent.models import TaskCreateRequest, TaskUpdateRequest
from agent.services import task_management_service as task_management_module
from agent.services.knowledge_index_task_ingress_policy import (
    BOUND_KNOWLEDGE_INDEX_MUTATION_REASON,
    RESERVED_KNOWLEDGE_INDEX_TASK_INGRESS_REASON,
    KnowledgeIndexTaskMutationConflict,
)
from agent.services.task_admin_service import TaskAdminService
from agent.services.task_claim_service import TaskClaimService
from agent.services.task_management_service import TaskManagementService
from agent.services.task_orchestration_service import (
    TaskOrchestrationDependencies,
    TaskOrchestrationService,
)
from agent.services.vector_index_task_ingress_policy import (
    RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON,
)


class _RawTaskUpdate:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return dict(self._payload)


def _unexpected_dependency(*args: Any, **kwargs: Any) -> Any:
    del args, kwargs
    pytest.fail("reserved Vector-Index ingress reached a task mutation dependency")


def _bound_knowledge_index_task(task_id: str = "bound-index-task") -> dict:
    return {
        "id": task_id,
        "status": "assigned",
        "task_kind": "codecompass_index_build",
        "assigned_agent_url": "http://worker-a:5001",
        "worker_execution_context": {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
                "job_id": task_id,
            }
        },
    }


@pytest.mark.parametrize(
    ("source", "data", "reserved_field"),
    [
        (
            "knowledge_index",
            TaskCreateRequest(description="forged index task"),
            "source",
        ),
        (
            "api",
            TaskCreateRequest(task_kind="codecompass_index_build"),
            "task_kind",
        ),
        (
            "api",
            TaskCreateRequest(
                worker_execution_context={"knowledge_index_job": {}}
            ),
            "worker_execution_context.knowledge_index_job",
        ),
    ],
)
def test_create_task_rejects_reserved_knowledge_index_markers(
    monkeypatch,
    source,
    data,
    reserved_field,
):
    monkeypatch.setattr(
        task_management_module,
        "get_task_queue_service",
        _unexpected_dependency,
    )

    result = TaskManagementService().create_task(
        data=data,
        source=source,
        created_by="external-user",
    )

    assert result == {
        "error": RESERVED_KNOWLEDGE_INDEX_TASK_INGRESS_REASON,
        "code": 403,
        "data": {
            "reason_code": RESERVED_KNOWLEDGE_INDEX_TASK_INGRESS_REASON,
            "reserved_field": reserved_field,
        },
    }


@pytest.mark.parametrize(
    ("source", "data", "reserved_field"),
    [
        (
            "vector_index",
            TaskCreateRequest(description="forged Vector-Index task"),
            "source",
        ),
        (
            "ui",
            TaskCreateRequest(
                description="forged Vector-Index task",
                task_kind="vector_index_operation",
            ),
            "task_kind",
        ),
        (
            "ui",
            TaskCreateRequest(
                description="forged Vector-Index task",
                worker_execution_context={"vector_index_task": {}},
            ),
            "worker_execution_context.vector_index_task",
        ),
    ],
)
def test_create_task_rejects_reserved_vector_index_markers_before_mutation(
    monkeypatch,
    source,
    data,
    reserved_field,
):
    monkeypatch.setattr(task_management_module, "get_repository_registry", _unexpected_dependency)
    monkeypatch.setattr(task_management_module, "get_task_queue_service", _unexpected_dependency)

    result = TaskManagementService().create_task(
        data=data,
        source=source,
        created_by="external-user",
    )

    assert result == {
        "error": RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON,
        "code": 403,
        "data": {
            "reason_code": RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON,
            "reserved_field": reserved_field,
        },
    }


@pytest.mark.parametrize(
    ("payload", "reserved_field"),
    [
        ({"source": "vector_index"}, "source"),
        ({"task_kind": "vector_index_operation"}, "task_kind"),
        (
            {"worker_execution_context": {"vector_index_task": {}}},
            "worker_execution_context.vector_index_task",
        ),
    ],
)
def test_patch_task_rejects_incoming_reserved_vector_index_markers_before_lookup(
    monkeypatch,
    payload,
    reserved_field,
):
    monkeypatch.setattr(task_management_module, "get_local_task_status", _unexpected_dependency)

    result = TaskManagementService().patch_task(
        task_id="external-task",
        data=_RawTaskUpdate(payload),
    )

    assert result["error"] == RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON
    assert result["code"] == 403
    assert result["data"]["reserved_field"] == reserved_field


@pytest.mark.parametrize(
    ("existing_task", "reserved_field"),
    [
        ({"id": "internal-vector-task", "source": "vector_index"}, "source"),
        (
            {"id": "internal-vector-task", "task_kind": "vector_index_operation"},
            "task_kind",
        ),
        (
            {
                "id": "internal-vector-task",
                "worker_execution_context": {"vector_index_task": {}},
            },
            "worker_execution_context.vector_index_task",
        ),
    ],
)
def test_patch_task_rejects_generic_mutation_of_existing_vector_index_task(
    monkeypatch,
    existing_task,
    reserved_field,
):
    monkeypatch.setattr(
        task_management_module,
        "get_local_task_status",
        lambda task_id: {**existing_task, "id": task_id},
    )
    monkeypatch.setattr(task_management_module, "update_local_task_status", _unexpected_dependency)

    result = TaskManagementService().patch_task(
        task_id="internal-vector-task",
        data=TaskUpdateRequest(title="generic mutation attempt"),
    )

    assert result["error"] == RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON
    assert result["code"] == 403
    assert result["data"]["reserved_field"] == reserved_field


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "rename governed task"},
        {"status": "completed"},
        {"task_kind": "ordinary_task"},
        {"required_capabilities": []},
        {
            "worker_execution_context": {
                "knowledge_index_dispatch_receipt": {
                    "schema": "forged-receipt"
                }
            }
        },
    ],
)
def test_patch_task_rejects_all_generic_mutation_of_bound_knowledge_index_job(
    monkeypatch,
    payload,
):
    task_id = "bound-knowledge-index-task"
    monkeypatch.setattr(
        task_management_module,
        "get_local_task_status",
        lambda requested_id: {
            "id": requested_id,
            "status": "assigned",
            "task_kind": "codecompass_index_build",
            "worker_execution_context": {
                "knowledge_index_job": {
                    "schema": "ananta.knowledge_index_execution_job.v2",
                    "job_id": requested_id,
                }
            },
        },
    )
    monkeypatch.setattr(
        task_management_module,
        "update_local_task_status",
        _unexpected_dependency,
    )

    result = TaskManagementService().patch_task(
        task_id=task_id,
        data=_RawTaskUpdate(payload),
    )

    assert result == {
        "error": "knowledge_index_task_control_plane_mutation_forbidden",
        "code": 409,
        "data": {
            "reason_code": (
                "knowledge_index_task_control_plane_mutation_forbidden"
            ),
            "task_id": task_id,
            "action": "patch",
        },
    }


def test_bound_knowledge_index_assignment_mutations_are_hub_only(
    monkeypatch,
):
    task = _bound_knowledge_index_task()
    monkeypatch.setattr(
        task_management_module,
        "get_local_task_status",
        lambda _task_id: task,
    )
    monkeypatch.setattr(
        task_management_module,
        "update_local_task_status",
        _unexpected_dependency,
    )
    service = TaskManagementService()

    results = {
        "assign": service.assign_task(
            task_id=task["id"],
            data=SimpleNamespace(
                agent_url="http://worker-b:5002",
                token=None,
                task_kind=None,
                required_capabilities=[],
            ),
        ),
        "auto_assign": service.auto_assign_task(
            task_id=task["id"],
            payload={},
            agent_registry_service=SimpleNamespace(),
            worker_contract_service=SimpleNamespace(),
        ),
        "unassign": service.unassign_task(task_id=task["id"]),
    }

    for action, result in results.items():
        assert result["error"] == BOUND_KNOWLEDGE_INDEX_MUTATION_REASON
        assert result["code"] == 409
        assert result["data"] == {
            "reason_code": BOUND_KNOWLEDGE_INDEX_MUTATION_REASON,
            "task_id": task["id"],
            "action": action,
        }


def test_bound_knowledge_index_generic_claim_is_rejected_before_queue(
    monkeypatch,
):
    task = _bound_knowledge_index_task()
    monkeypatch.setattr(
        "agent.services.task_claim_service.get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: SimpleNamespace(
                    model_dump=lambda: task
                )
            )
        ),
    )
    queue = SimpleNamespace(claim_task=_unexpected_dependency)

    result = TaskClaimService().claim_task(
        task_id=task["id"],
        agent_url="http://worker-a:5001",
        requested_lease=120,
        idempotency_key="claim-bound-index",
        policy=SimpleNamespace(
            validate_lease_duration=_unexpected_dependency
        ),
        task_queue_service=queue,
    )

    assert result == {
        "error": BOUND_KNOWLEDGE_INDEX_MUTATION_REASON,
        "code": 409,
        "data": {
            "reason_code": BOUND_KNOWLEDGE_INDEX_MUTATION_REASON,
            "task_id": task["id"],
            "action": "claim",
        },
    }


def test_bound_knowledge_index_secondary_management_mutations_are_rejected(
    monkeypatch,
):
    task = _bound_knowledge_index_task()
    monkeypatch.setattr(
        task_management_module,
        "get_local_task_status",
        lambda _task_id: task,
    )
    service = TaskManagementService()

    results = {
        "proposal_approve": service.review_task_proposal(
            task_id=task["id"],
            action="approve",
            comment=None,
        ),
        "subtask_callback": service.subtask_callback(
            task_id=task["id"],
            payload={"id": "subtask-a", "status": "completed"},
        ),
        "create_followups": service.create_followups(
            task_id=task["id"],
            data=SimpleNamespace(items=[]),
        ),
    }

    for action, result in results.items():
        assert result["error"] == BOUND_KNOWLEDGE_INDEX_MUTATION_REASON
        assert result["data"]["action"] == action


def _bound_orchestration_service(task):
    dependencies = TaskOrchestrationDependencies(
        get_task_status=lambda _task_id: task,
        update_task_status=_unexpected_dependency,
        forward_task_to_worker=_unexpected_dependency,
        repository_registry=_unexpected_dependency,
        routing_advisor=_unexpected_dependency,
        context_policy_service=_unexpected_dependency,
        execution_tracking_service=_unexpected_dependency,
    )
    return TaskOrchestrationService(
        dependencies=dependencies,
        research_delegation_policy=SimpleNamespace(),
    )


def test_bound_knowledge_index_generic_delegation_and_completion_are_rejected():
    task = _bound_knowledge_index_task()
    service = _bound_orchestration_service(task)

    delegated = service.delegate_task(
        task_id=task["id"],
        data=SimpleNamespace(),
        worker_job_service=SimpleNamespace(),
        worker_contract_service=SimpleNamespace(),
        agent_registry_service=SimpleNamespace(),
        result_memory_service=SimpleNamespace(),
        verification_service=SimpleNamespace(),
    )
    completed = service.complete_task(
        task_id=task["id"],
        payload={"output": "forged"},
        verification_service=SimpleNamespace(),
        worker_job_service=SimpleNamespace(),
        result_memory_service=SimpleNamespace(),
    )

    assert delegated["data"]["action"] == "delegate_task"
    assert completed["data"]["action"] == "orchestration_complete"
    assert delegated["error"] == completed["error"] == (
        BOUND_KNOWLEDGE_INDEX_MUTATION_REASON
    )


def test_bound_knowledge_index_admin_intervention_and_archive_are_rejected(
    monkeypatch,
):
    task_payload = _bound_knowledge_index_task()
    task = SimpleNamespace(
        **task_payload,
        model_dump=lambda: dict(task_payload),
    )
    repositories = SimpleNamespace(
        task_repo=SimpleNamespace(get_by_id=lambda _task_id: task),
        archived_task_repo=SimpleNamespace(
            get_by_id=lambda _task_id: task
        ),
    )
    monkeypatch.setattr(
        "agent.services.task_admin_service.get_repository_registry",
        lambda: repositories,
    )
    service = TaskAdminService()

    ok, reason, data = service.intervene_task(
        task_id=task_payload["id"],
        action="cancel",
        actor="external-admin",
    )
    assert ok is False
    assert reason == BOUND_KNOWLEDGE_INDEX_MUTATION_REASON
    assert data["action"] == "cancel"
    assert data["http_status"] == 409

    with pytest.raises(
        KnowledgeIndexTaskMutationConflict,
        match=BOUND_KNOWLEDGE_INDEX_MUTATION_REASON,
    ):
        service.archive_task(task_id=task_payload["id"])
    with pytest.raises(
        KnowledgeIndexTaskMutationConflict,
        match=BOUND_KNOWLEDGE_INDEX_MUTATION_REASON,
    ):
        service.restore_task(task_id=task_payload["id"])
