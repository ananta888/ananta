from __future__ import annotations

from dataclasses import replace

import pytest
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


def _assignment(*, expires=20_000, generation=1):
    return KnowledgeIndexExecutionAssignment(
        assignment_id=f"assignment-{generation}",
        worker_id="worker-index-01",
        lease_id=f"lease-{generation}",
        lease_generation=generation,
        lease_issued_epoch_ms=9_000,
        lease_expires_epoch_ms=expires,
    )


def _service(tmp_path, authority=None):
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
        clock_ms=lambda: 10_000,
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


def test_hub_issues_idempotent_bound_job_and_finalizes_once(tmp_path) -> None:
    service, _authority_port = _service(tmp_path)
    issued = _issue(service)
    replay = _issue(service)
    running = service.mark_running(
        job_id=issued.job.job_id,
        authenticated_worker_id="worker-index-01",
        expected_lock_version=1,
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
    assert running.state == "running"
    assert completed.state == final_replay.state == "completed"
    assert completed.result_digest == final_replay.result_digest


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
        def execute(self, _job):
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
        clock_ms=lambda: 10_000,
    ).execute(issued.job.to_wire())

    assert result["status"] == "failed"
    assert result["reason_code"] == "worker_result_fields_unknown"
    assert "hub_status" not in result


def _signed_source_access_job(
    record,
    *,
    grant_expires_at_epoch_ms=20_000,
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
        def execute(self, _job):
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
