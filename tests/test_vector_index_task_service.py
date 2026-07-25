from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.vector_index_task_service import (
    VectorIndexTaskService,
    VectorIndexTrustedScope,
)
from agent.services.vector_store_rollout_service import (
    InMemoryVectorStoreRolloutStore,
    VectorStoreRolloutService,
)


class _Repository:
    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    def get_by_id(self, task_id: str):
        return self.rows.get(task_id)

    def get_all(self):
        return list(self.rows.values())


class _Queue:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.calls: list[dict] = []

    def ingest_task(self, **kwargs):
        self.calls.append(kwargs)
        extra = dict(kwargs["extra_fields"])
        self.repository.rows[kwargs["task_id"]] = SimpleNamespace(
            id=kwargs["task_id"],
            status=kwargs["status"],
            worker_execution_context=extra["worker_execution_context"],
            verification_status={},
            model_dump=lambda: {
                "id": kwargs["task_id"],
                "status": kwargs["status"],
                "worker_execution_context": extra["worker_execution_context"],
                "verification_status": {},
            },
        )


def _fixture():
    repository = _Repository()
    queue = _Queue(repository)
    audits: list[tuple[str, dict]] = []
    status_calls: list[tuple[str, str, dict]] = []

    def update(task_id, status, **kwargs):
        status_calls.append((task_id, status, kwargs))
        row = repository.rows[task_id]
        row.status = status
        original_dump = row.model_dump
        row.model_dump = lambda: {
            **original_dump(),
            "status": status,
            "verification_status": kwargs.get("verification_status", {}),
        }

    rollout = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        audit=lambda *_args: None,
    )
    service = VectorIndexTaskService(
        task_queue=queue,
        task_repository=repository,
        rollout_service=rollout,
        status_updater=update,
        audit=lambda event, payload: audits.append((event, payload)),
        clock=lambda: 100.0,
    )
    return service, repository, queue, audits, status_calls


def _scope(workspace: str = "workspace-a") -> VectorIndexTrustedScope:
    return VectorIndexTrustedScope(
        workspace_id=workspace,
        repository_id="repo-a",
        profile_name="default",
        domain="codecompass",
    )


def _payload():
    return {
        "points": [
            {"point_id": "1", "vector": [1.0, 0.0], "payload": {"kind": "code"}}
        ]
    }


def test_hub_owns_queue_envelope_and_idempotent_retry_key() -> None:
    service, _repository, queue, audits, _status = _fixture()
    first = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    second = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )

    assert first["job_id"] == second["job_id"]
    assert len(queue.calls) == 1
    envelope = queue.calls[0]["extra_fields"]["worker_execution_context"][
        "vector_index_task"
    ]
    assert envelope["resolved_config"]["provider"] == "json"
    assert envelope["scope"]["workspace_id"] == "workspace-a"
    assert queue.calls[0]["extra_fields"]["task_kind"] == "vector_index_operation"
    assert all("points" not in payload for _, payload in audits)


def test_hub_serializes_mutations_per_scope_but_isolates_workspaces() -> None:
    service, _repository, queue, _audits, _status = _fixture()
    service.submit(
        operation="rebuild",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    with pytest.raises(RuntimeError, match="vector_index_task_conflict"):
        service.submit(
            operation="delete",
            trusted_scope=_scope(),
            idempotency_key="request-5678",
            payload={"point_ids": ["1"]},
            actor="admin-a",
        )

    other = service.submit(
        operation="delete",
        trusted_scope=_scope("workspace-b"),
        idempotency_key="request-5678",
        payload={"point_ids": ["1"]},
        actor="admin-a",
    )
    assert other["scope"]["workspace_id"] == "workspace-b"
    assert len(queue.calls) == 2


def test_cancel_and_retry_reuse_same_hub_task_and_idempotency_key() -> None:
    service, repository, _queue, _audits, status_calls = _fixture()
    created = service.submit(
        operation="index",
        trusted_scope=_scope(),
        idempotency_key="request-1234",
        payload=_payload(),
        actor="admin-a",
    )
    cancelled = service.cancel(job_id=created["job_id"], actor="admin-a")
    retried = service.retry(job_id=created["job_id"], actor="admin-a")

    assert cancelled["status"] == "cancelled"
    assert retried["status"] == "queued"
    assert retried["job_id"] == created["job_id"]
    assert retried["idempotency_key"] == "request-1234"
    assert [item[1] for item in status_calls] == ["cancelled", "todo"]
    assert repository.get_by_id(created["job_id"]) is not None


def test_search_and_plaintext_secrets_never_enter_mutation_queue() -> None:
    service, _repository, queue, _audits, _status = _fixture()
    with pytest.raises(ValueError, match="operation_invalid"):
        service.submit(
            operation="search",
            trusted_scope=_scope(),
            idempotency_key="request-1234",
            payload={},
            actor="admin-a",
        )
    with pytest.raises(ValueError, match="plaintext_secret"):
        service.submit(
            operation="index",
            trusted_scope=_scope(),
            idempotency_key="request-5678",
            payload={
                "points": [
                    {
                        "point_id": "1",
                        "vector": [1.0],
                        "payload": {"api_key": "secret"},
                    }
                ]
            },
            actor="admin-a",
        )
    assert queue.calls == []
