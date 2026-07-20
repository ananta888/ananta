from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import agent.services.semantic_server_compute_service as server_compute_module
from agent.db_models import AgentInfoDB
from agent.repositories.semantic_compute_schedule_repository import (
    SemanticComputeScheduleRepository,
)
from agent.repositories.semantic_contract_repository import (
    SemanticContractRepository,
    SemanticPrincipal,
)
from agent.repositories.semantic_lease_repository import SemanticLeaseRepository
from agent.services.semantic_compute_task_service import SemanticComputeTaskService
from agent.services.semantic_server_compute_service import (
    RegisteredSemanticServerWorkerDirectory,
    SemanticServerComputeError,
    SemanticServerComputeService,
)
from agent.services.semantic_task_lease_authority import HubSemanticTaskLeaseAuthority
from tests.semantic_compute_support import compute_contract


class _Queue:
    def __init__(self) -> None:
        self.calls = []

    def ingest_task(self, **kwargs) -> None:
        if any(item["task_id"] == kwargs["task_id"] for item in self.calls):
            return
        self.calls.append(kwargs)


class _AgentRepository:
    def __init__(self, workers: list[AgentInfoDB]) -> None:
        self._workers = list(workers)

    def get_all(self) -> list[AgentInfoDB]:
        return list(self._workers)


class _Inputs:
    def authorize(self, _principal, *, parent_task_id, input_refs):
        return parent_task_id == "parent-active" and list(input_refs) == ["artifact:owned"]


def _worker(
    url: str,
    *,
    capabilities: list[str],
    authorized_capabilities: list[str] | None = None,
) -> AgentInfoDB:
    return AgentInfoDB(
        url=url,
        name=url,
        role="worker",
        capabilities=capabilities,
        registration_validated=True,
        authorized_capabilities=authorized_capabilities or [],
        validation_errors=[],
        last_seen=1_000.0,
        status="online",
    )


def _service(
    monkeypatch: pytest.MonkeyPatch,
    workers: list[AgentInfoDB],
) -> tuple[
    SemanticServerComputeService,
    SemanticPrincipal,
    _Queue,
    dict[str, object],
    RegisteredSemanticServerWorkerDirectory,
]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    now = 1_000.0
    contracts = SemanticContractRepository(db_engine=engine, clock=lambda: now)
    owner = SemanticPrincipal("tenant-a", "owner-a")
    contracts.put_membership(
        owner,
        session_id="session-a",
        epoch=1,
        role="owner",
        permissions={"semantic_compute": True},
        expires_at=2_000,
    )
    payload = compute_contract(
        now_ms=1_000_000,
        security_mode="trusted_compute",
        trusted_compute_grant=True,
    )
    contracts.create(
        owner,
        contract_id=payload["contract_id"],
        request_digest="a" * 64,
        idempotency_key="create-trusted-contract",
        payload=payload,
        status="active",
    )
    leases = SemanticLeaseRepository(db_engine=engine, clock=lambda: now, clock_skew_seconds=0)
    queue = _Queue()
    task_service = SemanticComputeTaskService(
        contracts=contracts,
        leases=leases,
        queue=queue,
        clock_ms=lambda: 1_000_000,
        task_is_active=lambda task_id: task_id == "parent-active",
        lease_authority=HubSemanticTaskLeaseAuthority(
            b"semantic-compute-test-signing-key-32-bytes",
            clock_ms=lambda: 1_000_000,
        ),
    )
    directory = RegisteredSemanticServerWorkerDirectory(clock=lambda: now)
    monkeypatch.setattr(
        server_compute_module,
        "get_repository_registry",
        lambda: SimpleNamespace(agent_repo=_AgentRepository(workers)),
    )
    service = SemanticServerComputeService(
        contracts=contracts,
        leases=leases,
        receipts=SemanticComputeScheduleRepository(db_engine=engine, clock=lambda: now),
        tasks=task_service,
        workers=directory,
        inputs=_Inputs(),
        clock=lambda: now,
    )
    values = dict(
        parent_task_id="parent-active",
        contract_id=payload["contract_id"],
        session_id="session-a",
        epoch=1,
        expected_revision=1,
        task_type="visual_extract",
        audience="owner-a",
        input_refs=["artifact:owned"],
        sequence_start=0,
        sequence_end=3,
        deadline_epoch_ms=1_004_000,
        resource_budget={"cpu_ms": 500, "memory_bytes": 1_048_576, "artifact_bytes": 4_096},
        idempotency_key="delegate-server-once",
    )
    return service, owner, queue, values, directory


@pytest.mark.parametrize(
    ("capabilities", "authorized_capabilities"),
    [
        pytest.param(["semantic_compute"], [], id="generic-only"),
        pytest.param(
            ["semantic_compute", "semantic_compute.visual_validate"],
            [],
            id="wrong-task-capability",
        ),
        pytest.param(
            ["semantic_compute", "semantic_compute.visual_extract"],
            ["semantic_compute", "semantic_compute.visual_validate"],
            id="exact-capability-not-authorized",
        ),
    ],
)
def test_trusted_server_compute_rejects_worker_without_exact_task_capability_before_queue(
    monkeypatch: pytest.MonkeyPatch,
    capabilities: list[str],
    authorized_capabilities: list[str],
) -> None:
    service, owner, queue, values, _directory = _service(
        monkeypatch,
        [
            _worker(
                "http://semantic-worker:5000",
                capabilities=capabilities,
                authorized_capabilities=authorized_capabilities,
            )
        ],
    )

    with pytest.raises(SemanticServerComputeError) as caught:
        service.delegate(owner, **values)

    assert caught.value.reason_code == "trusted_worker_unavailable"
    assert caught.value.status_code == 503
    assert queue.calls == []


def test_trusted_server_compute_selects_exact_capability_deterministically_and_creates_one_child_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers = [
        _worker(
            "http://semantic-worker-b:5000",
            capabilities=["semantic_compute", "semantic_compute.visual_extract"],
            authorized_capabilities=[
                "semantic_compute",
                "semantic_compute.visual_extract",
            ],
        ),
        _worker(
            "http://semantic-worker-a:5000",
            capabilities=["semantic_compute.visual_extract"],
        ),
    ]
    service, owner, queue, values, directory = _service(monkeypatch, workers)

    directory_candidates = directory.candidates("visual_extract")
    assert [item.candidate_id for item in directory_candidates] == [
        "http://semantic-worker-a:5000",
        "http://semantic-worker-b:5000",
    ]

    first = service.delegate(owner, **values)
    replay = service.delegate(owner, **values)
    assert first.executor_id == "http://semantic-worker-a:5000"
    assert first.task["parent_task_id"] == "parent-active"
    assert first.task["artifact_publish_ref"].startswith("artifact-publish:semantic-output-")
    assert first.task["task_lease"]["executor_id"] == first.executor_id
    assert replay.idempotent_replay is True and replay.task == first.task
    assert len(queue.calls) == 1
    assert queue.calls[0]["extra_fields"]["assigned_agent_url"] == first.executor_id
    assert queue.calls[0]["extra_fields"]["required_capabilities"] == [
        "semantic_compute",
        "semantic_compute.visual_extract",
    ]
