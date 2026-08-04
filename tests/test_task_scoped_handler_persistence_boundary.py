from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services import _task_scoped_adapters as adapters


def _task(*, bound: bool) -> dict:
    worker_context = {}
    if bound:
        worker_context["knowledge_index_job"] = {
            "schema": "ananta.knowledge_index_execution_job.v2",
            "job_id": "job-a",
        }
    return {
        "id": "job-a",
        "worker_execution_context": worker_context,
    }


@pytest.mark.parametrize(
    ("role", "bound", "expected_persist_calls"),
    [
        ("worker", True, 0),
        ("worker", False, 1),
        ("hub", True, 1),
    ],
)
def test_handler_proposal_persistence_respects_hub_worker_boundary(
    monkeypatch,
    role,
    bound,
    expected_persist_calls,
) -> None:
    persist_calls: list[dict] = []

    class _Handler:
        @staticmethod
        def propose(**_kwargs):
            return {"status": "accepted", "reason": "ready"}

    registry = SimpleNamespace(
        resolve=lambda _task_kind: _Handler(),
        resolve_descriptor=lambda _task_kind: {
            "capabilities": ["retrieval", "index_write"]
        },
    )
    monkeypatch.setattr(adapters, "get_task_handler_registry", lambda: registry)
    monkeypatch.setattr(adapters.settings, "role", role)
    monkeypatch.setattr(
        adapters,
        "get_core_services",
        lambda: SimpleNamespace(
            task_execution_service=SimpleNamespace(
                persist_task_proposal_result=lambda **values: (
                    persist_calls.append(values)
                )
            )
        ),
    )

    response = adapters.try_handler_propose(
        tid="job-a",
        task=_task(bound=bound),
        task_kind="codecompass_index_build",
        request_data=SimpleNamespace(),
        base_prompt="build index",
        cli_runner=lambda **_kwargs: None,
        forwarder=lambda **_kwargs: None,
        tool_definitions_resolver=lambda **_kwargs: [],
        service=SimpleNamespace(),
        build_review_state=lambda *_args, **_kwargs: {},
    )

    assert response is not None
    assert response.data["status"] == "accepted"
    assert len(persist_calls) == expected_persist_calls
