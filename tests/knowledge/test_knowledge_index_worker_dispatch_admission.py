from __future__ import annotations

import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.services.source_access_enforcement import (
    SourceAccessRequest,
    source_access_binding_digest,
)
from agent.services.source_access_manifest_signing import (
    HubSourceAccessManifestSigner,
    SourceAccessSigningKey,
    WorkerSourceAccessManifestVerifier,
)
from ananta_contracts.knowledge_index_dispatch import (
    KNOWLEDGE_INDEX_DISPATCH_SCHEMA,
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_ERROR_TYPE,
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON,
    SOURCE_ACCESS_MANIFEST_FIELD,
    KnowledgeIndexDispatchContractError,
    build_knowledge_index_dispatch,
    parse_knowledge_index_dispatch,
)
from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexAuthorityBinding,
    KnowledgeIndexExecutionAssignment,
    KnowledgeIndexExecutionJob,
    KnowledgeIndexExecutionPayload,
    KnowledgeIndexFileManifest,
    KnowledgeIndexResourceBudget,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
)
from worker.retrieval.knowledge_index_dispatch_admission import (
    DISPATCH_RECEIPT_SCHEMA,
    KnowledgeIndexWorkerDispatchAdmission,
    KnowledgeIndexWorkerDispatchResultPendingError,
)
from worker.retrieval.knowledge_index_execution_guard import (
    KNOWLEDGE_INDEX_WORKER_DEADLINE_EXCEEDED_REASON,
    KNOWLEDGE_INDEX_WORKER_DEADLINE_PORT_REQUIRED_REASON,
    MonotonicKnowledgeIndexExecutionGuard,
)
from worker.retrieval.knowledge_index_job_handler import (
    KnowledgeIndexWorkerTaskHandler,
)

_SIGNING_KEY = SourceAccessSigningKey(
    "worker-dispatch-test",
    b"d" * 32,
)


def _job() -> dict:
    authority = KnowledgeIndexAuthorityBinding.create(
        tenant_id="tenant-alpha",
        project_id="project-atlas",
        source_revision_id=f"srev_{'a' * 64}",
        source_revision_digest="b" * 64,
        admission_digest="c" * 64,
        policy_snapshot_id="policy-v1",
        policy_snapshot_digest="d" * 64,
        destination_id=f"dst_{'e' * 64}",
        destination_digest="f" * 64,
        source_access_grant_id=f"grant_{'1' * 64}",
        source_access_grant_digest="2" * 64,
    )
    file_manifest = KnowledgeIndexFileManifest.create(
        [
            {
                "relative_path": "README.md",
                "sha256": "3" * 64,
                "size_bytes": 1,
            }
        ]
    )
    return KnowledgeIndexExecutionJob.create(
        hub_task_id="hub-task-alpha",
        idempotency_key_digest="4" * 64,
        authority_binding=authority,
        file_manifest=file_manifest,
        resources=KnowledgeIndexResourceBudget(
            max_files=1,
            max_total_bytes=1,
            max_file_bytes=1,
            max_runtime_seconds=60,
            max_memory_bytes=64 * 1024 * 1024,
            max_output_bytes=1024,
        ),
        payload=KnowledgeIndexExecutionPayload(
            payload_artifact_ref={
                "artifact_id": "payload-artifact-alpha",
                "sha256": "5" * 64,
                "size_bytes": 1,
                "media_type": ("application/vnd.ananta.knowledge-index-job+json"),
                "encoding": "json",
            },
        ),
        assignment=KnowledgeIndexExecutionAssignment(
            assignment_id="assignment-alpha",
            worker_id="worker-index-01",
            lease_id="lease-alpha",
            lease_generation=1,
            lease_issued_epoch_ms=9_000,
            lease_expires_epoch_ms=20_000,
        ),
        scope_id="source-alpha",
        source_scope="repository",
        profile_name="default",
        created_by="owner-alpha",
        created_at_epoch_ms=9_000,
        attempt=1,
        job_type="source_records",
    ).to_wire()


def _manifest(job: dict) -> dict:
    authority = job["authority_binding"]
    assignment = job["assignment"]
    request = SourceAccessRequest(
        tenant_id=authority["tenant_id"],
        project_id=authority["project_id"],
        source_revision_id=authority["source_revision_id"],
        source_revision_digest=authority["source_revision_digest"],
        destination_id=authority["destination_id"],
        destination_digest=authority["destination_digest"],
        source_access_grant_id=authority["source_access_grant_id"],
        source_access_grant_digest=(authority["source_access_grant_digest"]),
        operation=GrantOperation.INDEX,
        transformation=GrantTransformation.REDACTED,
        purpose="knowledge-index",
        policy_version=authority["policy_snapshot_id"],
        policy_digest=authority["policy_snapshot_digest"],
        manifest_id=job["file_manifest"]["manifest_id"],
        manifest_digest=job["file_manifest"]["manifest_digest"],
        assignment_id=assignment["assignment_id"],
        lease_id=assignment["lease_id"],
    )
    expires_at = 20_000
    binding_digest = source_access_binding_digest(
        request,
        grant_expires_at_epoch_ms=expires_at,
    )
    return {
        "schema": "ananta.source-control.enforcement-manifest.v1",
        "authority": "hub",
        "tenant_id": request.tenant_id,
        "project_id": request.project_id,
        "source_revision_id": request.source_revision_id,
        "source_revision_digest": request.source_revision_digest,
        "destination_id": request.destination_id,
        "destination_digest": request.destination_digest,
        "source_access_grant_id": request.source_access_grant_id,
        "source_access_grant_digest": request.source_access_grant_digest,
        "grant_expires_at_epoch_ms": expires_at,
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
        "signature": HubSourceAccessManifestSigner(_SIGNING_KEY).sign(manifest_digest=binding_digest),
    }


def _task(job: dict) -> dict:
    return {
        "id": job["job_id"],
        "status": "todo",
        "task_kind": "codecompass_index_build",
        "worker_execution_context": {
            "knowledge_index_job": copy.deepcopy(job),
            "parallel_context": {"preserved": True},
        },
    }


class _AtomicTaskRepository:
    def __init__(self, task: dict) -> None:
        self.task = copy.deepcopy(task)
        self.write_count = 0
        self._lock = threading.Lock()
        self.receipt_ledger = _AtomicReceiptLedger()

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.task)


class _AtomicReceiptLedger:
    def __init__(self, *, clock_ms=lambda: 10_000) -> None:
        self._clock_ms = clock_ms
        self._receipts: dict[tuple[str, ...], dict] = {}
        self._bindings: dict[tuple[str, ...], dict] = {}
        self._lock = threading.Lock()
        self.fail_completion = False

    @property
    def claim_count(self) -> int:
        with self._lock:
            return len(self._receipts)

    def claim(self, **binding):
        with self._lock:
            now = int(self._clock_ms())
            if now >= binding["lease_expires_epoch_ms"]:
                raise ValueError("knowledge_index_execution_lease_stale")
            if now >= binding["grant_expires_at_epoch_ms"]:
                raise ValueError("knowledge_index_source_access_grant_expired")
            key = tuple(
                binding[field]
                for field in (
                    "worker_id",
                    "job_id",
                )
            )
            if key in self._receipts:
                if self._bindings[key] != binding:
                    raise ValueError(
                        "knowledge_index_worker_dispatch_binding_conflict"
                    )
                receipt = copy.deepcopy(self._receipts[key])
                if receipt["state"] == "completed":
                    return receipt
                raise ValueError(
                    "knowledge_index_worker_dispatch_result_pending"
                )
            receipt = {
                "schema": DISPATCH_RECEIPT_SCHEMA,
                "job_id": binding["job_id"],
                "phase": "execute",
                "worker_id": binding["worker_id"],
                "assignment_id": binding["assignment_id"],
                "lease_id": binding["lease_id"],
                "marker_digest": binding["marker_digest"],
                "manifest_binding_digest": binding["manifest_binding_digest"],
                "claimed_at_epoch_ms": now,
                "state": "claimed",
                "result_digest": None,
                "result_payload": None,
                "completed_at_epoch_ms": None,
            }
            self._bindings[key] = copy.deepcopy(binding)
            self._receipts[key] = copy.deepcopy(receipt)
            return receipt

    def complete(self, *, result_payload, **binding):
        with self._lock:
            if self.fail_completion:
                raise RuntimeError(
                    "simulated_worker_result_outbox_failure"
                )
            key = (binding["worker_id"], binding["job_id"])
            if key not in self._receipts:
                raise ValueError(
                    "knowledge_index_worker_dispatch_receipt_missing"
                )
            expected = {
                field: self._bindings[key][field]
                for field in binding
            }
            if expected != binding:
                raise ValueError(
                    "knowledge_index_worker_dispatch_binding_conflict"
                )
            encoded = json.dumps(
                result_payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("ascii")
            normalized = json.loads(encoded.decode("ascii"))
            digest = hashlib.sha256(encoded).hexdigest()
            receipt = self._receipts[key]
            if receipt["state"] == "completed":
                if (
                    receipt["result_digest"] != digest
                    or receipt["result_payload"] != normalized
                ):
                    raise ValueError(
                        "knowledge_index_worker_dispatch_result_conflict"
                    )
                return copy.deepcopy(receipt)
            receipt.update(
                {
                    "state": "completed",
                    "result_digest": digest,
                    "result_payload": normalized,
                    "completed_at_epoch_ms": int(self._clock_ms()),
                }
            )
            return copy.deepcopy(receipt)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(list(self._receipts.values()))


def _handler(execution, repository, *, execution_guard=None):
    admission = KnowledgeIndexWorkerDispatchAdmission(
        receipt_ledger=repository.receipt_ledger,
        worker_id="worker-index-01",
    )
    handler_kwargs = {}
    if execution_guard is not None:
        handler_kwargs["execution_guard"] = execution_guard
    return KnowledgeIndexWorkerTaskHandler(
        execution,
        source_access_manifest_verifier=(
            WorkerSourceAccessManifestVerifier({_SIGNING_KEY.key_id: _SIGNING_KEY.secret})
        ),
        worker_id="worker-index-01",
        worker_dispatch_admission=admission,
        require_bound_dispatch_marker=True,
        clock_ms=lambda: 10_000,
        **handler_kwargs,
    )


def test_propose_marker_has_no_capability_and_does_not_claim() -> None:
    job = _job()
    repository = _AtomicTaskRepository(_task(job))
    marker = build_knowledge_index_dispatch(
        job_id=job["job_id"],
        phase="propose",
    )

    proposal = _handler(object(), repository).propose(
        task=_task(job),
        request_data={"knowledge_index_dispatch": marker},
    )

    assert proposal["proposal_id"].endswith("-proposal")
    assert set(marker) == {"schema", "job_id", "task_kind", "phase"}
    assert SOURCE_ACCESS_MANIFEST_FIELD not in marker
    assert repository.write_count == 0
    assert repository.receipt_ledger.claim_count == 0
    assert "knowledge_index_dispatch_receipt" not in (repository.snapshot()["worker_execution_context"])


@pytest.mark.parametrize("phase", ["propose", "execute"])
def test_hub_builds_bounded_marker_without_forwarding_full_job(
    monkeypatch,
    phase,
) -> None:
    from agent.services import _task_scoped_forwarding as forwarding

    job = _job()
    manifest = _manifest(job)
    task = _task(job)

    def authorize(**_kwargs):
        task["worker_execution_context"]["knowledge_index_job"] = {
            **job,
            **({SOURCE_ACCESS_MANIFEST_FIELD: manifest} if phase == "execute" else {}),
        }

    monkeypatch.setattr(
        forwarding,
        "_authorize_codecompass_worker_dispatch",
        authorize,
    )
    payload = {"task_id": job["job_id"]}

    forwarding._prepare_codecompass_worker_dispatch(
        enabled=True,
        tid=job["job_id"],
        task=task,
        payload=payload,
        registered_agent=object(),
        registered_worker_token="worker-token",
        dispatch_phase=phase,
    )

    marker = payload["knowledge_index_dispatch"]
    expected_fields = {"schema", "job_id", "task_kind", "phase"}
    if phase == "execute":
        expected_fields.add(SOURCE_ACCESS_MANIFEST_FIELD)
        assert marker[SOURCE_ACCESS_MANIFEST_FIELD] == manifest
    assert set(marker) == expected_fields
    assert "authority_binding" not in marker
    assert "file_manifest" not in marker


def test_execute_claim_is_durable_before_port_and_blocks_parallel_replay() -> None:
    job = _job()
    manifest = _manifest(job)
    repository = _AtomicTaskRepository(_task(job))
    call_lock = threading.Lock()
    execution_calls = 0

    class Execution:
        def execute(self, executable_job, *, execution_deadline):
            nonlocal execution_calls
            execution_deadline.checkpoint()
            stored_context = repository.snapshot()["worker_execution_context"]
            assert SOURCE_ACCESS_MANIFEST_FIELD not in (stored_context["knowledge_index_job"])
            assert executable_job[SOURCE_ACCESS_MANIFEST_FIELD] == manifest
            assert repository.receipt_ledger.snapshot()[0]["schema"] == DISPATCH_RECEIPT_SCHEMA
            with call_lock:
                execution_calls += 1
            return {"status": "completed", "artifact_refs": []}

    handler = _handler(Execution(), repository)
    marker = build_knowledge_index_dispatch(
        job_id=job["job_id"],
        phase="execute",
        source_access_manifest=manifest,
    )
    start = threading.Barrier(2)

    def invoke():
        start.wait(timeout=2)
        try:
            return handler.execute(
                task=_task(job),
                request_data={"knowledge_index_dispatch": marker},
            )
        except (
            KnowledgeIndexWorkerDispatchResultPendingError,
            ValueError,
        ) as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: invoke(), range(2)))

    completed = [value for value in outcomes if isinstance(value, dict)]
    rejected = [value for value in outcomes if isinstance(value, str)]
    assert completed
    assert all(value["status"] == "completed" for value in completed)
    assert len(completed) + len(rejected) == 2
    assert set(rejected) <= {
        "knowledge_index_worker_dispatch_result_pending"
    }
    assert execution_calls == 1
    assert repository.write_count == 0
    assert repository.receipt_ledger.claim_count == 1
    stored_context = repository.snapshot()["worker_execution_context"]
    assert stored_context["parallel_context"] == {"preserved": True}
    assert SOURCE_ACCESS_MANIFEST_FIELD not in (stored_context["knowledge_index_job"])


def test_lost_http_response_retry_replays_without_reexecution() -> None:
    job = _job()
    manifest = _manifest(job)
    repository = _AtomicTaskRepository(_task(job))
    execution_calls = 0

    class Execution:
        def execute(self, _job, *, execution_deadline):
            nonlocal execution_calls
            execution_deadline.checkpoint()
            execution_calls += 1
            return {"status": "completed", "artifact_refs": []}

    handler = _handler(Execution(), repository)
    request_data = {
        "knowledge_index_dispatch": build_knowledge_index_dispatch(
            job_id=job["job_id"],
            phase="execute",
            source_access_manifest=manifest,
        )
    }

    first_result = handler.execute(
        task=_task(job),
        request_data=request_data,
    )
    replayed_result = handler.execute(
        task=_task(job),
        request_data=request_data,
    )

    assert first_result == replayed_result
    assert first_result["status"] == "completed"
    assert execution_calls == 1
    assert repository.receipt_ledger.snapshot()[0]["state"] == (
        "completed"
    )


def test_retry_after_crash_before_result_outbox_is_explicit() -> None:
    job = _job()
    manifest = _manifest(job)
    repository = _AtomicTaskRepository(_task(job))
    repository.receipt_ledger.fail_completion = True
    execution_calls = 0

    class Execution:
        def execute(self, _job, *, execution_deadline):
            nonlocal execution_calls
            execution_deadline.checkpoint()
            execution_calls += 1
            return {"status": "completed", "artifact_refs": []}

    handler = _handler(Execution(), repository)
    request_data = {
        "knowledge_index_dispatch": build_knowledge_index_dispatch(
            job_id=job["job_id"],
            phase="execute",
            source_access_manifest=manifest,
        )
    }

    with pytest.raises(
        RuntimeError,
        match="simulated_worker_result_outbox_failure",
    ):
        handler.execute(task=_task(job), request_data=request_data)
    repository.receipt_ledger.fail_completion = False
    with pytest.raises(
        KnowledgeIndexWorkerDispatchResultPendingError,
    ) as pending:
        handler.execute(task=_task(job), request_data=request_data)

    assert str(pending.value) == (
        KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON
    )
    assert type(pending.value).__name__ == (
        KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_ERROR_TYPE
    )
    assert pending.value.status_code == 409
    assert pending.value.retryable is True
    assert pending.value.details == {
        "reason_code": (
            KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON
        )
    }
    assert execution_calls == 1
    assert repository.receipt_ledger.snapshot()[0]["state"] == (
        "claimed"
    )


def test_execute_accepts_exact_manifest_already_mirrored_locally() -> None:
    job = _job()
    manifest = _manifest(job)
    mirrored_job = {
        **job,
        SOURCE_ACCESS_MANIFEST_FIELD: copy.deepcopy(manifest),
    }
    mirrored_task = _task(mirrored_job)
    repository = _AtomicTaskRepository(mirrored_task)

    class Execution:
        def execute(self, executable_job, *, execution_deadline):
            execution_deadline.checkpoint()
            assert executable_job == mirrored_job
            return {"status": "completed", "artifact_refs": []}

    result = _handler(Execution(), repository).execute(
        task=copy.deepcopy(mirrored_task),
        request_data={
            "knowledge_index_dispatch": build_knowledge_index_dispatch(
                job_id=job["job_id"],
                phase="execute",
                source_access_manifest=manifest,
            )
        },
    )

    assert result["status"] == "completed"
    assert repository.write_count == 0
    assert repository.receipt_ledger.snapshot()[0]["schema"] == (DISPATCH_RECEIPT_SCHEMA)
    assert "knowledge_index_dispatch_receipt" not in (repository.snapshot()["worker_execution_context"])


def test_v2_execution_fails_closed_without_deadline_aware_port() -> None:
    job = _job()
    manifest = _manifest(job)
    repository = _AtomicTaskRepository(_task(job))

    class LegacyExecution:
        called = False

        def execute(self, _job):
            self.called = True
            return {"status": "completed", "artifact_refs": []}

    execution = LegacyExecution()
    result = _handler(execution, repository).execute(
        task=_task(job),
        request_data={
            "knowledge_index_dispatch": build_knowledge_index_dispatch(
                job_id=job["job_id"],
                phase="execute",
                source_access_manifest=manifest,
            )
        },
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == (
        KNOWLEDGE_INDEX_WORKER_DEADLINE_PORT_REQUIRED_REASON
    )
    assert execution.called is False


def test_worker_enforces_bound_runtime_inside_execution_port() -> None:
    job = _job()
    manifest = _manifest(job)
    repository = _AtomicTaskRepository(_task(job))
    now = [100.0]

    class Execution:
        def execute(self, _job, *, execution_deadline):
            now[0] = 160.0
            execution_deadline.checkpoint()
            pytest.fail("expired execution continued")

    result = _handler(
        Execution(),
        repository,
        execution_guard=MonotonicKnowledgeIndexExecutionGuard(
            monotonic_clock=lambda: now[0],
        ),
    ).execute(
        task=_task(job),
        request_data={
            "knowledge_index_dispatch": build_knowledge_index_dispatch(
                job_id=job["job_id"],
                phase="execute",
                source_access_manifest=manifest,
            )
        },
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == (
        KNOWLEDGE_INDEX_WORKER_DEADLINE_EXCEEDED_REASON
    )


def test_v2_network_handler_fails_closed_without_exact_marker() -> None:
    job = _job()
    repository = _AtomicTaskRepository(_task(job))

    class Execution:
        called = False

        def execute(self, _job):
            self.called = True
            return {"status": "completed", "artifact_refs": []}

    execution = Execution()
    with pytest.raises(
        KnowledgeIndexDispatchContractError,
        match="knowledge_index_dispatch_missing",
    ):
        _handler(execution, repository).execute(
            task=_task(job),
            request_data={},
        )
    assert execution.called is False
    assert repository.write_count == 0


def test_marker_rejects_unknown_fields_and_path_phase_spoof() -> None:
    job = _job()
    marker = build_knowledge_index_dispatch(
        job_id=job["job_id"],
        phase="propose",
    )
    with pytest.raises(
        KnowledgeIndexDispatchContractError,
        match="knowledge_index_dispatch_invalid",
    ):
        parse_knowledge_index_dispatch(
            {**marker, "source_access_enforcement_manifest": {}},
            expected_phase="propose",
            expected_job_id=job["job_id"],
        )
    with pytest.raises(
        KnowledgeIndexDispatchContractError,
        match="knowledge_index_dispatch_phase_mismatch",
    ):
        parse_knowledge_index_dispatch(
            marker,
            expected_phase="execute",
            expected_job_id=job["job_id"],
        )


def test_legacy_v1_programmatic_handler_remains_compatible() -> None:
    fingerprint = "a" * 64

    class Execution:
        def execute(self, _job):
            return {"status": "completed", "artifact_refs": []}

    result = KnowledgeIndexWorkerTaskHandler(Execution()).execute(
        {
            "schema": "ananta.knowledge_index_job.v1",
            "job_id": "knowledge-index-legacy",
            "idempotency_fingerprint": fingerprint,
            "job_type": "source_records",
            "payload": {},
        }
    )

    assert result["status"] == "completed"
    assert result["schema"] == "ananta.knowledge_index_job_result.v1"
    assert KNOWLEDGE_INDEX_DISPATCH_SCHEMA.startswith("ananta.knowledge_index_dispatch.")
