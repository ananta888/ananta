from __future__ import annotations

from types import SimpleNamespace

import pytest

from worker.retrieval.knowledge_index_output_authorization import (
    KnowledgeIndexWorkerOutputAuthorizationError,
    KnowledgeIndexWorkerOutputCapabilityAuthorizer,
)


JOB_ID = "knowledge-index-" + "a" * 32
MANIFEST = {
    "assignment_id": "assignment-1",
    "lease_id": "lease-1",
    "grant_expires_at_epoch_ms": 20_000,
    "signature": "signed",
}


class Repository:
    def __init__(self, task):
        self.task = task

    def get_by_id(self, task_id):
        return self.task if task_id == JOB_ID else None


class Verifier:
    def __init__(self, accepted=True):
        self.accepted = accepted

    def verify_manifest(self, _manifest):
        return self.accepted


def task(*, worker_url="http://worker-a:5000", manifest=MANIFEST):
    return {
        "id": JOB_ID,
        "status": "proposing",
        "assigned_agent_url": worker_url,
        "worker_execution_context": {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
                "job_id": JOB_ID,
                "source_access_enforcement_manifest": dict(manifest),
            }
        },
    }


def parsed_job(*, worker_id="worker-a", lease_expires=15_000):
    return SimpleNamespace(
        assignment=SimpleNamespace(
            assignment_id="assignment-1",
            worker_id=worker_id,
            lease_id="lease-1",
            lease_expires_epoch_ms=lease_expires,
        )
    )


def authorizer(*, stored_task=None, verifier=None, parsed=None):
    return KnowledgeIndexWorkerOutputCapabilityAuthorizer(
        task_repository=Repository(stored_task or task()),
        manifest_verifier=verifier or Verifier(),
        worker_id="worker-a",
        worker_url="http://worker-a:5000",
        clock_ms=lambda: 10_000,
        execution_job_parser=lambda _job: parsed or parsed_job(),
    )


def authorize(service, *, metadata=None):
    service.authorize(
        artifact_id="artifact-1",
        artifact_sha256="b" * 64,
        artifact_size_bytes=42,
        artifact_media_type="application/json",
        artifact_metadata=metadata
        or {
            "system_artifact_kind": "knowledge_index_worker_output",
            "knowledge_index_job_id": JOB_ID,
            "knowledge_index_id": "idx-1",
            "knowledge_index_run_id": "run-1",
            "output_role": "manifest",
        },
        manifest=MANIFEST,
        job_id=JOB_ID,
        knowledge_index_id="idx-1",
        run_id="run-1",
        output_role="manifest",
    )


def test_authorizes_exact_live_worker_job_and_artifact_binding() -> None:
    authorize(authorizer())


@pytest.mark.parametrize(
    ("service", "reason_code"),
    [
        (
            authorizer(verifier=Verifier(False)),
            "knowledge_index_output_capability_signature_invalid",
        ),
        (
            authorizer(parsed=parsed_job(worker_id="worker-b")),
            "knowledge_index_output_execution_binding_invalid",
        ),
        (
            authorizer(parsed=parsed_job(lease_expires=9_999)),
            "knowledge_index_output_execution_binding_invalid",
        ),
        (
            authorizer(stored_task=task(worker_url="http://worker-b:5000")),
            "knowledge_index_output_worker_url_mismatch",
        ),
    ],
)
def test_denies_invalid_capability_or_delegation(service, reason_code) -> None:
    with pytest.raises(
        KnowledgeIndexWorkerOutputAuthorizationError,
        match=reason_code,
    ):
        authorize(service)


def test_denies_cross_job_artifact_metadata() -> None:
    metadata = {
        "system_artifact_kind": "knowledge_index_worker_output",
        "knowledge_index_job_id": "knowledge-index-" + "c" * 32,
        "knowledge_index_id": "idx-1",
        "knowledge_index_run_id": "run-1",
        "output_role": "manifest",
    }

    with pytest.raises(
        KnowledgeIndexWorkerOutputAuthorizationError,
        match="knowledge_index_output_artifact_binding_mismatch",
    ):
        authorize(authorizer(), metadata=metadata)
