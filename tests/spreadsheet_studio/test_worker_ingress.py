from __future__ import annotations

import hashlib
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlmodel import SQLModel, create_engine

import agent.db_models  # noqa: F401 - registers SQLModel metadata
from agent.adapters.spreadsheet_mock_execution_adapter import DeterministicSpreadsheetMockExecutionAdapter
from agent.adapters.spreadsheet_queue_execution_adapter import QueueBoundSpreadsheetExecutionAdapter
from agent.repositories.spreadsheet_document_repository import SqlSpreadsheetDocumentRepository
from agent.repositories.spreadsheet_execution_queue_repository import SqlSpreadsheetExecutionQueueRepository
from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_execution_queue_ports import (
    SpreadsheetLeaseDecision,
    SpreadsheetWorkerJobBinding,
)
from agent.services.spreadsheet_execution_queue_service import SpreadsheetExecutionQueueService
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_store import SpreadsheetStoreConflict
from agent.services.spreadsheet_worker_capability_service import SpreadsheetWorkerCapabilityService
from agent.services.spreadsheet_worker_ingress_service import SpreadsheetWorkerIngressService
from ananta_contracts.spreadsheet_studio import WorkbookSnapshotV1, canonical_digest
from tests.spreadsheet_studio.helpers import proposal, snapshot


class FakeWorkerJobs:
    def create(self, **_values):
        return SpreadsheetWorkerJobBinding(worker_job_id="worker-job-one")

    def bind_lease(self, **_values) -> None:
        return None


class FakeScheduler:
    def acquire(self, **_values):
        return SpreadsheetLeaseDecision(
            status="active",
            reason_code="slot_acquired",
            slot_lease_id="slot-one",
        )


class FakeLeaseControl:
    def __init__(self, *, live: bool = True) -> None:
        self.live = live
        self.claimed = 0
        self.finished: list[str] = []

    def claim(self, _job) -> None:
        self.require_live(_job)
        self.claimed += 1

    def require_live(self, _job) -> None:
        if not self.live:
            raise RuntimeError("spreadsheet_worker_lease_inactive")

    def finish(self, _job, *, status: str) -> None:
        self.finished.append(status)
        self.live = False


def _environment(tmp_path, *, source_content: bytes | None = None, live: bool = True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{tmp_path / 'worker-ingress.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    documents = SqlSpreadsheetDocumentRepository(db_engine=engine)
    artifacts = SpreadsheetArtifactStore(tmp_path / "artifacts")
    parsed = WorkbookSnapshotV1.from_mapping(snapshot())
    source = None
    if source_content is not None:
        digest = hashlib.sha256(source_content).hexdigest()
        stored = artifacts.store(
            tenant_id="tenant-a",
            content=source_content,
            format="xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            expected_sha256=digest,
        )
        source = {
            "artifact_id": stored.artifact_id,
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "format": stored.format,
            "media_type": stored.media_type,
        }
    document = documents.create_document(
        "tenant-a",
        {
            "schema": "ananta.spreadsheet-document-version.v1",
            "document_id": "document-a",
            "owner_id": "owner-a",
            "title": "Budget",
            "snapshot": parsed.to_dict(),
            "snapshot_digest": parsed.digest,
            "state": "published",
            **({"source_artifact": source} if source is not None else {}),
        },
    )
    saga = SpreadsheetSagaService(
        documents,
        policy=SpreadsheetPolicy(enabled=True, mode="worker", automatic_promotion_enabled=True),
        executor=QueueBoundSpreadsheetExecutionAdapter(),
        artifact_store=artifacts,
    )
    queue = SqlSpreadsheetExecutionQueueRepository(db_engine=engine)
    submitter = SpreadsheetExecutionQueueService(
        saga=saga,
        queue=queue,
        worker_jobs=FakeWorkerJobs(),
        leases=FakeScheduler(),
        worker_id="spreadsheet-worker",
    )
    queued = submitter.execute_proposal(
        tenant_id="tenant-a",
        principal_id="owner-a",
        proposal=proposal(document),
    )
    lease_control = FakeLeaseControl(live=live)
    ingress = SpreadsheetWorkerIngressService(
        queue=queue,
        saga=saga,
        artifacts=artifacts,
        leases=lease_control,
        capabilities=SpreadsheetWorkerCapabilityService(signing_secret="test-signing-secret-at-least-32-bytes"),
    )
    return documents, queue, ingress, lease_control, queued, source_content


def test_claim_and_callback_are_capability_lease_and_digest_bound(tmp_path) -> None:
    documents, _, ingress, lease, queued, _ = _environment(tmp_path)
    assignment = ingress.claim(worker_id="spreadsheet-worker")
    assert assignment is not None
    assert assignment["job_id"] == queued["job_id"]
    assert "tenant_id" not in assignment and "principal_id" not in assignment
    assert assignment["human_intervention_required"] is False

    execution = dict(
        DeterministicSpreadsheetMockExecutionAdapter().dry_run(
            snapshot=assignment["snapshot"],
            actions=tuple(assignment["actions"]),
        )
    )
    body = {
        "status": "completed",
        "assignment_digest": assignment["assignment_digest"],
        "result": execution,
        "result_digest": canonical_digest(execution),
        "reason_code": None,
    }
    tampered = {**body, "result_digest": "0" * 64}
    with pytest.raises(ValueError, match="result_digest_invalid"):
        ingress.accept_result(job_id=assignment["job_id"], token=assignment["callback_token"], payload=tampered)

    completed = ingress.accept_result(
        job_id=assignment["job_id"],
        token=assignment["callback_token"],
        payload=body,
    )
    assert completed["status"] == "completed"
    assert completed["result"]["state"] == "promoted"
    assert documents.get_document("tenant-a", "document-a")["version"] == 2
    assert lease.finished == ["completed"]
    replay = ingress.accept_result(
        job_id=assignment["job_id"],
        token=assignment["callback_token"],
        payload=body,
    )
    assert replay["replayed"] is True


def test_source_handle_is_opaque_tenant_bound_and_consumed_once(tmp_path) -> None:
    content = b"immutable-source-workbook"
    _, _, ingress, _, _, _ = _environment(tmp_path, source_content=content)
    assignment = ingress.claim(worker_id="spreadsheet-worker")
    assert assignment is not None
    handle = assignment["source_artifact_handle"]
    assert "tenant-a" not in str(handle)

    downloaded, metadata = ingress.read_source_artifact(
        job_id=assignment["job_id"],
        token=handle["token"],
    )
    assert downloaded == content
    assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
    with pytest.raises(SpreadsheetStoreConflict, match="handle_consumed"):
        ingress.read_source_artifact(job_id=assignment["job_id"], token=handle["token"])
    with pytest.raises(ValueError, match="scope_invalid|binding_invalid"):
        ingress.accept_result(
            job_id=assignment["job_id"],
            token=handle["token"],
            payload={
                "status": "failed",
                "assignment_digest": assignment["assignment_digest"],
                "result": None,
                "result_digest": None,
                "reason_code": "spreadsheet_worker_execution_failed",
            },
        )


def test_stale_lease_and_worker_failure_terminalize_automatically(tmp_path) -> None:
    _, queue, stale_ingress, _, stale_job, _ = _environment(tmp_path / "stale", live=False)
    with pytest.raises(RuntimeError, match="lease_inactive"):
        stale_ingress.claim(worker_id="spreadsheet-worker")
    assert queue.get(tenant_id="tenant-a", job_id=stale_job["job_id"])["status"] == "failed"

    _, queue, ingress, lease, queued, _ = _environment(tmp_path / "failed")
    assignment = ingress.claim(worker_id="spreadsheet-worker")
    assert assignment is not None
    body = {
        "status": "failed",
        "assignment_digest": assignment["assignment_digest"],
        "result": None,
        "result_digest": None,
        "reason_code": "spreadsheet_worker_execution_failed",
    }
    failed = ingress.accept_result(
        job_id=assignment["job_id"],
        token=assignment["callback_token"],
        payload=body,
    )
    assert failed["status"] == "failed"
    assert lease.finished == ["failed"]
    assert queue.get(tenant_id="tenant-a", job_id=queued["job_id"])["status"] == "failed"


def test_worker_ingress_migration_adds_capability_bindings(monkeypatch) -> None:
    modules = [
        importlib.import_module("migrations.versions.b9d1f3a5c7e0_add_spreadsheet_document_persistence"),
        importlib.import_module("migrations.versions.c0e2f4a6d8b1_add_spreadsheet_execution_queue"),
        importlib.import_module("migrations.versions.d1f3a5b7c9e2_harden_spreadsheet_worker_ingress"),
    ]
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        for module in modules:
            monkeypatch.setattr(module, "op", operations)
            module.upgrade()
        columns = {column["name"] for column in inspect(connection).get_columns("spreadsheet_execution_jobs")}
        assert {
            "callback_jti",
            "artifact_handle_jti",
            "claimed_at",
            "artifact_consumed_at",
            "callback_payload_digest",
        } <= columns
        modules[2].downgrade()
        modules[1].downgrade()
        modules[0].downgrade()
