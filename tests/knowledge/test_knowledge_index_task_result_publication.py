from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import TaskDB
from agent.repositories.tasks import TaskRepository
from agent.services._vector_index_result_forwarding import (
    persist_forwarded_execution_status,
)
from agent.services.knowledge_index_job_service import (
    KnowledgeIndexCompletionProjectionPending,
    KnowledgeIndexJobService,
)
from agent.services.knowledge_index_task_result_publication import (
    KnowledgeIndexTaskResultPublicationError,
    KnowledgeIndexTaskResultPublisher,
)

TASK_ID = "knowledge-index-" + "a" * 32


def _envelope() -> dict:
    return {
        "schema": "ananta.knowledge_index_execution_job.v2",
        "job_id": TASK_ID,
        "idempotency_fingerprint": "b" * 64,
    }


def _result() -> dict:
    return {
        "schema": "ananta.knowledge_index_execution_result.v2",
        "job_id": TASK_ID,
        "status": "completed",
        "reason_code": None,
        "knowledge_index": {"id": "idx-1", "status": "completed"},
        "run": {"id": "run-1", "status": "completed"},
        "artifact_refs": [],
    }


class _ProjectedBindings:
    @staticmethod
    def get_completion_projection(
        _job_id: str,
        *,
        require_terminal_result: bool = True,
    ) -> SimpleNamespace:
        del require_terminal_result
        return SimpleNamespace(
            job_id=TASK_ID,
            state="projected",
            lock_version=2,
            projection_digest="c" * 64,
        )


def test_bound_result_publication_is_atomic_and_exact_replay_is_noop(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bound-result-publication.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=TASK_ID,
                status="running",
                task_kind="codecompass_index_build",
                assigned_agent_url="http://worker-a:5001",
                history=[{"event_type": "existing"}],
                verification_status={"parallel_verification": True},
                worker_execution_context={
                    "knowledge_index_job": _envelope(),
                    "parallel_context": {"preserved": True},
                },
            )
        )
        session.commit()
    monkeypatch.setattr("agent.repositories.tasks._engine", lambda: engine)
    post_commits: list[dict] = []
    publisher = KnowledgeIndexTaskResultPublisher(
        repository=TaskRepository(),
        execution_binding_service=_ProjectedBindings(),
        post_commit=lambda *args, **kwargs: post_commits.append(
            {"args": args, "kwargs": kwargs}
        ),
    )
    values = {
        "history": [
            {"event_type": "existing"},
            {
                "event_type": "execution_result",
                "status": "completed",
                "forwarded": True,
            },
        ],
        "last_output": "indexed",
        "last_exit_code": 0,
        "verification_status": {"worker_verified": True},
    }

    first = publisher.publish(
        job_id=TASK_ID,
        expected_envelope=_envelope(),
        result=_result(),
        status_values=values,
        event_type="knowledge_index_job_completed",
        event_actor="knowledge-index-worker-gateway",
    )
    second = publisher.publish(
        job_id=TASK_ID,
        expected_envelope=_envelope(),
        result=_result(),
        status_values=values,
        event_type="knowledge_index_job_completed",
        event_actor="knowledge-index-worker-gateway",
    )

    assert first.status == "completed"
    assert second.status == "completed"
    persisted = TaskRepository().get_by_id(TASK_ID)
    assert persisted is not None
    assert persisted.last_output == "indexed"
    assert persisted.last_exit_code == 0
    assert persisted.verification_status == {
        "parallel_verification": True,
        "worker_verified": True,
        "knowledge_index_job_result": _result(),
    }
    assert persisted.worker_execution_context["parallel_context"] == {
        "preserved": True
    }
    assert [item["event_type"] for item in persisted.history] == [
        "existing",
        "execution_result",
        "knowledge_index_job_completed",
    ]
    assert (
        persisted.status_reason_details[
            "knowledge_index_task_result_publication"
        ]["completion_projection_digest"]
        == "c" * 64
    )
    assert len(post_commits) == 1


def test_completed_task_cannot_publish_before_projection_is_projected() -> None:
    task = TaskDB(
        id=TASK_ID,
        status="running",
        task_kind="codecompass_index_build",
        worker_execution_context={"knowledge_index_job": _envelope()},
    )

    class Repository:
        @staticmethod
        def compare_and_set_status(*_args, **_kwargs):
            pytest.fail("task CAS must not run before Source-Control projection")

    class Bindings:
        @staticmethod
        def get_completion_projection(_job_id):
            return SimpleNamespace(
                state="pending",
                projection_digest="c" * 64,
            )

    publisher = KnowledgeIndexTaskResultPublisher(
        repository=Repository(),
        execution_binding_service=Bindings(),
    )
    with pytest.raises(
        KnowledgeIndexTaskResultPublicationError,
        match="knowledge_index_completion_projection_not_projected",
    ):
        publisher.publish(
            job_id=TASK_ID,
            expected_envelope=task.worker_execution_context[
                "knowledge_index_job"
            ],
            result=_result(),
            event_type="knowledge_index_job_completed",
            event_actor="test",
        )
    assert task.status == "running"


def test_forwarded_bound_result_uses_specialized_publisher_only() -> None:
    publications = []

    persist_forwarded_execution_status(
        job_id=TASK_ID,
        response={"status": "completed"},
        status_values={"verification_status": {}},
        recovery_child=False,
        authoritative_recovery_task=None,
        vector_index_result=None,
        accept_vector_result=lambda **_kwargs: False,
        update_task_status=lambda *_args, **_kwargs: pytest.fail(
            "bound v2 result must not use the generic status writer"
        ),
        bound_knowledge_index_result=_result(),
        publish_bound_knowledge_index_result=lambda **kwargs: (
            publications.append(kwargs)
        ),
    )

    assert publications == [
        {
            "job_id": TASK_ID,
            "result": _result(),
            "status_values": {"verification_status": {}},
        }
    ]


def test_crash_after_projection_marker_reconciles_task_without_worker() -> None:
    events: list[str] = []
    projection = SimpleNamespace(
        job_id=TASK_ID,
        state="pending",
        lock_version=1,
        projection_digest="c" * 64,
        payload={
            "materialized_result": _result(),
            "artifact_references": [],
        },
    )
    task = TaskDB(
        id=TASK_ID,
        status="running",
        task_kind="codecompass_index_build",
        verification_status={},
        worker_execution_context={"knowledge_index_job": _envelope()},
    )

    class Repository:
        failures = 1

        @staticmethod
        def get_by_id(_task_id):
            return task

        def compare_and_set_status(
            self,
            _task_id,
            *,
            expected_statuses,
            target_status,
            predicate,
            mutate,
        ):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("task database temporarily unavailable")
            previous = task.status
            if previous not in expected_statuses or not predicate(task):
                return SimpleNamespace(
                    updated=False,
                    task=task,
                    previous_status=previous,
                )
            task.status = target_status
            mutate(task)
            return SimpleNamespace(
                updated=True,
                task=task,
                previous_status=previous,
            )

    repository = Repository()

    class Bindings:
        @staticmethod
        def get_completion_projection(
            _job_id,
            *,
            require_terminal_result=True,
        ):
            del require_terminal_result
            events.append("load_outbox")
            return projection

        @staticmethod
        def mark_completion_projection_projected(**_kwargs):
            events.append("mark")
            if projection.state == "pending":
                projection.state = "projected"
                projection.lock_version += 1
            return projection

    class Projector:
        @staticmethod
        def project(**_kwargs):
            events.append("project")

    class ArtifactService:
        @staticmethod
        def materialize(**_kwargs):
            pytest.fail("completion reconciliation must not invoke Worker materialization")

        @staticmethod
        def activate_materialized_result(**kwargs):
            events.append("activate")
            return dict(kwargs["result"])

    bindings = Bindings()
    service = KnowledgeIndexJobService(
        task_repository=repository,
        execution_binding_service=bindings,
        source_control_completion_projector=Projector(),
        worker_artifact_service=ArtifactService(),
        task_result_publisher=KnowledgeIndexTaskResultPublisher(
            repository=repository,
            execution_binding_service=bindings,
        ),
    )

    with pytest.raises(KnowledgeIndexCompletionProjectionPending):
        service.reconcile_completion_projection(
            job_id=TASK_ID,
            expected_projection_lock_version=1,
        )

    assert task.status == "running"
    assert projection.state == "projected"
    assert projection.lock_version == 2

    reconciled = service.reconcile_completion_projection(
        job_id=TASK_ID,
        expected_projection_lock_version=2,
    )

    assert reconciled["status"] == "completed"
    assert task.verification_status["knowledge_index_job_result"] == (
        _result()
    )
    assert events == [
        "load_outbox",
        "project",
        "activate",
        "mark",
        "load_outbox",
        "load_outbox",
        "project",
        "activate",
        "mark",
        "load_outbox",
        "load_outbox",
    ]
