from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from flask import Flask, g

from agent.models import TaskCreateRequest
from agent.routes import codecompass_retrieve as routes
from agent.services import task_management_service as task_management_module
from agent.services.codecompass_sira_index_operation_service import (
    CodeCompassSiraIndexOperationService,
    SiraIndexOperationConflict,
)
from agent.services.sira_index_task_ingress_policy import (
    bound_sira_index_mutation_error,
    find_reserved_sira_index_marker,
)
from agent.services.task_management_service import TaskManagementService
from ananta_contracts.sira_index_operation import (
    CONTEXT_KEY,
    TASK_KIND,
    SiraIndexOperation,
)
from worker.retrieval.sira.index_operation_handler import (
    LocalSiraIndexOperationRuntime,
    SiraIndexOperationTaskHandler,
    UnavailableSiraIndexOperationRuntime,
)


def _command(**overrides: str) -> SiraIndexOperation:
    values = {
        "operation": "sync",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "repository_id": "repository-a",
        "snapshot_artifact_id": "snapshot-0001",
        "idempotency_key": "sync-request-0001",
        **overrides,
    }
    return SiraIndexOperation.create(**values)


def _task(command: SiraIndexOperation, *, status: str = "todo") -> dict:
    return {
        "id": command.operation_id,
        "status": status,
        "tenant_id": command.tenant_id,
        "project_id": command.project_id,
        "task_kind": TASK_KIND,
        "worker_execution_context": {CONTEXT_KEY: command.to_dict()},
    }


def test_sira_operation_contract_is_strict_bound_and_path_free():
    command = _command()
    assert SiraIndexOperation.from_mapping(command.to_dict()) == command

    tampered = {**command.to_dict(), "repository_id": "repository-b"}
    with pytest.raises(ValueError, match="request_digest_mismatch"):
        SiraIndexOperation.from_mapping(tampered)
    with pytest.raises(ValueError, match="unknown_fields"):
        SiraIndexOperation.from_mapping({**command.to_dict(), "db_path": "/tmp/index"})
    with pytest.raises(ValueError, match="snapshot_artifact_id_forbidden"):
        _command(operation="compact")
    with pytest.raises(ValueError, match="snapshot_artifact_id_invalid"):
        _command(snapshot_artifact_id="../snapshot")


class _TaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}

    def get_by_id(self, task_id: str):
        return self.tasks.get(task_id)


class _Queue:
    def __init__(self, repository: _TaskRepository) -> None:
        self.repository = repository
        self.calls: list[dict] = []

    def ingest_task(self, **kwargs):
        self.calls.append(kwargs)
        fields = dict(kwargs["extra_fields"])
        self.repository.tasks[kwargs["task_id"]] = {
            "id": kwargs["task_id"],
            "status": kwargs["status"],
            **fields,
        }


def test_hub_service_queues_once_and_returns_scoped_status():
    repository = _TaskRepository()
    queue = _Queue(repository)
    service = CodeCompassSiraIndexOperationService(
        task_repository=repository,
        task_queue=queue,
    )
    arguments = {
        "operation": "sync",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "repository_id": "repository-a",
        "snapshot_artifact_id": "snapshot-0001",
        "idempotency_key": "sync-request-0001",
        "actor_id": "automation",
    }

    created = service.submit(**arguments)
    replayed = service.submit(**arguments)

    assert created["status"] == "todo"
    assert created["replayed"] is False
    assert replayed["replayed"] is True
    assert len(queue.calls) == 1
    assert queue.calls[0]["extra_fields"]["task_kind"] == TASK_KIND
    assert (
        service.get(
            created["operation_id"],
            tenant_id="tenant-a",
            project_id="project-a",
        )
        is not None
    )
    assert (
        service.get(
            created["operation_id"],
            tenant_id="tenant-b",
            project_id="project-a",
        )
        is None
    )
    with pytest.raises(SiraIndexOperationConflict, match="idempotency_conflict"):
        service.submit(**{**arguments, "snapshot_artifact_id": "snapshot-0002"})


def test_generic_task_ingress_and_mutation_cannot_forge_sira_operation():
    command = _command()
    task = _task(command)
    assert find_reserved_sira_index_marker(task) == "task_kind"
    assert find_reserved_sira_index_marker({}, source="codecompass_sira") == "source"
    assert bound_sira_index_mutation_error(task, action="patch") == {
        "error": "sira_index_task_control_plane_mutation_forbidden",
        "code": 409,
        "data": {
            "reason_code": "sira_index_task_control_plane_mutation_forbidden",
            "task_id": command.operation_id,
            "action": "patch",
        },
    }


def test_central_task_management_rejects_forged_sira_task(monkeypatch):
    monkeypatch.setattr(
        task_management_module,
        "get_task_queue_service",
        lambda: pytest.fail("forged SIRA task reached the Hub queue"),
    )
    result = TaskManagementService().create_task(
        data=TaskCreateRequest(task_kind=TASK_KIND),
        source="api",
        created_by="external-user",
    )
    assert result == {
        "error": "sira_index_reserved_task_ingress_forbidden",
        "code": 403,
        "data": {
            "reason_code": "sira_index_reserved_task_ingress_forbidden",
            "reserved_field": "task_kind",
        },
    }


def test_worker_handler_executes_once_without_review_or_orchestration():
    command = _command()
    runtime = UnavailableSiraIndexOperationRuntime()
    handler = SiraIndexOperationTaskHandler(runtime)

    proposal = handler.propose(task=_task(command))
    result = handler.execute(task=_task(command))

    assert proposal["command"] is None
    assert proposal["safety_flags"] == {
        "worker_only": True,
        "worker_orchestration_forbidden": True,
        "human_approval_required": False,
    }
    assert result["status"] == "failed"
    assert result["reason_code"] == "sira_index_operation_runtime_unavailable"
    invalid = handler.execute(tid="invalid-task", task={"task_kind": TASK_KIND})
    assert invalid == {
        "schema": "ananta.sira-index-operation-result.v1",
        "operation_id": "invalid-task",
        "status": "failed",
        "reason_code": "sira_index_operation_object_required",
    }


def _artifact(record_id: str, document_hash: str) -> dict:
    artifact = {
        "schema": "codecompass.sira-enrichment.v1",
        "artifact_id": f"artifact-{record_id}-{document_hash}",
        "source_chunk_id": record_id,
        "source_document_hash": document_hash,
        "generated_terms": [],
    }
    artifact["artifact_digest"] = hashlib.sha256(
        json.dumps(
            artifact,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return artifact


def _snapshot(*, revision: str, documents: list[dict]) -> dict:
    return {
        "schema": "codecompass.sira-sync-snapshot.v1",
        "binding": {
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "repository_id": "repository-a",
            "repository_revision": revision,
            "source_manifest_hash": "b" * 64,
            "index_digest": "c" * 64,
            "statistics_digest": "d" * 64,
            "profile_version": "corpus-discriminative-lexical.v1",
            "profile_digest": "e" * 64,
        },
        "documents": documents,
        "enrichments": {item["record_id"]: _artifact(item["record_id"], item["document_hash"]) for item in documents},
    }


def test_local_runtime_syncs_only_changed_records_then_compacts(tmp_path):
    snapshot_root = tmp_path / "snapshots"
    layer_root = tmp_path / "layers"
    snapshot_root.mkdir()
    first = _command()
    (snapshot_root / "snapshot-0001.json").write_text(
        json.dumps(
            _snapshot(
                revision="r1",
                documents=[
                    {"record_id": "a", "document_hash": "hash-a1"},
                    {"record_id": "b", "document_hash": "hash-b1"},
                ],
            )
        ),
        encoding="utf-8",
    )
    runtime = LocalSiraIndexOperationRuntime(
        snapshot_root=snapshot_root,
        layer_root=layer_root,
    )
    first_result = runtime.execute(first)

    second = _command(
        snapshot_artifact_id="snapshot-0002",
        idempotency_key="sync-request-0002",
    )
    (snapshot_root / "snapshot-0002.json").write_text(
        json.dumps(
            _snapshot(
                revision="r2",
                documents=[
                    {"record_id": "a", "document_hash": "hash-a1"},
                    {"record_id": "b", "document_hash": "hash-b2"},
                ],
            )
        ),
        encoding="utf-8",
    )
    second_result = runtime.execute(second)
    compact = _command(
        operation="compact",
        snapshot_artifact_id="",
        idempotency_key="compact-request-0001",
    )
    compact_result = runtime.execute(compact)

    assert first_result["enriched_count"] == 2
    assert second_result["enriched_count"] == 1
    assert second_result["unchanged_count"] == 1
    assert second_result["delta_layer_count"] == 1
    assert compact_result["status"] == "completed"
    assert compact_result["delta_layer_count"] == 0


def test_operation_http_adapter_accepts_headless_trigger(monkeypatch):
    app = Flask(__name__)
    submitted: dict = {}

    class _Service:
        def submit(self, **kwargs):
            submitted.update(kwargs)
            return {
                "schema": "ananta.sira-index-operation-status.v1",
                "operation_id": "sira-operation-test",
                "status": "todo",
            }

    monkeypatch.setattr(routes, "authorize_route_request", lambda **kwargs: None)
    monkeypatch.setattr(
        "agent.services.codecompass_sira_index_operation_service.get_codecompass_sira_index_operation_service",
        lambda: _Service(),
    )
    with app.test_request_context(
        "/api/codecompass/sira/operations",
        method="POST",
        json={
            "operation": "sync",
            "repository_id": "repository-a",
            "snapshot_artifact_id": "snapshot-0001",
            "idempotency_key": "sync-request-0001",
        },
    ):
        g.source_control_principal = SimpleNamespace(
            tenant_id="tenant-a",
            project_id="project-a",
            subject_id="automation",
        )
        response, status = routes.create_codecompass_sira_operation()

    assert status == 202
    assert submitted["actor_id"] == "automation"
    assert submitted["tenant_id"] == "tenant-a"
    assert "approved" not in submitted
