from __future__ import annotations

import hashlib
from typing import Any

from agent.services.organization_category_research_callback_adapter import (
    OrganizationCategoryResearchCallbackAdapter,
    OrganizationCategoryResearchCallbackError,
)
import pytest


def _category_task() -> dict[str, Any]:
    return {
        "id": "task-1",
        "task_kind": "planning_research",
        "worker_execution_context": {
            "planning_result_callback": {
                "schema": "organization_planning_result_callback.v1",
                "method": "POST",
                "path_template": (
                    "/api/worker-results/tasks/{source_task_id}/assignments/"
                    "{assignment_id}/planning/category"
                ),
                "authorization": "worker_result_capability",
            },
            "planning_research_binding": {
                "artifact_hashes": {"prompt": "a" * 64}
            },
        },
    }


def test_generic_worker_callback_is_adapted_to_closed_category_result() -> None:
    calls: list[dict[str, Any]] = []
    raw_output = '{"project":"research"}'
    adapter = OrganizationCategoryResearchCallbackAdapter(
        task_reader=lambda _task_id: _category_task(),
        result_acceptor=lambda **kwargs: calls.append(kwargs)
        or {"status": "accepted"},
    )

    result = adapter.accept_if_applicable(
        source_task_id="task-1",
        payload={
            "id": "assignment-1",
            "status": "completed",
            "last_output": raw_output,
            "last_exit_code": 0,
        },
        capability_claims={
            "source_task_id": "task-1",
            "assignment_id": "assignment-1",
            "dispatch_lease_id": "lease-1",
            "worker_id": "worker-1",
        },
    )

    assert result == {"status": "accepted"}
    assert calls[0]["raw_output_digest"] == hashlib.sha256(
        raw_output.encode("utf-8")
    ).hexdigest()
    assert calls[0]["runtime_artifact_hashes"] == {"prompt": "a" * 64}
    assert calls[0]["idempotency_key"].startswith("category-result-")


def test_non_category_callback_remains_on_generic_path() -> None:
    task = _category_task()
    task["task_kind"] = "coding"
    adapter = OrganizationCategoryResearchCallbackAdapter(
        task_reader=lambda _task_id: task,
        result_acceptor=lambda **_kwargs: {"unexpected": True},
    )

    assert (
        adapter.accept_if_applicable(
            source_task_id="task-1",
            payload={"id": "assignment-1", "status": "completed"},
            capability_claims={},
        )
        is None
    )


def test_category_pipeline_error_is_preserved_for_fail_closed_http_mapping() -> None:
    adapter = OrganizationCategoryResearchCallbackAdapter(
        task_reader=lambda _task_id: _category_task(),
        result_acceptor=lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("category_dispatch_lease_invalid")
        ),
    )

    with pytest.raises(
        OrganizationCategoryResearchCallbackError,
        match="category_dispatch_lease_invalid",
    ):
        adapter.accept_if_applicable(
            source_task_id="task-1",
            payload={
                "id": "assignment-1",
                "status": "completed",
                "last_output": '{"project":"research"}',
                "last_exit_code": 0,
            },
            capability_claims={
                "source_task_id": "task-1",
                "assignment_id": "assignment-1",
                "dispatch_lease_id": "lease-1",
                "worker_id": "worker-1",
            },
        )
