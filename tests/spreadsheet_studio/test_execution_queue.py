from __future__ import annotations

import copy
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

import agent.db_models  # noqa: F401 - registers SQLModel metadata
from agent.adapters.spreadsheet_queue_execution_adapter import QueueBoundSpreadsheetExecutionAdapter
from agent.repositories.spreadsheet_document_repository import SqlSpreadsheetDocumentRepository
from agent.repositories.spreadsheet_execution_queue_repository import SqlSpreadsheetExecutionQueueRepository
from agent.services.spreadsheet_execution_queue_ports import (
    SpreadsheetLeaseDecision,
    SpreadsheetWorkerJobBinding,
)
from agent.services.spreadsheet_execution_queue_service import SpreadsheetExecutionQueueService
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_store import SpreadsheetStoreConflict
from tests.spreadsheet_studio.helpers import proposal, snapshot


class FakeWorkerJobs:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.bound: list[dict] = []

    def create(self, **values):
        self.created.append(values)
        return SpreadsheetWorkerJobBinding(worker_job_id="worker-job-one")

    def bind_lease(self, **values) -> None:
        self.bound.append(values)


class FakeLeases:
    def __init__(self, *, status: str = "active") -> None:
        self.status = status
        self.requests: list[dict] = []

    def acquire(self, **values):
        self.requests.append(values)
        return SpreadsheetLeaseDecision(
            status=self.status,
            reason_code="slot_acquired" if self.status == "active" else "worker_queue_full",
            slot_lease_id="slot-one" if self.status != "rejected" else None,
            queue_position=None,
        )


def _services(tmp_path, *, lease_status: str = "active"):
    engine = create_engine(f"sqlite:///{tmp_path / 'execution-queue.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    documents = SqlSpreadsheetDocumentRepository(db_engine=engine)
    saga = SpreadsheetSagaService(
        documents,
        policy=SpreadsheetPolicy(enabled=True, mode="worker", automatic_promotion_enabled=True),
        executor=QueueBoundSpreadsheetExecutionAdapter(),
    )
    worker_jobs = FakeWorkerJobs()
    leases = FakeLeases(status=lease_status)
    queue = SqlSpreadsheetExecutionQueueRepository(db_engine=engine)
    service = SpreadsheetExecutionQueueService(
        saga=saga,
        queue=queue,
        worker_jobs=worker_jobs,
        leases=leases,
        worker_id="spreadsheet-worker",
    )
    document = saga.create_document(
        tenant_id="tenant-a",
        owner_id="owner-a",
        title="Budget",
        snapshot=snapshot(),
        document_id="document-a",
    )
    return engine, queue, service, worker_jobs, leases, document


def test_queue_service_projects_exact_assignment_to_worker_job_and_lease(tmp_path) -> None:
    _, _, service, worker_jobs, leases, document = _services(tmp_path)

    result = service.execute_proposal(
        tenant_id="tenant-a",
        principal_id="owner-a",
        proposal=proposal(document),
    )

    assert result["status"] == "leased"
    assert result["worker_job_id"] == "worker-job-one"
    assert result["slot_lease_id"] == "slot-one"
    assert result["human_intervention_required"] is False
    assert worker_jobs.created[0]["assignment_digest"] == result["assignment_digest"]
    assert leases.requests[0]["assignment_digest"] == result["assignment_digest"]
    replay = service.execute_proposal(
        tenant_id="tenant-a",
        principal_id="owner-a",
        proposal=proposal(document),
    )
    assert replay["replayed"] is True
    assert len(worker_jobs.created) == 1


def test_queue_rejects_mutated_replay_and_persisted_assignment_tampering(tmp_path) -> None:
    engine, queue, service, _, _, document = _services(tmp_path)
    payload = proposal(document)
    result = service.execute_proposal(tenant_id="tenant-a", principal_id="owner-a", proposal=payload)
    mutated = copy.deepcopy(payload)
    mutated["actions"][0]["value"] = 7
    with pytest.raises(SpreadsheetStoreConflict, match="execution_replay_conflict"):
        service.execute_proposal(tenant_id="tenant-a", principal_id="owner-a", proposal=mutated)

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE spreadsheet_execution_jobs SET assignment_json='{}' WHERE tenant_id='tenant-a'")
        )
    with pytest.raises(RuntimeError, match="assignment_integrity_failed"):
        queue.get(tenant_id="tenant-a", job_id=result["job_id"])


def test_queue_full_fails_automatically_without_worker_dispatch(tmp_path) -> None:
    _, queue, service, worker_jobs, _, document = _services(tmp_path, lease_status="rejected")
    result = service.execute_proposal(
        tenant_id="tenant-a",
        principal_id="owner-a",
        proposal=proposal(document),
    )
    assert result["status"] == "failed"
    assert result["human_intervention_required"] is False
    assert worker_jobs.bound[0]["status"] == "rejected"
    assert queue.get(tenant_id="tenant-a", job_id=result["job_id"])["status"] == "failed"


def test_spreadsheet_execution_queue_migration_upgrade_and_downgrade(monkeypatch) -> None:
    documents = importlib.import_module("migrations.versions.b9d1f3a5c7e0_add_spreadsheet_document_persistence")
    queue = importlib.import_module("migrations.versions.c0e2f4a6d8b1_add_spreadsheet_execution_queue")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(documents, "op", operations)
        monkeypatch.setattr(queue, "op", operations)
        documents.upgrade()
        queue.upgrade()
        assert "spreadsheet_execution_jobs" in inspect(connection).get_table_names()
        queue.downgrade()
        assert "spreadsheet_execution_jobs" not in inspect(connection).get_table_names()
        documents.downgrade()
