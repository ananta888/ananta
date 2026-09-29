from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_execution_binding_service import (
    KnowledgeIndexExecutionBindingError,
)
from agent.services.knowledge_index_job_service import (
    KnowledgeIndexCompletionProjectionPending,
    KnowledgeIndexJobService,
)
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


@pytest.mark.parametrize(
    "reason_code",
    [
        "knowledge_index_execution_authority_stale",
        "knowledge_index_execution_version_conflict",
    ],
)
def test_finalize_failure_never_projects_completed_source_control_state(
    reason_code,
) -> None:
    class ArtifactService:
        def materialize(self, **_kwargs):
            return result()

    class Projector:
        calls = 0

        def project(self, **_kwargs):
            self.calls += 1

    class Bindings:
        def finalize_completed_result_with_projection(self, **_kwargs):
            raise KnowledgeIndexExecutionBindingError(reason_code)

    class Service(KnowledgeIndexJobService):
        def validate_worker_result(self, **_kwargs):
            return {
                **result(),
                "artifact_refs": references(),
            }

    projector = Projector()
    service = Service(
        worker_artifact_service=ArtifactService(),
        source_control_completion_projector=projector,
        execution_binding_service=Bindings(),
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

    with pytest.raises(
        KnowledgeIndexExecutionBindingError,
        match=reason_code,
    ):
        service.materialize_worker_result(
            job_id=envelope()["job_id"],
            result={},
            task={"worker_execution_context": {"knowledge_index_job": envelope()}},
            authenticated_worker_id="worker-1",
        )

    assert projector.calls == 0


def test_projection_failure_after_finalize_is_retryable_and_replay_catches_up(
) -> None:
    events = []

    class ArtifactService:
        def materialize(self, **_kwargs):
            events.append("materialize")
            return result()

        def activate_materialized_result(self, **kwargs):
            events.append("activate")
            return dict(kwargs["result"])

    class Projector:
        calls = 0

        def project(self, **_kwargs):
            self.calls += 1
            events.append("project")
            if self.calls == 1:
                raise RuntimeError("projection temporarily unavailable")

    class Bindings:
        calls = 0

        def finalize_completed_result_with_projection(self, **_kwargs):
            self.calls += 1
            events.append("atomic_commit")
            projection = SimpleNamespace(
                job_id=envelope()["job_id"],
                state="pending",
                lock_version=1,
                projection_digest="7" * 64,
                payload={
                    "materialized_result": result(),
                    "artifact_references": references(),
                },
            )
            return SimpleNamespace(state="completed"), projection

        def mark_completion_projection_projected(self, **_kwargs):
            events.append("mark")

    class Service(KnowledgeIndexJobService):
        def validate_worker_result(self, **_kwargs):
            return {
                **result(),
                "artifact_refs": references(),
            }

    task_payload = {
        "status": "running",
        "worker_execution_context": {
            "knowledge_index_job": envelope()
        },
    }
    bindings = Bindings()
    projector = Projector()
    service = Service(
        worker_artifact_service=ArtifactService(),
        source_control_completion_projector=projector,
        execution_binding_service=bindings,
        task_repository=SimpleNamespace(
            get_by_id=lambda _job_id: SimpleNamespace(
                model_dump=lambda: task_payload
            )
        ),
    )

    with pytest.raises(
        KnowledgeIndexCompletionProjectionPending
    ) as pending:
        service.materialize_worker_result(
            job_id=envelope()["job_id"],
            result={},
            task=task_payload,
            authenticated_worker_id="worker-1",
        )

    assert pending.value.retryable is True
    assert pending.value.status_code == 503
    assert pending.value.details == {
        "projection_reason_code": "RuntimeError"
    }
    assert task_payload["status"] == "running"
    assert events == ["materialize", "atomic_commit", "project"]

    replay = service.materialize_worker_result(
        job_id=envelope()["job_id"],
        result={},
        task=task_payload,
        authenticated_worker_id="worker-1",
    )

    assert replay["status"] == "completed"
    assert bindings.calls == 2
    assert projector.calls == 2
    assert events[-5:] == [
        "materialize",
        "atomic_commit",
        "project",
        "activate",
        "mark",
    ]


def test_failed_result_deadline_expiry_after_cas_is_not_fake_projection_pending(
) -> None:
    failed = {
        "status": "failed",
        "reason_code": "knowledge_index_worker_execution_deadline_exceeded",
        "artifact_refs": [],
        "error": "deadline",
    }

    class Deadline:
        expired = False

        def require_remaining_seconds(self):
            if self.expired:
                raise RuntimeError("deadline")
            return 1.0

    deadline = Deadline()

    class ArtifactService:
        def materialize(self, **_kwargs):
            return dict(failed)

    class Bindings:
        finalized = 0

        def finalize_completed_result_with_projection(self, **_kwargs):
            pytest.fail("failed result must not commit a completion projection")

        def finalize_result(self, **_kwargs):
            self.finalized += 1
            deadline.expired = True

    class Service(KnowledgeIndexJobService):
        def validate_worker_result(self, **_kwargs):
            return dict(failed)

    bindings = Bindings()
    service = Service(
        worker_artifact_service=ArtifactService(),
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

    admitted = service.materialize_worker_result(
        job_id=envelope()["job_id"],
        result=failed,
        task={
            "worker_execution_context": {
                "knowledge_index_job": envelope()
            }
        },
        authenticated_worker_id="worker-1",
        transfer_deadline=deadline,
    )

    assert admitted == failed
    assert bindings.finalized == 1


def test_deadline_after_atomic_completion_is_hub_only_pending(
) -> None:
    events = []

    class Deadline:
        expired = False

        def require_remaining_seconds(self):
            if self.expired:
                raise RuntimeError("deadline")
            return 1.0

    deadline = Deadline()
    projection = SimpleNamespace(
        job_id=envelope()["job_id"],
        state="pending",
        lock_version=1,
        projection_digest="7" * 64,
        payload={
            "materialized_result": result(),
            "artifact_references": references(),
        },
    )

    class ArtifactService:
        def materialize(self, **_kwargs):
            events.append("materialize")
            return result()

    class Bindings:
        def finalize_completed_result_with_projection(self, **_kwargs):
            events.append("atomic_commit")
            deadline.expired = True
            return SimpleNamespace(state="completed"), projection

    class Projector:
        def project(self, **_kwargs):
            pytest.fail("expired projection must wait for Hub reconciliation")

    class Service(KnowledgeIndexJobService):
        def validate_worker_result(self, **_kwargs):
            return {
                **result(),
                "artifact_refs": references(),
            }

    service = Service(
        worker_artifact_service=ArtifactService(),
        execution_binding_service=Bindings(),
        source_control_completion_projector=Projector(),
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

    with pytest.raises(KnowledgeIndexCompletionProjectionPending):
        service.materialize_worker_result(
            job_id=envelope()["job_id"],
            result={},
            task={
                "worker_execution_context": {
                    "knowledge_index_job": envelope()
                }
            },
            authenticated_worker_id="worker-1",
            transfer_deadline=deadline,
        )

    assert events == ["materialize", "atomic_commit"]


def test_explicit_completion_reconciler_uses_outbox_without_worker_call(
) -> None:
    events = []
    projection = SimpleNamespace(
        job_id=envelope()["job_id"],
        state="pending",
        lock_version=4,
        projection_digest="7" * 64,
        payload={
            "materialized_result": result(),
            "artifact_references": references(),
        },
    )

    class ArtifactService:
        def materialize(self, **_kwargs):
            pytest.fail("reconciler must not call Worker materialization")

        def activate_materialized_result(self, **kwargs):
            events.append("activate")
            return dict(kwargs["result"])

    class Bindings:
        def get_completion_projection(self, _job_id):
            events.append("load_outbox")
            return projection

        def mark_completion_projection_projected(self, **_kwargs):
            events.append("mark")
            projection.state = "projected"
            projection.lock_version += 1
            return projection

    class Projector:
        def project(self, **_kwargs):
            events.append("project")

    class Service(KnowledgeIndexJobService):
        def get_job(self, job_id):
            return {
                "job_id": job_id,
                "status": "completed",
                "completion_projection_state": "projected",
                "completion_projection_lock_version": 5,
            }

    class Task:
        id = envelope()["job_id"]
        status = "running"
        verification_status = {}
        status_reason_details = {}
        status_reason_code = None
        history = []
        last_output = None
        last_exit_code = None
        last_proposal = None
        goal_id = None
        goal_trace_id = None
        plan_id = None
        worker_execution_context = {
            "knowledge_index_job": envelope()
        }

        def model_dump(self):
            return {
                "status": self.status,
                "verification_status": self.verification_status,
                "status_reason_details": self.status_reason_details,
                "history": self.history,
                "worker_execution_context": self.worker_execution_context,
            }

    task = Task()

    class TaskRepository:
        @staticmethod
        def get_by_id(_job_id):
            return task

        @staticmethod
        def compare_and_set_status(
            _job_id,
            *,
            expected_statuses,
            target_status,
            predicate,
            mutate,
        ):
            previous_status = task.status
            if (
                previous_status not in expected_statuses
                or not predicate(task)
            ):
                return SimpleNamespace(
                    updated=False,
                    task=task,
                    previous_status=previous_status,
                )
            task.status = target_status
            mutate(task)
            return SimpleNamespace(
                updated=True,
                task=task,
                previous_status=previous_status,
            )

    service = Service(
        worker_artifact_service=ArtifactService(),
        source_control_completion_projector=Projector(),
        execution_binding_service=Bindings(),
        task_repository=TaskRepository(),
    )

    reconciled = service.reconcile_completion_projection(
        job_id=envelope()["job_id"],
        expected_projection_lock_version=4,
    )

    assert reconciled["status"] == "completed"
    assert events == [
        "load_outbox",
        "project",
        "activate",
        "mark",
        "load_outbox",
    ]
    assert task.status == "completed"
    assert task.verification_status["knowledge_index_job_result"] == (
        result()
    )
