from __future__ import annotations

import copy

import pytest

from agent.services.knowledge_index_forward_timeout import (
    resolve_knowledge_index_forward_budget_seconds,
    resolve_knowledge_index_forward_deadline,
)
from tests.knowledge_index_execution_test_support import (
    build_execution_task,
)


def test_deadline_comes_from_fully_bound_persisted_v2_job() -> None:
    task = build_execution_task(
        max_runtime_seconds=900,
        include_manifest=True,
    )

    deadline = resolve_knowledge_index_forward_deadline(
        task,
        dispatch_phase="execute",
        monotonic_clock=lambda: 100.0,
    )

    assert deadline is not None
    assert deadline.budget_seconds == 930
    assert deadline.expires_at_monotonic == 1_030.0
    assert deadline.connect_timeout_seconds == 5.0


def test_v2_budget_tampering_fails_full_contract_parsing() -> None:
    task = build_execution_task(max_runtime_seconds=900)
    tampered = copy.deepcopy(task)
    tampered["worker_execution_context"]["knowledge_index_job"][
        "resources"
    ]["max_runtime_seconds"] = 86_400

    with pytest.raises(
        ValueError,
        match="knowledge_index_execution_job_invalid",
    ):
        resolve_knowledge_index_forward_budget_seconds(
            tampered,
            dispatch_phase="execute",
        )


def test_v2_job_must_match_persisted_task_identity() -> None:
    task = build_execution_task()
    task["id"] = "different-task"

    with pytest.raises(
        ValueError,
        match="knowledge_index_execution_job_task_mismatch",
    ):
        resolve_knowledge_index_forward_budget_seconds(
            task,
            dispatch_phase="execute",
        )


def test_public_v1_job_does_not_enter_governed_deadline_path() -> None:
    task = {
        "id": "public-v1-job",
        "task_kind": "codecompass_index_build",
        "worker_execution_context": {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_job.v1",
                "resources": {"max_runtime_seconds": 86_400},
            }
        },
    }

    assert (
        resolve_knowledge_index_forward_deadline(
            task,
            dispatch_phase="execute",
            monotonic_clock=lambda: 100.0,
        )
        is None
    )


@pytest.mark.parametrize(
    "worker_context",
    [
        {},
        {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_job.unknown"
            }
        },
    ],
)
def test_execute_rejects_missing_or_unknown_binding_schema(
    worker_context,
) -> None:
    task = {
        "id": "codecompass-invalid-binding",
        "task_kind": "codecompass_index_build",
        "worker_execution_context": worker_context,
    }

    with pytest.raises(ValueError, match="knowledge_index_execution_binding"):
        resolve_knowledge_index_forward_deadline(
            task,
            dispatch_phase="execute",
        )
