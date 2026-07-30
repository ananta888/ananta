from __future__ import annotations

from types import SimpleNamespace

import pytest

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


class _Job:
    def __init__(self, envelope):
        self._envelope = dict(envelope)

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

    def validate_before_dispatch(
        self,
        *,
        job_id,
        authenticated_worker_id,
    ):
        self.dispatch_calls.append((job_id, authenticated_worker_id))
        return SimpleNamespace(job=_Job(self.envelope))

    def retry(self, *, job_id, assignment, **_options):
        self.envelope = {
            **self.envelope,
            "assignment": assignment.model_dump(mode="json"),
            "attempt": int(self.envelope.get("attempt") or 1) + 1,
        }
        return SimpleNamespace(
            job=_Job(self.envelope),
            state="queued",
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
            "lease_expires_epoch_ms": 2_000,
        },
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
    )

    context = service.authorize_bound_worker_dispatch(
        job_id=envelope["job_id"],
        authenticated_worker_id="worker-1",
    )

    assert binding.dispatch_calls == [
        (envelope["job_id"], "worker-1")
    ]
    assert context == {"knowledge_index_job": envelope}


def test_retry_replaces_queued_worker_execution_context() -> None:
    envelope = _envelope()
    repository = _Repository(_task(envelope))
    binding = _BindingService(envelope)
    service = KnowledgeIndexJobService(
        task_repository=repository,
        execution_binding_service=binding,
        allow_legacy_unresolved_destination=True,
        allow_legacy_unsigned_source_dispatch=True,
    )

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

    queued = repository.task["worker_execution_context"][
        "knowledge_index_job"
    ]
    assert queued["attempt"] == 2
    assert queued["assignment"]["worker_id"] == "worker-2"
    assert queued == binding.envelope


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
