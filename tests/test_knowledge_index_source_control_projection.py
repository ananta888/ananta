from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_job_service import KnowledgeIndexJobService
from agent.services.knowledge_index_source_control_projection import (
    KnowledgeIndexSourceControlCompletionProjector,
    KnowledgeIndexSourceControlProjectionError,
)


class Repository:
    def __init__(self):
        self.index = None
        self.run = None
        self.calls = 0
        self.revision = SimpleNamespace(
            contract=SimpleNamespace(
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="owner-1",
                connection_id="conn-1",
                source_revision_id="srev_" + "a" * 64,
            )
        )

    def get_scoped_revision(self, **_kwargs):
        return self.revision

    def project_completed_index_run(self, *, index, run):
        self.calls += 1
        if self.index is not None:
            if self.index != index or self.run != run:
                raise ValueError("projection conflict")
            return self.index, self.run
        self.index = index
        self.run = run
        return index, run


def envelope():
    authority = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "source_revision_id": "srev_" + "a" * 64,
        "source_revision_digest": "b" * 64,
        "admission_digest": "c" * 64,
        "policy_snapshot_id": "policy-1",
        "policy_snapshot_digest": "d" * 64,
        "destination_id": "dst_" + "e" * 64,
        "destination_digest": "f" * 64,
        "source_access_grant_id": "grant_" + "1" * 64,
        "source_access_grant_digest": "2" * 64,
    }
    from ananta_contracts.knowledge_index_execution import (
        KnowledgeIndexAuthorityBinding,
        KnowledgeIndexExecutionAssignment,
        KnowledgeIndexExecutionJob,
        KnowledgeIndexExecutionPayload,
        KnowledgeIndexFileManifest,
        KnowledgeIndexPayloadArtifactRef,
        KnowledgeIndexResourceBudget,
    )

    job = KnowledgeIndexExecutionJob.create(
        hub_task_id="hub-task-1",
        job_type="source_records",
        created_at_epoch_ms=1,
        attempt=1,
        idempotency_key_digest="3" * 64,
        authority_binding=KnowledgeIndexAuthorityBinding.create(**authority),
        file_manifest=KnowledgeIndexFileManifest.create(
            files=[
                {
                    "relative_path": "README.md",
                    "sha256": "9" * 64,
                    "size_bytes": 1,
                }
            ],
        ),
        resources=KnowledgeIndexResourceBudget(
            max_files=1,
            max_total_bytes=1024,
            max_file_bytes=1024,
            max_runtime_seconds=60,
            max_memory_bytes=64 * 1024 * 1024,
            max_output_bytes=1024,
        ),
        payload=KnowledgeIndexExecutionPayload(
            payload_artifact_ref=KnowledgeIndexPayloadArtifactRef(
                artifact_id="artifact-payload",
                sha256="4" * 64,
                size_bytes=1,
                media_type=(
                    "application/vnd.ananta.knowledge-index-job+json"
                ),
            )
        ),
        assignment=KnowledgeIndexExecutionAssignment(
            assignment_id="assignment-1",
            worker_id="worker-1",
            lease_id="lease-1",
            lease_generation=1,
            lease_issued_epoch_ms=1,
            lease_expires_epoch_ms=10_000,
        ),
        scope_id="source-1",
        source_scope="repo_path",
        profile_name="default",
        created_by="owner-1",
    ).to_wire()
    job["source_access_enforcement_manifest"] = {"signature": "signed"}
    return job


def result(*, source_revision_id=None):
    revision = source_revision_id or "srev_" + "a" * 64
    public = {
        "schema": "ananta.codecompass.artifact-manifest.v1",
        "knowledge_index_id": "idx-1",
        "run_id": "run-1",
        "source_revision_id": revision,
        "status": "completed",
        "manifest_digest": "5" * 64,
    }
    return {
        "status": "completed",
        "knowledge_index": {
            "id": "idx-1",
            "status": "completed",
            "created_at": 50.0,
            "index_metadata": {"artifact_manifest": public},
        },
        "run": {
            "id": "run-1",
            "knowledge_index_id": "idx-1",
            "status": "completed",
            "created_at": 51.0,
            "finished_at": 52.0,
            "run_metadata": {"artifact_manifest": public},
        },
    }


def references():
    return [
        {
            "role": "manifest",
            "knowledge_index_id": "idx-1",
            "run_id": "run-1",
            "sha256": "6" * 64,
        }
    ]


def test_projects_completed_result_and_exact_replay() -> None:
    repository = Repository()
    projector = KnowledgeIndexSourceControlCompletionProjector(
        repository=repository,
        clock=lambda: 100.0,
    )

    first = projector.project(
        envelope=envelope(),
        result=result(),
        artifact_references=references(),
    )
    second = projector.project(
        envelope=envelope(),
        result=result(),
        artifact_references=references(),
    )

    assert first == second
    assert first[0].status == "completed"
    assert first[1].artifacts_verified is True
    assert first[1].artifact_manifest_digest == "6" * 64


def test_rejects_public_manifest_for_another_revision() -> None:
    projector = KnowledgeIndexSourceControlCompletionProjector(
        repository=Repository()
    )

    with pytest.raises(
        KnowledgeIndexSourceControlProjectionError,
        match="manifest_invalid",
    ):
        projector.project(
            envelope=envelope(),
            result=result(source_revision_id="srev_" + "9" * 64),
            artifact_references=references(),
        )


def test_projection_failure_precedes_execution_finalize() -> None:
    class ArtifactService:
        def materialize(self, **_kwargs):
            return result()

    class Projector:
        def project(self, **_kwargs):
            raise RuntimeError("projection failed")

    class Bindings:
        finalized = False

        def finalize_result(self, **_kwargs):
            self.finalized = True

    class Service(KnowledgeIndexJobService):
        def validate_worker_result(self, **_kwargs):
            return {
                **result(),
                "artifact_refs": references(),
            }

    bindings = Bindings()
    service = Service(
        worker_artifact_service=ArtifactService(),
        source_control_completion_projector=Projector(),
        execution_binding_service=bindings,
        task_repository=SimpleNamespace(
            get_by_id=lambda _job_id: SimpleNamespace(
                model_dump=lambda: {
                    "worker_execution_context": {
                        "knowledge_index_job": envelope()
                    }
                }
            )
        ),
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        service.materialize_worker_result(
            job_id=envelope()["job_id"],
            result={},
            task={"worker_execution_context": {"knowledge_index_job": envelope()}},
            authenticated_worker_id="worker-1",
        )

    assert bindings.finalized is False
