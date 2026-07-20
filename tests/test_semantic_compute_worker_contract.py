from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from agent.repositories.semantic_contract_repository import SemanticPrincipal
from agent.services.semantic_compute_task_service import SemanticComputeTaskError, SemanticComputeTaskService
from agent.services.semantic_task_lease_authority import HubSemanticTaskLeaseAuthority
from ananta_contracts.semantic_compute import (
    SemanticComputeContractError,
    SemanticComputeWorkerResult,
    SemanticComputeWorkerTask,
)
from tests.semantic_compute_support import compute_contract
from worker.semantic_media.handler import (
    SemanticComputeWorkerError,
    SemanticComputeWorkerHandler,
    WorkerArtifact,
)

ROOT = Path(__file__).resolve().parents[1]
LEASE_SECRET = b"semantic-compute-worker-contract-test-key" * 2


class _ConsentAuthority:
    allowed = True

    def authorized(self, _context):
        return self.allowed


def _lease_authority(now_ms: int = 1_000_000) -> HubSemanticTaskLeaseAuthority:
    return HubSemanticTaskLeaseAuthority(LEASE_SECRET, clock_ms=lambda: now_ms)


def worker_task(**values) -> SemanticComputeWorkerTask:
    defaults = dict(
        task_id="task-a",
        parent_task_id="parent-a",
        contract_id="contract-a",
        contract_digest="a" * 64,
        lease_id="lease-a",
        fencing_token=1,
        session_id="session-a",
        epoch=1,
        task_type="visual_extract",
        audience="viewer-a",
        input_refs=("artifact:input-a",),
        deadline_epoch_ms=2_000_000,
        resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
        artifact_publish_ref="artifact-publish:target-a",
    )
    defaults.update(values)
    return SemanticComputeWorkerTask(**defaults)


def test_worker_contract_is_closed_and_forbids_orchestration() -> None:
    payload = worker_task().to_dict()
    schema = json.loads((ROOT / "schemas/worker/semantic_compute_task.v1.json").read_text())
    Draft202012Validator(schema).validate(payload)
    parsed = SemanticComputeWorkerTask.from_dict(payload)
    assert parsed.task_id == "task-a"
    payload["orchestration"]["may_create_tasks"] = True
    with pytest.raises(SemanticComputeContractError) as captured:
        SemanticComputeWorkerTask.from_dict(payload)
    assert captured.value.reason_code == "worker_orchestration_forbidden"


def test_worker_executes_one_task_and_publishes_only_while_authorized() -> None:
    class Guard:
        allowed = True

        def authorized(self, _task):
            return self.allowed

    class Executor:
        calls = 0

        def execute(self, task, cancelled):
            self.calls += 1
            assert not cancelled()
            return WorkerArtifact(b"result", {"duration_ms": 2.0})

    class Publisher:
        calls = 0

        def publish(self, task, content):
            self.calls += 1
            assert task.artifact_publish_ref == "artifact-publish:target-a"
            return "artifact:result-a"

    guard, executor, publisher = Guard(), Executor(), Publisher()
    handler = SemanticComputeWorkerHandler(
        executor=executor, publisher=publisher, lease_guard=guard, clock_ms=lambda: 1_000_000
    )
    result = handler.handle(worker_task().to_dict())
    result_schema = json.loads((ROOT / "schemas/worker/semantic_compute_result.v1.json").read_text())
    Draft202012Validator(result_schema).validate(result)
    assert result["status"] == "completed"
    assert executor.calls == publisher.calls == 1

    guard.allowed = False
    with pytest.raises(SemanticComputeWorkerError, match="lease_not_authorized"):
        handler.handle(worker_task(task_id="task-b").to_dict())
    assert publisher.calls == 1


def test_cancellation_deadline_and_artifact_budget_block_publication() -> None:
    class Guard:
        def authorized(self, _task):
            return True

    class Publisher:
        calls = 0

        def publish(self, _task, _content):
            self.calls += 1
            return "artifact:result"

    class LargeExecutor:
        def execute(self, _task, _cancelled):
            return WorkerArtifact(b"x" * 2_000, {})

    publisher = Publisher()
    handler = SemanticComputeWorkerHandler(
        executor=LargeExecutor(), publisher=publisher, lease_guard=Guard(), clock_ms=lambda: 1_000_000
    )
    with pytest.raises(SemanticComputeWorkerError, match="artifact_budget_exceeded"):
        handler.handle(worker_task().to_dict())
    assert publisher.calls == 0
    cancelled = SemanticComputeWorkerHandler(
        executor=LargeExecutor(),
        publisher=publisher,
        lease_guard=Guard(),
        clock_ms=lambda: 1_000_000,
        cancelled=lambda _task: True,
    )
    with pytest.raises(SemanticComputeWorkerError, match="task_cancelled"):
        cancelled.handle(worker_task().to_dict())


class Queue:
    def __init__(self):
        self.calls = []

    def ingest_task(self, **kwargs):
        self.calls.append(kwargs)


class Contracts:
    def __init__(self, security_mode, grant):
        payload = compute_contract(security_mode=security_mode, trusted_compute_grant=grant)
        self.item = SimpleNamespace(
            id="semantic-contract-test",
            status="active",
            security_mode=security_mode,
            digest=payload["contract_digest"],
            session_id="session-a",
            epoch=1,
            room_id=payload.get("room_id"),
            expires_at=2_000.0,
            contract_payload=payload,
        )

    def get(self, _principal, _contract_id):
        return self.item


class Leases:
    def get(self, _id):
        return SimpleNamespace(fencing_token=1)

    def authorize_result(self, **_kwargs):
        return SimpleNamespace(
            id="lease-a",
            fencing_token=1,
            role="primary",
            executor_id="worker-a",
            contract_id="semantic-contract-test",
            contract_digest=self._digest,
            session_id="session-a",
            epoch=1,
            task_type="visual_extract",
            audience="viewer-a",
            sequence_start=0,
            sequence_end=9,
            resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
            issued_at=999.0,
            expires_at=1_011.0,
            deadline_at=1_011.0,
            tenant_id="tenant",
            owner_subject="owner",
        )

    _digest = compute_contract(security_mode="trusted_compute", trusted_compute_grant=True)["contract_digest"]


@pytest.mark.parametrize(
    ("security_mode", "grant", "reason"),
    [
        ("strict_e2ee", False, "strict_e2ee_server_compute_forbidden"),
        ("trusted_compute", False, "trusted_compute_grant_missing"),
    ],
)
def test_server_compute_gates_fail_before_queue_persistence(security_mode, grant, reason) -> None:
    queue = Queue()
    service = SemanticComputeTaskService(
        contracts=Contracts(security_mode, grant),  # type: ignore[arg-type]
        leases=Leases(),
        queue=queue,
        clock_ms=lambda: 1_000_000,  # type: ignore[arg-type]
        lease_authority=_lease_authority(),
        consent_authority=_ConsentAuthority(),
    )
    with pytest.raises(SemanticComputeTaskError, match=reason):
        service.create_server_task(
            SemanticPrincipal("tenant", "owner"),
            parent_task_id="parent-a",
            contract_id="semantic-contract-test",
            lease_id="lease-a",
            task_type="visual_extract",
            audience="viewer-a",
            input_refs=["artifact:input"],
            deadline_epoch_ms=1_010_000,
            resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
            artifact_publish_ref="artifact-publish:target",
        )
    assert queue.calls == []


def test_hub_binds_worker_parent_lease_budget_deadline_and_publish_target() -> None:
    queue = Queue()
    service = SemanticComputeTaskService(
        contracts=Contracts("trusted_compute", True),  # type: ignore[arg-type]
        leases=Leases(),
        queue=queue,
        clock_ms=lambda: 1_000_000,  # type: ignore[arg-type]
        task_is_active=lambda _task_id: True,
        lease_authority=_lease_authority(),
        consent_authority=_ConsentAuthority(),
    )
    task = service.create_server_task(
        SemanticPrincipal("tenant", "owner"),
        parent_task_id="parent-a",
        contract_id="semantic-contract-test",
        lease_id="lease-a",
        task_type="visual_extract",
        audience="viewer-a",
        input_refs=["artifact:input"],
        deadline_epoch_ms=1_010_000,
        resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
        artifact_publish_ref="artifact-publish:target",
    )
    assert task.parent_task_id == "parent-a"
    assert task.artifact_publish_ref == "artifact-publish:target"
    assert queue.calls[0]["extra_fields"]["assigned_agent_url"] == "worker-a"
    assert queue.calls[0]["extra_fields"]["required_capabilities"] == [
        "semantic_compute",
        "semantic_compute.visual_extract",
    ]
    assert queue.calls[0]["extra_fields"]["worker_execution_context"]["semantic_compute"] == task.to_dict()


def test_hub_rejects_late_cancelled_or_fencing_mismatched_results() -> None:
    contracts = Contracts("trusted_compute", True)
    lease_row = SimpleNamespace(
        id="lease-a",
        contract_id=contracts.item.id,
        contract_digest=contracts.item.digest,
        session_id="session-a",
        epoch=1,
        task_type="visual_extract",
        role="primary",
        executor_id="worker-a",
        audience="viewer-a",
        sequence_start=0,
        sequence_end=9,
        fencing_token=1,
        resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
        issued_at=999.0,
        expires_at=1_001.0,
        deadline_at=1_001.0,
        tenant_id="tenant",
        owner_subject="owner",
    )
    authority = _lease_authority(1_000_600)
    delegated = worker_task(
        contract_id=contracts.item.id,
        contract_digest=contracts.item.digest,
        deadline_epoch_ms=1_001_000,
        task_lease=authority.issue(lease_row, room_id=contracts.item.room_id),
    )
    result = SemanticComputeWorkerResult(
        task_id=delegated.task_id,
        contract_id=delegated.contract_id,
        contract_digest=delegated.contract_digest,
        lease_id=delegated.lease_id,
        fencing_token=delegated.fencing_token,
        session_id=delegated.session_id,
        epoch=delegated.epoch,
        task_type=delegated.task_type,
        audience=delegated.audience,
        status="completed",
        result_digest="b" * 64,
        artifact_refs=("artifact:result",),
        completed_at_ms=1_000_500,
    )
    lease_store = Leases()
    lease_store.authorize_result = lambda **_kwargs: lease_row  # type: ignore[method-assign]
    service = SemanticComputeTaskService(
        contracts=contracts,  # type: ignore[arg-type]
        leases=lease_store,
        queue=Queue(),
        clock_ms=lambda: 1_000_600,  # type: ignore[arg-type]
        task_is_active=lambda _task: True,
        task_binding_lookup=lambda _task: delegated.to_dict(),
        lease_authority=authority,
        consent_authority=_ConsentAuthority(),
    )
    assert service.authorize_result(result.to_dict()).task_id == delegated.task_id
    mismatched = result.to_dict()
    mismatched["fencing_token"] = 2
    with pytest.raises(SemanticComputeTaskError, match="result_task_binding_mismatch"):
        service.authorize_result(mismatched)
    cancelled = SemanticComputeTaskService(
        contracts=contracts,  # type: ignore[arg-type]
        leases=lease_store,
        queue=Queue(),
        clock_ms=lambda: 1_000_600,  # type: ignore[arg-type]
        task_is_active=lambda _task: False,
        task_binding_lookup=lambda _task: delegated.to_dict(),
        lease_authority=authority,
        consent_authority=_ConsentAuthority(),
    )
    with pytest.raises(SemanticComputeTaskError, match="task_cancelled_or_terminal"):
        cancelled.authorize_result(result.to_dict())
