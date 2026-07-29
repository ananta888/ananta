from __future__ import annotations

from typing import Any

import pytest

from agent.models import TaskCreateRequest, TaskUpdateRequest
from agent.services import task_management_service as task_management_module
from agent.services.task_management_service import TaskManagementService
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
