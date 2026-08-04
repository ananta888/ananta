from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from sqlalchemy import BigInteger
from sqlmodel import SQLModel, create_engine

from agent.db_models.knowledge_index_execution import (
    KnowledgeIndexExecutionBindingDB,
)
from agent.repositories.knowledge_index_execution_repository import (
    SQLKnowledgeIndexExecutionRepository,
)
from agent.services.knowledge_index_execution_binding_service import (
    CurrentKnowledgeIndexAuthority,
    KnowledgeIndexExecutionBindingError,
    KnowledgeIndexExecutionBindingService,
)
from agent.services.knowledge_index_job_service import KnowledgeIndexJobService
from agent.services.source_access_enforcement import (
    SourceAccessRequest,
    source_access_binding_digest,
)
from agent.services.source_access_manifest_signing import (
    HubSourceAccessManifestSigner,
    SourceAccessSigningKey,
    WorkerSourceAccessManifestVerifier,
)
from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexExecutionAssignment,
    KnowledgeIndexExecutionResult,
    KnowledgeIndexResourceBudget,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
)
from worker.retrieval.knowledge_index_job_handler import (
    KnowledgeIndexWorkerTaskHandler,
)


def test_execution_binding_epoch_millisecond_columns_use_bigint() -> None:
    epoch_columns = {
        column.name: column
        for column in KnowledgeIndexExecutionBindingDB.__table__.columns
        if column.name.endswith("_epoch_ms")
    }

    assert epoch_columns
    assert all(
        isinstance(column.type, BigInteger)
        for column in epoch_columns.values()
    )


class Authority:
    def __init__(self, current):
        self.current = current

    def resolve(self, **_kwargs):
        return self.current


def _authority():
    return CurrentKnowledgeIndexAuthority(
        tenant_id="tenant-alpha",
        project_id="project-atlas",
        source_revision_id=f"srev_{'a' * 64}",
        source_revision_digest="b" * 64,
        admission_digest="c" * 64,
        policy_snapshot_id="policy-snapshot-v7",
        policy_snapshot_digest="d" * 64,
        destination_id=f"dst_{'e' * 64}",
        destination_digest="f" * 64,
        source_access_grant_id=f"grant_{'1' * 64}",
        source_access_grant_digest="2" * 64,
    )


def _assignment(*, expires=220_000, generation=1):
    return KnowledgeIndexExecutionAssignment(
        assignment_id=f"assignment-{generation}",
        worker_id="worker-index-01",
        lease_id=f"lease-{generation}",
        lease_generation=generation,
        lease_issued_epoch_ms=9_000,
        lease_expires_epoch_ms=expires,
    )


def _service(tmp_path, authority=None, *, clock_ms=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'execution.sqlite3'}"
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[KnowledgeIndexExecutionBindingDB.__table__],
    )
    authority_port = Authority(authority or _authority())
    service = KnowledgeIndexExecutionBindingService(
        repository=SQLKnowledgeIndexExecutionRepository(engine),
        authority=authority_port,
        clock_ms=clock_ms or (lambda: 10_000),
    )
    return service, authority_port


def _issue(service):
    return service.issue(
        hub_task_id="hub-task-001",
        owner_id="owner-alice",
        idempotency_key_digest="3" * 64,
        authority=_authority(),
        files=[
            {
                "relative_path": "agent/main.py",
                "sha256": "4" * 64,
                "size_bytes": 128,
            }
        ],
        resources=KnowledgeIndexResourceBudget(
            max_files=10,
            max_total_bytes=1024,
            max_file_bytes=512,
            max_runtime_seconds=60,
            max_memory_bytes=128 * 1024 * 1024,
            max_output_bytes=4096,
        ),
        payload_artifact_ref={
            "artifact_id": "artifact-payload-001",
            "sha256": "5" * 64,
            "size_bytes": 256,
            "media_type": (
                "application/vnd.ananta.knowledge-index-job+json"
            ),
            "encoding": "json",
        },
        assignment=_assignment(),
        scope_id="source-alpha",
        source_scope="repository",
        profile_name="deep-code",
        created_by="owner-alice",
    )


def _result(record):
    return KnowledgeIndexExecutionResult.create(
        record.job,
        status="completed",
        reason_code=None,
        knowledge_index={"id": "knowledge-index-alpha"},
        run={"id": "knowledge-attempt-alpha"},
        artifact_refs=[],
    ).to_wire()


def _submit_bound(
    service: KnowledgeIndexJobService,
    *,
    assignment: KnowledgeIndexExecutionAssignment | None = None,
    idempotency_key: str = "bound-job-assignment",
    source_id: str = "source-alpha",
):
    authority = _authority()
    return service.submit_bound_source_revision_job(
        hub_task_id="hub-task-001",
        tenant_id=authority.tenant_id,
        project_id=authority.project_id,
        owner_id="owner-alice",
        source_revision_id=authority.source_revision_id,
        source_revision_digest=authority.source_revision_digest,
        admission_digest=authority.admission_digest,
        policy_snapshot_id=authority.policy_snapshot_id,
        policy_snapshot_digest=authority.policy_snapshot_digest,
        destination_id=authority.destination_id,
        destination_digest=authority.destination_digest,
        source_access_grant_id=authority.source_access_grant_id,
        source_access_grant_digest=authority.source_access_grant_digest,
        files=[
            {
                "relative_path": "agent/main.py",
                "sha256": "4" * 64,
                "size_bytes": 128,
            }
        ],
        resource_budget=KnowledgeIndexResourceBudget(
            max_files=10,
            max_total_bytes=1024,
            max_file_bytes=512,
            max_runtime_seconds=60,
            max_memory_bytes=128 * 1024 * 1024,
            max_output_bytes=4096,
        ).to_wire(),
        assignment=(assignment or _assignment()).to_wire(),
        idempotency_key=idempotency_key,
        source_scope="repository",
        source_id=source_id,
        records=[],
        created_by="owner-alice",
    )


class _BoundTasks:
    def __init__(self):
        self.items = {}

    def get_by_id(self, task_id):
        return self.items.get(task_id)


class _BoundQueue:
    def __init__(self, tasks, *, failures=0):
        self.tasks = tasks
        self.calls = []
        self.failures = failures

    def ingest_task(self, **values):
        self.calls.append(values)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("queue_temporarily_unavailable")
        self.tasks.items[values["task_id"]] = {
            "id": values["task_id"],
            "status": values["status"],
            "verification_status": {},
            **dict(values["extra_fields"]),
        }


class _BoundPayloads:
    def __init__(self):
        self.store_calls = 0

    def prepare_reference(self, *, content, fingerprint):
        return {
            "artifact_id": f"knowledge-index-payload-{fingerprint}",
            "sha256": fingerprint,
            "size_bytes": len(content),
            "media_type": (
                "application/vnd.ananta.knowledge-index-job+json"
            ),
            "encoding": "json",
        }

    def store_payload(self, *, content, fingerprint, created_by):
        del created_by
        self.store_calls += 1
        return self.prepare_reference(
            content=content,
            fingerprint=fingerprint,
        )


class _BoundWorkers:
    def __init__(self):
        self.calls = []

    def resolve_worker_url(self, worker_id):
        self.calls.append(worker_id)
        return "http://worker-index-01:5001"


def _bound_job_service(binding_service, *, queue_failures=0):
    tasks = _BoundTasks()
    queue = _BoundQueue(tasks, failures=queue_failures)
    payloads = _BoundPayloads()
    workers = _BoundWorkers()
    service = KnowledgeIndexJobService(
        task_queue=queue,
        task_repository=tasks,
        payload_store=payloads,
        execution_binding_service=binding_service,
        worker_directory=workers,
        allow_legacy_unresolved_destination=True,
        clock=lambda: 10.0,
    )
    return service, tasks, queue, payloads, workers


def test_hub_issues_idempotent_bound_job_and_finalizes_once(tmp_path) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)
    replay = _issue(service)
    running = service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=1,
    )
    delegated = service.validate_delegated_payload_access(
        assignment_id="assignment-1",
        lease_id="lease-1",
        authenticated_worker_id="worker-index-01",
    )
    completed = service.finalize_result(
        job_id=issued.job.job_id,
        payload=_result(issued),
        authenticated_worker_id="worker-index-01",
    )
    final_replay = service.finalize_result(
        job_id=issued.job.job_id,
        payload=_result(issued),
        authenticated_worker_id="worker-index-01",
    )

    assert replay.job == issued.job
    assert delegated.job == issued.job
    assert running.state == "running"
    assert completed.state == final_replay.state == "completed"
    assert completed.result_digest == final_replay.result_digest


def test_terminal_exact_result_replay_survives_lease_and_authority_rotation(
    tmp_path,
) -> None:
    now_ms = [10_000]
    service, authority_port = _service(
        tmp_path,
        clock_ms=lambda: now_ms[0],
    )
    issued = _issue(service)
    service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=1,
    )
    payload = _result(issued)
    completed = service.finalize_result(
        job_id=issued.job.job_id,
        payload=payload,
        authenticated_worker_id="worker-index-01",
    )

    now_ms[0] = 300_000
    authority_port.current = replace(
        _authority(),
        policy_snapshot_digest="9" * 64,
    )

    replay = service.finalize_result(
        job_id=issued.job.job_id,
        payload=payload,
        authenticated_worker_id="worker-index-01",
    )

    assert replay == completed
    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="knowledge_index_execution_lease_stale",
    ):
        service.finalize_result(
            job_id=issued.job.job_id,
            payload=payload,
            authenticated_worker_id="worker-other",
        )
    changed = dict(payload)
    changed["knowledge_index"] = {"id": "knowledge-index-other"}
    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="knowledge_index_execution_result_state_invalid",
    ):
        service.finalize_result(
            job_id=issued.job.job_id,
            payload=changed,
            authenticated_worker_id="worker-index-01",
        )


def test_completion_projection_outbox_uses_occ_after_atomic_result_commit(
    tmp_path,
) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)
    service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=1,
    )
    worker_result = _result(issued)
    materialized = {
        "status": "completed",
        "knowledge_index": {
            "id": "knowledge-index-alpha",
            "status": "completed",
        },
        "run": {
            "id": "knowledge-attempt-alpha",
            "knowledge_index_id": "knowledge-index-alpha",
            "status": "completed",
        },
    }
    _completed, pending = (
        service.finalize_completed_result_with_projection(
            job_id=issued.job.job_id,
            worker_result=worker_result,
            materialized_result=materialized,
            authenticated_worker_id="worker-index-01",
        )
    )

    assert pending.state == "pending"
    assert pending.lock_version == 1
    projected = service.mark_completion_projection_projected(
        job_id=issued.job.job_id,
        expected_lock_version=1,
        expected_projection_digest=pending.projection_digest,
    )
    replay = service.mark_completion_projection_projected(
        job_id=issued.job.job_id,
        expected_lock_version=1,
        expected_projection_digest=pending.projection_digest,
    )

    assert projected.state == replay.state == "projected"
    assert projected.lock_version == replay.lock_version == 2
    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="knowledge_index_completion_projection_conflict",
    ):
        service.mark_completion_projection_projected(
            job_id=issued.job.job_id,
            expected_lock_version=2,
            expected_projection_digest="8" * 64,
        )


def test_completed_result_and_projection_outbox_commit_atomically(
    tmp_path,
) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)
    running = service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=issued.lock_version,
    )
    worker_result = _result(issued)
    materialized = {
        "status": "completed",
        "knowledge_index": {
            "id": "knowledge-index-alpha",
            "status": "completed",
        },
        "run": {
            "id": "knowledge-attempt-alpha",
            "knowledge_index_id": "knowledge-index-alpha",
            "status": "completed",
        },
    }

    completed, pending = (
        service.finalize_completed_result_with_projection(
            job_id=issued.job.job_id,
            worker_result=worker_result,
            materialized_result=materialized,
            authenticated_worker_id="worker-index-01",
        )
    )
    replay = service.finalize_completed_result_with_projection(
        job_id=issued.job.job_id,
        worker_result=worker_result,
        materialized_result=materialized,
        authenticated_worker_id="worker-index-01",
    )

    assert completed.state == "completed"
    assert completed.lock_version == running.lock_version + 1
    assert completed.result_digest == pending.payload["worker_result_digest"]
    assert pending.state == "pending"
    assert pending.lock_version == 1
    assert replay == (completed, pending)


def test_atomic_completion_converges_after_lost_repository_return(
    tmp_path,
) -> None:
    service, authority_port = _service(tmp_path)
    issued = _issue(service)
    service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=issued.lock_version,
    )
    repository = service._repository

    class LostReturnRepository:
        def __init__(self):
            self.return_lost = False

        def __getattr__(self, name):
            return getattr(repository, name)

        def complete_with_projection(self, **kwargs):
            committed = repository.complete_with_projection(**kwargs)
            if not self.return_lost:
                self.return_lost = True
                raise RuntimeError("database_return_lost")
            return committed

    recovering = KnowledgeIndexExecutionBindingService(
        repository=LostReturnRepository(),
        authority=authority_port,
        clock_ms=lambda: 10_000,
    )
    worker_result = _result(issued)
    materialized = {
        "status": "completed",
        "knowledge_index": {
            "id": "knowledge-index-alpha",
            "status": "completed",
        },
        "run": {
            "id": "knowledge-attempt-alpha",
            "knowledge_index_id": "knowledge-index-alpha",
            "status": "completed",
        },
    }

    first = recovering.finalize_completed_result_with_projection(
        job_id=issued.job.job_id,
        worker_result=worker_result,
        materialized_result=materialized,
        authenticated_worker_id="worker-index-01",
    )

    completed = repository.get(issued.job.job_id)
    pending = repository.get_completion_projection(issued.job.job_id)
    replay = recovering.finalize_completed_result_with_projection(
        job_id=issued.job.job_id,
        worker_result=worker_result,
        materialized_result=materialized,
        authenticated_worker_id="worker-index-01",
    )

    assert completed is not None and completed.state == "completed"
    assert pending is not None and pending.state == "pending"
    assert first == (completed, pending)
    assert replay == (completed, pending)


def test_conflicting_result_is_not_accepted_from_existing_completion_outbox(
    tmp_path,
) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)
    service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=issued.lock_version,
    )
    worker_result = _result(issued)
    materialized = {
        "status": "completed",
        "knowledge_index": {"id": "knowledge-index-alpha"},
        "run": {"id": "knowledge-attempt-alpha"},
    }
    completed, projection = (
        service.finalize_completed_result_with_projection(
            job_id=issued.job.job_id,
            worker_result=worker_result,
            materialized_result=materialized,
            authenticated_worker_id="worker-index-01",
        )
    )
    conflicting_result = {
        **worker_result,
        "knowledge_index": {"id": "knowledge-index-conflict"},
    }

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="knowledge_index_execution_result_state_invalid",
    ):
        service.finalize_completed_result_with_projection(
            job_id=issued.job.job_id,
            worker_result=conflicting_result,
            materialized_result={
                **materialized,
                "knowledge_index": {
                    "id": "knowledge-index-conflict"
                },
            },
            authenticated_worker_id="worker-index-01",
        )

    assert service.get_record(issued.job.job_id) == completed
    assert service.get_completion_projection(issued.job.job_id) == (
        projection
    )


def test_bound_job_is_queued_already_assigned_to_planned_worker(
    tmp_path,
) -> None:
    binding_service, _authority_port = _service(tmp_path)
    service, _tasks, queue, payloads, workers = _bound_job_service(
        binding_service
    )

    result = _submit_bound(service)
    replay = _submit_bound(service)

    assert result["job_id"].startswith("knowledge-index-")
    assert replay["job_id"] == result["job_id"]
    assert workers.calls == ["worker-index-01", "worker-index-01"]
    assert len(queue.calls) == 1
    assert payloads.store_calls == 1
    assert queue.calls[0]["status"] == "assigned"
    assert queue.calls[0]["extra_fields"]["assigned_agent_url"] == (
        "http://worker-index-01:5001"
    )


def test_bound_submission_recovers_queue_projection_without_new_payload(
    tmp_path,
) -> None:
    binding_service, _authority_port = _service(tmp_path)
    service, tasks, queue, payloads, _workers = _bound_job_service(
        binding_service,
        queue_failures=1,
    )

    with pytest.raises(RuntimeError, match="queue_temporarily_unavailable"):
        _submit_bound(service)

    assert payloads.store_calls == 1
    assert tasks.items == {}
    recovered = _submit_bound(service)

    assert recovered["job_id"] in tasks.items
    assert payloads.store_calls == 1
    assert len(queue.calls) == 2


def test_bound_submission_rejects_changed_payload_for_same_key_before_write(
    tmp_path,
) -> None:
    binding_service, _authority_port = _service(tmp_path)
    service, _tasks, queue, payloads, _workers = _bound_job_service(
        binding_service
    )
    _submit_bound(service)

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="knowledge_index_execution_idempotency_conflict",
    ):
        _submit_bound(service, source_id="source-changed")

    assert payloads.store_calls == 1
    assert len(queue.calls) == 1


def test_bound_submission_validates_expired_lease_before_payload_write(
    tmp_path,
) -> None:
    binding_service, _authority_port = _service(tmp_path)
    service, tasks, queue, payloads, _workers = _bound_job_service(
        binding_service
    )

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="assignment_lease_expired",
    ):
        _submit_bound(service, assignment=_assignment(expires=9_999))

    assert payloads.store_calls == 0
    assert tasks.items == {}
    assert queue.calls == []


def test_assigned_job_cannot_read_payload_or_submit_result(
    tmp_path,
) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="payload_access_invalid",
    ):
        service.validate_delegated_payload_access(
            assignment_id="assignment-1",
            lease_id="lease-1",
            authenticated_worker_id="worker-index-01",
        )
    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="result_state_invalid",
    ):
        service.validate_result(
            job_id=issued.job.job_id,
            payload=_result(issued),
            authenticated_worker_id="worker-index-01",
        )


def test_hub_dispatch_claim_allows_only_one_parallel_caller(
    tmp_path,
) -> None:
    service, authority_port = _service(tmp_path)
    issued = _issue(service)
    repository = SQLKnowledgeIndexExecutionRepository(
        create_engine(f"sqlite:///{tmp_path / 'execution.sqlite3'}")
    )
    claim_barrier = Barrier(2)

    class ConcurrentClaimRepository:
        def admit(self, record):
            return repository.admit(record)

        def get(self, job_id):
            return repository.get(job_id)

        def get_by_assignment(self, **kwargs):
            return repository.get_by_assignment(**kwargs)

        def compare_and_set(self, record, *, expected_lock_version):
            claim_barrier.wait(timeout=5)
            return repository.compare_and_set(
                record,
                expected_lock_version=expected_lock_version,
            )

    concurrent_service = KnowledgeIndexExecutionBindingService(
        repository=ConcurrentClaimRepository(),
        authority=authority_port,
        clock_ms=lambda: 10_000,
    )

    def claim():
        try:
            record = concurrent_service.claim_dispatch(
                job_id=issued.job.job_id,
                authenticated_worker_id="worker-index-01",
                expected_lock_version=issued.lock_version,
            )
        except KnowledgeIndexExecutionBindingError as exc:
            return exc.reason_code
        return record.state

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: claim(), range(2)))

    assert outcomes.count("running") == 1
    assert outcomes.count(
        "knowledge_index_execution_dispatch_in_progress"
    ) == 1


def test_mark_running_remains_idempotent_for_legacy_callers(tmp_path) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)
    running = service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=issued.lock_version,
    )

    replay = service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=running.lock_version,
    )

    assert replay == running


def test_governed_claim_fails_when_transport_margin_is_no_longer_available(
    tmp_path,
) -> None:
    now_ms = [10_000]
    service, _authority_port = _service(
        tmp_path,
        clock_ms=lambda: now_ms[0],
    )
    issued = _issue(service)
    now_ms[0] = 130_001

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="dispatch_window_insufficient",
    ):
        service.claim_dispatch(
            job_id=issued.job.job_id,
            authenticated_worker_id="worker-index-01",
            expected_lock_version=issued.lock_version,
        )


def test_pre_dispatch_reserve_allows_proposal_and_network_delay(
    tmp_path,
) -> None:
    now_ms = [10_000]
    service, _authority_port = _service(
        tmp_path,
        clock_ms=lambda: now_ms[0],
    )
    issued = _issue(service)
    now_ms[0] += 60_000

    claimed = service.claim_dispatch(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=issued.lock_version,
    )

    assert claimed.state == "running"


def test_crash_after_claim_reconciles_only_after_lease_and_never_replays(
    tmp_path,
) -> None:
    now_ms = [10_000]
    service, authority_port = _service(
        tmp_path,
        clock_ms=lambda: now_ms[0],
    )
    issued = _issue(service)
    running = service.claim_dispatch(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=issued.lock_version,
    )

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="dispatch_lease_active",
    ):
        service.reconcile_expired_dispatch(
            job_id=issued.job.job_id,
            expected_lock_version=running.lock_version,
        )

    # Simulate a lost Worker response followed by policy rotation while the
    # Hub was unavailable.  Closing expired state must not re-authorize work.
    now_ms[0] = issued.job.assignment.lease_expires_epoch_ms
    authority_port.current = replace(
        _authority(),
        policy_snapshot_digest="6" * 64,
    )
    failed = service.reconcile_expired_dispatch(
        job_id=issued.job.job_id,
        expected_lock_version=running.lock_version,
    )
    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="reconcile_conflict",
    ):
        service.reconcile_expired_dispatch(
            job_id=issued.job.job_id,
            expected_lock_version=running.lock_version,
        )
    replay = service.reconcile_expired_dispatch(
        job_id=issued.job.job_id,
        expected_lock_version=failed.lock_version,
    )

    assert failed == replay
    assert failed.state == "failed"
    assert failed.lock_version == running.lock_version + 1
    assert failed.completed_at_epoch_ms == now_ms[0]
    assert failed.result_digest is not None
    assert failed.job.authority_binding == issued.job.authority_binding
    assert failed.job.assignment == issued.job.assignment
    assert failed.job.attempt == 1

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="lease_stale",
    ):
        service.validate_result(
            job_id=issued.job.job_id,
            payload=_result(issued),
            authenticated_worker_id="worker-index-01",
        )
    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="retry_requires_fresh_grant",
    ):
        service.retry(
            job_id=issued.job.job_id,
            assignment=_assignment(
                generation=2,
                expires=30_000,
            ),
            expected_lock_version=failed.lock_version,
        )


def test_parallel_expiry_reconciliation_converges_on_one_tombstone(
    tmp_path,
) -> None:
    now_ms = [10_000]
    service, authority_port = _service(
        tmp_path,
        clock_ms=lambda: now_ms[0],
    )
    issued = _issue(service)
    running = service.claim_dispatch(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=issued.lock_version,
    )
    now_ms[0] = issued.job.assignment.lease_expires_epoch_ms
    repository = SQLKnowledgeIndexExecutionRepository(
        create_engine(f"sqlite:///{tmp_path / 'execution.sqlite3'}")
    )
    reconcile_barrier = Barrier(2)

    class ConcurrentReconcileRepository:
        def admit(self, record):
            return repository.admit(record)

        def get(self, job_id):
            return repository.get(job_id)

        def get_by_assignment(self, **kwargs):
            return repository.get_by_assignment(**kwargs)

        def compare_and_set(self, record, *, expected_lock_version):
            reconcile_barrier.wait(timeout=5)
            return repository.compare_and_set(
                record,
                expected_lock_version=expected_lock_version,
            )

    concurrent_service = KnowledgeIndexExecutionBindingService(
        repository=ConcurrentReconcileRepository(),
        authority=authority_port,
        clock_ms=lambda: now_ms[0],
    )

    def reconcile():
        try:
            return concurrent_service.reconcile_expired_dispatch(
                job_id=issued.job.job_id,
                expected_lock_version=running.lock_version,
            )
        except KnowledgeIndexExecutionBindingError as exc:
            return exc.reason_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: reconcile(), range(2)))

    records = [item for item in outcomes if not isinstance(item, str)]
    assert len(records) == 1
    assert records[0].state == "failed"
    assert outcomes.count(
        "knowledge_index_execution_reconcile_conflict"
    ) == 1


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            {"lease_generation": 2},
            "result_lease_generation_stale",
        ),
        (
            {"source_revision_digest": "6" * 64},
            "result_source_revision_digest_stale",
        ),
        (
            {"policy_snapshot_digest": "6" * 64},
            "result_policy_snapshot_digest_stale",
        ),
        (
            {"file_manifest_digest": "6" * 64},
            "result_file_manifest_digest_stale",
        ),
        (
            {"destination_digest": "6" * 64},
            "result_destination_digest_stale",
        ),
        (
            {"source_access_grant_digest": "6" * 64},
            "result_source_access_grant_digest_stale",
        ),
    ],
)
def test_result_echo_mismatch_is_rejected(
    tmp_path,
    mutation,
    reason,
) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)
    service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=issued.lock_version,
    )
    payload = {**_result(issued), **mutation}

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match=reason,
    ):
        service.validate_result(
            job_id=issued.job.job_id,
            payload=payload,
            authenticated_worker_id="worker-index-01",
        )


def test_current_authority_change_and_stale_lease_fail_closed(
    tmp_path,
) -> None:
    service, authority_port = _service(tmp_path)
    issued = _issue(service)
    authority_port.current = replace(
        _authority(),
        policy_snapshot_digest="6" * 64,
    )
    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="authority_stale",
    ):
        service.validate_before_dispatch(
            job_id=issued.job.job_id,
            authenticated_worker_id="worker-index-01",
        )

    expired_service, _ = _service(tmp_path / "expired")
    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="lease_expired",
    ):
        expired_service.issue(
            hub_task_id="hub-task-expired",
            owner_id="owner-alice",
            idempotency_key_digest="7" * 64,
            authority=_authority(),
            files=[
                {
                    "relative_path": "a.py",
                    "sha256": "8" * 64,
                    "size_bytes": 1,
                }
            ],
            resources=KnowledgeIndexResourceBudget(
                max_files=1,
                max_total_bytes=1,
                max_file_bytes=1,
                max_runtime_seconds=1,
                max_memory_bytes=64 * 1024 * 1024,
                max_output_bytes=1,
            ),
            payload_artifact_ref={
                "artifact_id": "artifact-expired",
                "sha256": "9" * 64,
                "size_bytes": 1,
                "media_type": "application/vnd.ananta.knowledge-index-job+json",
                "encoding": "json",
            },
            assignment=_assignment(expires=10_000),
            scope_id="source-expired",
            source_scope="repository",
            profile_name="default",
            created_by="owner-alice",
        )


def test_worker_emits_only_closed_execution_outcome(tmp_path) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)

    class Execution:
        def execute(self, _job, *, execution_deadline):
            execution_deadline.checkpoint()
            return {
                "status": "completed",
                "knowledge_index": {"id": "knowledge-index-alpha"},
                "run": {"id": "knowledge-attempt-alpha"},
                "artifact_refs": [],
                "hub_status": "completed",
            }

    result = KnowledgeIndexWorkerTaskHandler(
        Execution(),
        allow_legacy_unsigned_source_dispatch=True,
        worker_id="worker-index-01",
        clock_ms=lambda: 10_000,
    ).execute(issued.job.to_wire())

    assert result["status"] == "failed"
    assert result["reason_code"] == "worker_result_fields_unknown"
    assert "hub_status" not in result


def test_worker_proposal_has_no_source_capability(tmp_path) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)
    handler = KnowledgeIndexWorkerTaskHandler(
        object(),
        worker_id="worker-index-01",
        clock_ms=lambda: 10_000,
    )

    proposal = handler.propose(
        task={
            "worker_execution_context": {
                "knowledge_index_job": issued.job.to_wire()
            }
        }
    )

    assert proposal["tool_calls"][0]["name"] == (
        "codecompass_index_build"
    )
    with pytest.raises(
        ValueError,
        match="proposal_source_access_forbidden",
    ):
        handler.propose(
            task={
                "worker_execution_context": {
                    "knowledge_index_job": _signed_source_access_job(
                        issued
                    )
                }
            }
        )


def _signed_source_access_job(
    record,
    *,
    grant_expires_at_epoch_ms=300_000,
):
    job = record.job.to_wire()
    authority = record.job.authority_binding
    assignment = record.job.assignment
    request = SourceAccessRequest(
        tenant_id=authority.tenant_id,
        project_id=authority.project_id,
        source_revision_id=authority.source_revision_id,
        source_revision_digest=authority.source_revision_digest,
        destination_id=authority.destination_id,
        destination_digest=authority.destination_digest,
        source_access_grant_id=authority.source_access_grant_id,
        source_access_grant_digest=(
            authority.source_access_grant_digest
        ),
        operation=GrantOperation.INDEX,
        transformation=GrantTransformation.REDACTED,
        purpose="knowledge-index",
        policy_version=authority.policy_snapshot_id,
        policy_digest=authority.policy_snapshot_digest,
        manifest_id="knowledge-index-manifest-alpha",
        manifest_digest=record.job.file_manifest.manifest_digest,
        assignment_id=assignment.assignment_id,
        lease_id=assignment.lease_id,
    )
    binding_digest = source_access_binding_digest(
        request,
        grant_expires_at_epoch_ms=grant_expires_at_epoch_ms,
    )
    signer = HubSourceAccessManifestSigner(
        SourceAccessSigningKey(
            key_id="source-access-test",
            secret=b"s" * 32,
        )
    )
    manifest = {
        "schema": "ananta.source-control.enforcement-manifest.v1",
        "authority": "hub",
        "tenant_id": request.tenant_id,
        "project_id": request.project_id,
        "source_revision_id": request.source_revision_id,
        "source_revision_digest": request.source_revision_digest,
        "destination_id": request.destination_id,
        "destination_digest": request.destination_digest,
        "source_access_grant_id": request.source_access_grant_id,
        "source_access_grant_digest": (
            request.source_access_grant_digest
        ),
        "grant_expires_at_epoch_ms": grant_expires_at_epoch_ms,
        "operation": request.operation.value,
        "transformation": request.transformation.value,
        "purpose": request.purpose,
        "policy_version": request.policy_version,
        "policy_digest": request.policy_digest,
        "content_manifest_id": request.manifest_id,
        "content_manifest_digest": request.manifest_digest,
        "assignment_id": request.assignment_id,
        "lease_id": request.lease_id,
        "binding_digest": binding_digest,
        "signature": signer.sign(manifest_digest=binding_digest),
    }
    return {
        **job,
        "source_access_enforcement_manifest": manifest,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("destination_digest", "6" * 64),
        ("source_revision_digest", "6" * 64),
        ("source_access_grant_digest", "6" * 64),
        ("assignment_id", "assignment-tampered"),
        ("lease_id", "lease-tampered"),
        ("signature", "v1.source-access-test." + "0" * 64),
    ],
)
def test_worker_rejects_signed_manifest_tampering_before_execution(
    tmp_path,
    field,
    value,
) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)
    job = _signed_source_access_job(issued)
    job["source_access_enforcement_manifest"] = {
        **job["source_access_enforcement_manifest"],
        field: value,
    }

    class Execution:
        called = False

        def execute(self, _job):
            self.called = True
            return {"status": "completed", "artifact_refs": []}

    execution = Execution()
    handler = KnowledgeIndexWorkerTaskHandler(
        execution,
        source_access_manifest_verifier=(
            WorkerSourceAccessManifestVerifier(
                {"source-access-test": b"s" * 32}
            )
        ),
        worker_id="worker-index-01",
        clock_ms=lambda: 10_000,
    )

    with pytest.raises(ValueError):
        handler.execute(job)
    assert execution.called is False


def test_worker_accepts_hub_signed_exact_binding(tmp_path) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)

    class Execution:
        def execute(self, _job, *, execution_deadline):
            execution_deadline.checkpoint()
            return {
                "status": "completed",
                "artifact_refs": [],
            }

    result = KnowledgeIndexWorkerTaskHandler(
        Execution(),
        source_access_manifest_verifier=(
            WorkerSourceAccessManifestVerifier(
                {"source-access-test": b"s" * 32}
            )
        ),
        worker_id="worker-index-01",
        clock_ms=lambda: 10_000,
    ).execute(_signed_source_access_job(issued))

    assert result["status"] == "completed"


def test_worker_rejects_correctly_signed_expired_grant_before_execution(
    tmp_path,
) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)

    class Execution:
        called = False

        def execute(self, _job):
            self.called = True
            return {"status": "completed", "artifact_refs": []}

    execution = Execution()
    handler = KnowledgeIndexWorkerTaskHandler(
        execution,
        source_access_manifest_verifier=(
            WorkerSourceAccessManifestVerifier(
                {"source-access-test": b"s" * 32}
            )
        ),
        worker_id="worker-index-01",
        clock_ms=lambda: 10_000,
    )

    with pytest.raises(
        ValueError,
        match="source_access_grant_expired",
    ):
        handler.execute(
            _signed_source_access_job(
                issued,
                grant_expires_at_epoch_ms=10_000,
            )
        )
    assert execution.called is False


def test_worker_rejects_assignment_to_another_worker(tmp_path) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)

    class Execution:
        called = False

        def execute(self, _job):
            self.called = True
            return {"status": "completed", "artifact_refs": []}

    execution = Execution()
    handler = KnowledgeIndexWorkerTaskHandler(
        execution,
        source_access_manifest_verifier=(
            WorkerSourceAccessManifestVerifier(
                {"source-access-test": b"s" * 32}
            )
        ),
        worker_id="worker-other",
        clock_ms=lambda: 10_000,
    )

    with pytest.raises(
        ValueError,
        match="assignment_worker_mismatch",
    ):
        handler.execute(_signed_source_access_job(issued))
    assert execution.called is False
