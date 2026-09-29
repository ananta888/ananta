from __future__ import annotations

import time

import pytest
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import TaskDB
from agent.repositories.tasks import TaskRepository


def _bound_envelope(*, manifest: dict | None = None) -> dict:
    envelope = {
        "schema": "ananta.knowledge_index_execution_job.v2",
        "job_id": "knowledge-index-" + "a" * 32,
        "assignment": {
            "assignment_id": "assignment-a",
            "worker_id": "worker-a",
            "lease_id": "lease-a",
            "lease_expires_epoch_ms": 9_999_999_999_999,
        },
    }
    if manifest is not None:
        envelope["source_access_enforcement_manifest"] = {
            "grant_expires_at_epoch_ms": 9_999_999_999_999,
            **dict(manifest),
        }
    return envelope


def test_bound_index_envelope_replace_preserves_parallel_context(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'task-context.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    base = _bound_envelope()
    with Session(engine) as session:
        session.add(
            TaskDB(
                id="knowledge-index-" + "a" * 32,
                task_kind="codecompass_index_build",
                worker_execution_context={
                    "knowledge_index_job": base,
                    "destination_selection": {"worker_id": "worker-a"},
                },
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    repository = TaskRepository()
    stale_parallel_writer = repository.get_by_id("knowledge-index-" + "a" * 32)
    assert stale_parallel_writer is not None
    manifest = {
        "schema": "ananta.source-control.enforcement-manifest.v1",
        "binding_digest": "b" * 64,
    }
    replacement = _bound_envelope(manifest=manifest)

    repository.replace_bound_knowledge_index_envelope(
        stale_parallel_writer.id,
        expected_envelope=base,
        replacement_envelope=replacement,
    )
    stale_parallel_writer.worker_execution_context = {
        **dict(stale_parallel_writer.worker_execution_context or {}),
        "parallel_context_key": {"value": "retained"},
    }
    stale_parallel_writer.updated_at = time.time() + 1
    repository.save(stale_parallel_writer)

    persisted = repository.get_by_id(stale_parallel_writer.id)
    assert persisted is not None
    assert persisted.worker_execution_context["knowledge_index_job"] == (replacement)
    assert persisted.worker_execution_context["parallel_context_key"] == {"value": "retained"}
    assert persisted.worker_execution_context["destination_selection"] == {"worker_id": "worker-a"}


def test_bound_index_envelope_replace_rejects_stale_authority(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'task-context-conflict.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    current = _bound_envelope(manifest={"binding_digest": "c" * 64})
    task_id = "knowledge-index-" + "a" * 32
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=task_id,
                task_kind="codecompass_index_build",
                worker_execution_context={"knowledge_index_job": current},
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    with pytest.raises(
        ValueError,
        match="knowledge_index_execution_queue_context_conflict",
    ):
        TaskRepository().replace_bound_knowledge_index_envelope(
            task_id,
            expected_envelope=_bound_envelope(),
            replacement_envelope=_bound_envelope(manifest={"binding_digest": "d" * 64}),
        )


def test_generic_writer_cannot_change_bound_kind_or_inject_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker-dispatch-claim.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    task_id = "knowledge-index-" + "a" * 32
    base = _bound_envelope()
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=task_id,
                task_kind="codecompass_index_build",
                status="todo",
                assigned_agent_url="http://worker-a:5001",
                worker_execution_context={
                    "knowledge_index_job": base,
                    "destination_selection": {
                        "worker_id": "worker-a",
                        "runtime_id": "runtime-a",
                    },
                    "source_access_intent": {
                        "operation": "index",
                        "purpose": "knowledge-index",
                    },
                    "parallel_context": {"before": True},
                },
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    repository = TaskRepository()
    stale = repository.get_by_id(task_id)
    assert stale is not None
    stale.status = "completed"
    stale.assigned_agent_url = "http://attacker:5001"
    stale.task_kind = "shell"
    stale.worker_execution_context = {
        **dict(stale.worker_execution_context or {}),
        "knowledge_index_dispatch_receipt": {"schema": "attacker-receipt"},
        "knowledge_index_worker_binding": {
            "schema": "ananta.knowledge_index_worker_binding.v1",
            "worker_id": "attacker",
            "worker_url": "http://attacker:5001",
        },
        "destination_selection": {"worker_id": "attacker"},
        "source_access_intent": {"operation": "exfiltrate"},
        "parallel_context": {"after": True},
    }
    repository.save(stale)

    persisted = repository.get_by_id(task_id)
    assert persisted is not None
    context = persisted.worker_execution_context
    assert persisted.status == "todo"
    assert persisted.assigned_agent_url == "http://worker-a:5001"
    assert persisted.task_kind == "codecompass_index_build"
    assert context["knowledge_index_job"] == base
    assert context["destination_selection"] == {
        "worker_id": "worker-a",
        "runtime_id": "runtime-a",
    }
    assert context["source_access_intent"] == {
        "operation": "index",
        "purpose": "knowledge-index",
    }
    assert "knowledge_index_dispatch_receipt" not in context
    assert "knowledge_index_worker_binding" not in context
    assert context["parallel_context"] == {"after": True}


def test_bound_index_status_changes_only_through_repository_cas(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bound-index-status-cas.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    task_id = "knowledge-index-" + "a" * 32
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=task_id,
                task_kind="codecompass_index_build",
                status="todo",
                assigned_agent_url="http://worker-a:5001",
                worker_execution_context={
                    "knowledge_index_job": _bound_envelope(),
                    "parallel_context": {"before": True},
                },
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    result = TaskRepository().compare_and_set_status(
        task_id,
        expected_statuses={"todo"},
        target_status="running",
        mutate=lambda task: setattr(
            task,
            "worker_execution_context",
            {
                **dict(task.worker_execution_context or {}),
                "parallel_context": {"after": True},
            },
        ),
    )

    assert result.updated is True
    assert result.previous_status == "todo"
    assert result.task is not None
    assert result.task.status == "running"
    assert result.task.assigned_agent_url == "http://worker-a:5001"
    assert result.task.worker_execution_context["knowledge_index_job"] == (
        _bound_envelope()
    )
    assert result.task.worker_execution_context["parallel_context"] == {
        "after": True
    }


def test_snapshot_upsert_validates_shared_task_without_mutating_it(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'shared-task-snapshot.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    task_id = "knowledge-index-" + "a" * 32
    base = _bound_envelope()
    mirrored = _bound_envelope(manifest={"binding_digest": "b" * 64})
    original_updated_at = 123.0
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=task_id,
                task_kind="codecompass_index_build",
                status="todo",
                assigned_agent_url="http://worker-a:5001/",
                updated_at=original_updated_at,
                worker_execution_context={
                    "knowledge_index_job": mirrored,
                    "hub_context": {"preserved": True},
                },
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    TaskRepository().upsert_bound_knowledge_index_worker_snapshot(
        task_id,
        status="todo",
        base_envelope=base,
        worker_binding={
            "schema": "ananta.knowledge_index_worker_binding.v1",
            "worker_id": "worker-a",
            "worker_url": "http://worker-a:5001",
        },
    )

    persisted = TaskRepository().get_by_id(task_id)
    assert persisted is not None
    assert persisted.updated_at == original_updated_at
    assert persisted.worker_execution_context == {
        "knowledge_index_job": mirrored,
        "hub_context": {"preserved": True},
    }


@pytest.mark.parametrize(
    ("assigned_url", "base_digest"),
    [
        ("http://worker-other:5001", "b" * 64),
        ("http://worker-a:5001", "c" * 64),
    ],
)
def test_snapshot_upsert_rejects_shared_authority_mismatch(
    tmp_path,
    monkeypatch,
    assigned_url,
    base_digest,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'shared-conflict-{base_digest[:1]}.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    task_id = "knowledge-index-" + "a" * 32
    stored = _bound_envelope()
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=task_id,
                task_kind="codecompass_index_build",
                status="todo",
                assigned_agent_url=assigned_url,
                worker_execution_context={"knowledge_index_job": stored},
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    snapshot_base = _bound_envelope()
    snapshot_base["assignment"]["assignment_id"] = "assignment-" + base_digest[:1]

    with pytest.raises(
        ValueError,
        match="knowledge_index_task_snapshot_authority_conflict",
    ):
        TaskRepository().upsert_bound_knowledge_index_worker_snapshot(
            task_id,
            status="todo",
            base_envelope=snapshot_base,
            worker_binding={
                "schema": "ananta.knowledge_index_worker_binding.v1",
                "worker_id": "worker-a",
                "worker_url": "http://worker-a:5001",
            },
        )
    persisted = TaskRepository().get_by_id(task_id)
    assert persisted is not None
    assert persisted.worker_execution_context == {"knowledge_index_job": stored}


def test_snapshot_upsert_creates_minimal_isolated_worker_task(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'isolated-worker-task.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    base = _bound_envelope()
    task_id = base["job_id"]
    binding = {
        "schema": "ananta.knowledge_index_worker_binding.v1",
        "worker_id": "worker-a",
        "worker_url": "http://worker-a:5001",
    }

    TaskRepository().upsert_bound_knowledge_index_worker_snapshot(
        task_id,
        status="todo",
        base_envelope=base,
        worker_binding=binding,
    )

    persisted = TaskRepository().get_by_id(task_id)
    assert persisted is not None
    assert persisted.task_kind == "codecompass_index_build"
    assert persisted.assigned_agent_url == "http://worker-a:5001"
    assert persisted.worker_execution_context == {
        "knowledge_index_job": base,
        "knowledge_index_worker_binding": binding,
    }

    TaskRepository().upsert_bound_knowledge_index_worker_snapshot(
        task_id,
        status="running",
        base_envelope=base,
        worker_binding=binding,
    )

    refreshed = TaskRepository().get_by_id(task_id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.assigned_agent_url == "http://worker-a:5001"
    assert refreshed.worker_execution_context == {
        "knowledge_index_job": base,
        "knowledge_index_worker_binding": binding,
    }
