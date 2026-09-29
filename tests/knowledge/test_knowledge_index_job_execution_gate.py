from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_execution_binding_service import (
    KnowledgeIndexExecutionBindingError,
)
from agent.services.knowledge_index_job_service import (
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KnowledgeIndexJobService,
)


class _Repository:
    def __init__(self, task):
        self.task = task

    def get_by_id(self, task_id):
        if task_id != self.task["id"]:
            return None
        return self.task

    def save(self, task):
        self.task = dict(task)
        return self.task

    def replace_bound_knowledge_index_envelope(
        self,
        task_id,
        *,
        expected_envelope,
        replacement_envelope,
    ):
        assert task_id == self.task["id"]
        context = dict(self.task["worker_execution_context"])
        assert context["knowledge_index_job"] == expected_envelope
        context["knowledge_index_job"] = dict(replacement_envelope)
        self.task = {**self.task, "worker_execution_context": context}
        return self.task

    def compare_and_set_status(
        self,
        task_id,
        *,
        expected_statuses,
        target_status,
        predicate=None,
        mutate=None,
    ):
        assert task_id == self.task["id"]
        previous_status = self.task["status"]
        if (
            previous_status not in expected_statuses
            or (predicate is not None and not predicate(self.task))
        ):
            return SimpleNamespace(
                updated=False,
                task=self.task,
                previous_status=previous_status,
            )
        candidate = copy.deepcopy(self.task)
        candidate["status"] = target_status
        if mutate is not None:
            mutate(candidate)
        self.task = candidate
        return SimpleNamespace(
            updated=True,
            task=self.task,
            previous_status=previous_status,
        )


class _Job:
    def __init__(self, envelope):
        self._envelope = dict(envelope)
        self.job_id = str(envelope["job_id"])
        self.authority_binding = SimpleNamespace(
            binding_digest="a" * 64
        )
        self.assignment = SimpleNamespace(
            **dict(envelope["assignment"])
        )

    def to_wire(self):
        return dict(self._envelope)


class _ParsedResult:
    def __init__(self, payload):
        self._payload = dict(payload)

    def to_wire(self):
        return dict(self._payload)


class _BindingService:
    def __init__(self, envelope):
        self.envelope = dict(envelope)
        self.dispatch_calls = []
        self.claim_calls = []
        self.retry_calls = []
        self.reconcile_calls = []
        self.execution_lock_version = 1
        self.execution_state = "assigned"
        self.reconciled_record = None

    def validate_before_dispatch(
        self,
        *,
        job_id,
        authenticated_worker_id,
    ):
        self.dispatch_calls.append((job_id, authenticated_worker_id))
        return SimpleNamespace(
            job=_Job(self.envelope),
            lock_version=1,
        )

    def claim_dispatch(self, **values):
        self.claim_calls.append(values)

    def retry(self, *, job_id, assignment, **_options):
        self.retry_calls.append((job_id, assignment))
        raise KnowledgeIndexExecutionBindingError(
            "knowledge_index_retry_requires_fresh_grant"
        )

    def reconcile_expired_dispatch(
        self,
        *,
        job_id,
        expected_lock_version,
    ):
        self.reconcile_calls.append((job_id, expected_lock_version))
        if expected_lock_version != self.execution_lock_version:
            raise KnowledgeIndexExecutionBindingError(
                "knowledge_index_execution_reconcile_conflict"
            )
        if self.reconciled_record is None:
            self.execution_lock_version += 1
            self.reconciled_record = SimpleNamespace(
                job=_Job(self.envelope),
                state="failed",
                lock_version=self.execution_lock_version,
                completed_at_epoch_ms=2_000,
            )
        return self.reconciled_record

    def get_record(self, job_id):
        assert job_id == self.envelope["job_id"]
        if self.reconciled_record is not None:
            return self.reconciled_record
        return SimpleNamespace(
            job=_Job(self.envelope),
            state=self.execution_state,
            lock_version=self.execution_lock_version,
        )

    def validate_result(
        self,
        *,
        job_id,
        payload,
        authenticated_worker_id,
    ):
        del job_id, authenticated_worker_id
        return SimpleNamespace(), _ParsedResult(payload)


def _envelope():
    return {
        "schema": KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
        "job_id": "knowledge-index-bound-001",
        "assignment": {
            "assignment_id": "assignment-1",
            "worker_id": "worker-1",
            "lease_id": "lease-1",
            "lease_generation": 1,
            "lease_issued_epoch_ms": 1_000,
            "lease_expires_epoch_ms": 100_000,
        },
        "resources": {"max_runtime_seconds": 60},
        "attempt": 1,
    }


def _task(envelope):
    return {
        "id": envelope["job_id"],
        "status": "todo",
        "worker_execution_context": {
            "knowledge_index_job": dict(envelope)
        },
    }


def test_dispatch_must_pass_binding_gate() -> None:
    envelope = _envelope()
    repository = _Repository(_task(envelope))
    binding = _BindingService(envelope)
    service = KnowledgeIndexJobService(
        task_repository=repository,
        execution_binding_service=binding,
        allow_legacy_unresolved_destination=True,
        allow_legacy_unsigned_source_dispatch=True,
        clock=lambda: 1.0,
    )

    context = service.authorize_bound_worker_dispatch(
        job_id=envelope["job_id"],
        authenticated_worker_id="worker-1",
    )

    assert binding.dispatch_calls == [
        (envelope["job_id"], "worker-1")
    ]
    assert binding.claim_calls == [
        {
            "job_id": envelope["job_id"],
            "authenticated_worker_id": "worker-1",
            "expected_lock_version": 1,
        }
    ]
    assert context == {"knowledge_index_job": envelope}


def test_retry_requires_fresh_job_and_grant_without_queue_mutation() -> None:
    envelope = _envelope()
    repository = _Repository(_task(envelope))
    binding = _BindingService(envelope)
    service = KnowledgeIndexJobService(
        task_repository=repository,
        execution_binding_service=binding,
        allow_legacy_unresolved_destination=True,
        allow_legacy_unsigned_source_dispatch=True,
    )

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match="retry_requires_fresh_grant",
    ):
        service.retry_bound_job(
            job_id=envelope["job_id"],
            assignment={
                "assignment_id": "assignment-2",
                "worker_id": "worker-2",
                "lease_id": "lease-2",
                "lease_generation": 2,
                "lease_issued_epoch_ms": 2_000,
                "lease_expires_epoch_ms": 3_000,
            },
        )

    assert len(binding.retry_calls) == 1
    assert repository.task == _task(envelope)
    assert binding.envelope == envelope


def test_expired_dispatch_reconciliation_projects_task_failure_once() -> None:
    envelope = _envelope()
    repository = _Repository(_task(envelope))
    binding = _BindingService(envelope)
    service = KnowledgeIndexJobService(
        task_repository=repository,
        execution_binding_service=binding,
        allow_legacy_unresolved_destination=True,
        allow_legacy_unsigned_source_dispatch=True,
    )

    result = service.reconcile_expired_bound_dispatch(
        job_id=envelope["job_id"],
        expected_lock_version=1,
    )
    replay = service.reconcile_expired_bound_dispatch(
        job_id=envelope["job_id"],
        expected_lock_version=2,
    )

    assert result == replay
    assert result["status"] == "failed"
    assert result["execution_lock_version"] == 2
    assert binding.reconcile_calls == [
        (envelope["job_id"], 1),
        (envelope["job_id"], 2),
    ]
    assert repository.task["status"] == "failed"
    assert repository.task["status_reason_code"] == (
        "knowledge_index_execution_dispatch_lease_expired"
    )
    marker = repository.task["status_reason_details"][
        "knowledge_index_dispatch_reconciliation"
    ]
    assert marker["job_id"] == envelope["job_id"]
    assert marker["execution_lock_version"] == 2
    assert marker["assignment_id"] == "assignment-1"


def test_expired_dispatch_reconciliation_projects_proposing_task_failure() -> None:
    envelope = _envelope()
    task = _task(envelope)
    task["status"] = "proposing"
    repository = _Repository(task)
    binding = _BindingService(envelope)
    service = KnowledgeIndexJobService(
        task_repository=repository,
        execution_binding_service=binding,
        allow_legacy_unresolved_destination=True,
        allow_legacy_unsigned_source_dispatch=True,
    )

    result = service.reconcile_expired_bound_dispatch(
        job_id=envelope["job_id"],
        expected_lock_version=1,
    )

    assert result["status"] == "failed"
    assert repository.task["status"] == "failed"
    assert repository.task["status_reason_code"] == (
        "knowledge_index_execution_dispatch_lease_expired"
    )


def test_proposing_task_keeps_queue_status_and_exposes_running_execution() -> None:
    envelope = _envelope()
    task = _task(envelope)
    task["status"] = "proposing"
    repository = _Repository(task)
    binding = _BindingService(envelope)
    binding.execution_state = "running"
    service = KnowledgeIndexJobService(
        task_repository=repository,
        execution_binding_service=binding,
        allow_legacy_unresolved_destination=True,
        allow_legacy_unsigned_source_dispatch=True,
    )

    result = service.get_job(envelope["job_id"])

    assert result is not None
    assert result["status"] == "queued"
    assert result["execution_state"] == "running"
    assert result["execution_lock_version"] == 1


def test_v2_artifact_references_are_strict_before_finalize() -> None:
    envelope = _envelope()
    repository = _Repository(_task(envelope))
    binding = _BindingService(envelope)
    service = KnowledgeIndexJobService(
        task_repository=repository,
        execution_binding_service=binding,
        allow_legacy_unresolved_destination=True,
        allow_legacy_unsigned_source_dispatch=True,
    )
    result = {
        "schema": "ananta.knowledge_index_execution_result.v2",
        "job_id": envelope["job_id"],
        "artifact_refs": [
            {
                "artifact_id": "artifact-1",
                "sha256": "A" * 64,
                "media_type": "application/json",
            }
        ],
    }

    with pytest.raises(
        ValueError,
        match="artifact_ref_digest_invalid",
    ):
        service.validate_worker_result(
            job_id=envelope["job_id"],
            result=result,
            authenticated_worker_id="worker-1",
        )
