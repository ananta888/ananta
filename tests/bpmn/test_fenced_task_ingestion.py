"""Actual SQL Task creation and lease authority commit in the same transaction."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, create_engine

from agent.db_models import TaskDB
from agent.db_models.workflow_runtime import WorkflowRuntimeCheckpointDB
from agent.repositories import tasks
from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter
from agent.services.native_task_ingestion import ingest_native_task_fenced
from agent.services.workflow_runtime import HmacKeyRing
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyCheckpointStore
from tests.bpmn.completion_helpers import linear_request
from tests.test_native_graph_task_adapters import command


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'authority.sqlite'}")
    SQLModel.metadata.create_all(engine, tables=[TaskDB.__table__, WorkflowRuntimeCheckpointDB.__table__])
    monkeypatch.setattr(tasks, "_engine", lambda: engine)
    keys = HmacKeyRing({"test": "k" * 32}, active_key_id="test")
    now = [100.0]
    leases = BpmnRunLeaseService(checkpoints=SQLAlchemyCheckpointStore(engine), keys=keys, clock=lambda: now[0])
    repository = tasks.TaskRepository()
    notifications = []

    def ingest(*, lease, **values):
        ingest_native_task_fenced(
            repository=repository,
            source_resolver=lambda fields: None,
            post_commit=lambda *args, **kwargs: notifications.append((args, kwargs)),
            lease=lease,
            **values,
        )

    adapter = AnantaHubTaskQueueAdapter(
        task_queue=SimpleNamespace(ingest_task_fenced=ingest), task_repository=repository, task_runtime=None
    )
    request = linear_request()
    value = command()
    value = replace(
        value,
        tenant_id=request.plan.tenant_id,
        workflow_id=request.plan.workflow_id,
        run_id=request.run_id,
        plan_hash=request.plan.plan_hash,
        policy_version=request.plan.policy_version,
        control_task_id=request.control_task_id,
    )
    yield SimpleNamespace(
        engine=engine,
        now=now,
        leases=leases,
        request=request,
        command=value,
        repository=repository,
        adapter=adapter,
        notifications=notifications,
        keys=keys,
    )
    engine.dispose()


def test_fenced_ingestion_is_idempotent_preserves_history_and_notifies_once(setup):
    s = setup
    with s.leases.acquire(s.request) as lease:
        first = s.adapter.submit_fenced(s.command, lease=lease)
        second = s.adapter.submit_fenced(s.command, lease=lease)
    assert first == second
    rows = s.repository.get_all()
    assert len(rows) == 1
    assert rows[0].status == "created"
    assert rows[0].history[0]["event_type"] == "workflow_node_task_created"
    assert len(s.notifications) == 1


def test_expired_sender_cannot_insert_after_a_successor_takes_control(setup):
    s = setup
    with s.leases.acquire(s.request) as previous:
        s.now[0] = 131.0
        successor = BpmnRunLeaseService(
            checkpoints=SQLAlchemyCheckpointStore(s.engine), keys=s.keys, clock=lambda: s.now[0]
        )
        with successor.acquire(s.request):
            with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
                s.adapter.submit_fenced(s.command, lease=previous)
    assert s.repository.get_all() == []
    assert s.notifications == []


def test_foreign_recipient_does_not_share_control_authority(setup):
    s = setup
    with s.leases.acquire(s.request) as lease:
        with pytest.raises((ValueError, OptimisticConcurrencyError)):
            s.adapter.submit_fenced(replace(s.command, run_id="foreign-run"), lease=lease)
    assert s.repository.get_all() == []


def test_same_task_id_cannot_overwrite_a_different_command(setup):
    s = setup
    with s.leases.acquire(s.request) as lease:
        s.adapter.submit_fenced(s.command, lease=lease)
        with pytest.raises(ValueError, match="task_id_conflict"):
            s.adapter.submit_fenced(replace(s.command, input_data={"changed": True}), lease=lease)
    rows = s.repository.get_all()
    assert len(rows) == 1
    assert rows[0].worker_execution_context["native_node_command"]["input_data"] == s.command.input_data


def test_recipient_failure_rolls_back_task_without_notifying(setup, monkeypatch):
    s = setup

    def deny(*args, **kwargs):
        raise ValueError("synthetic-policy-denied")

    monkeypatch.setattr(tasks, "_apply_task_completion_policy", deny)
    with s.leases.acquire(s.request) as lease:
        with pytest.raises(ValueError, match="synthetic-policy-denied"):
            s.adapter.submit_fenced(s.command, lease=lease)
    assert s.repository.get_all() == []
    assert s.notifications == []
