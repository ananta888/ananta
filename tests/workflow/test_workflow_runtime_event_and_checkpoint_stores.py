from __future__ import annotations

from collections.abc import Iterator

import pytest

from agent.services.identity_validation import IdentityValidationError
from agent.services.workflow_runtime import (
    CanonicalWorkflowEvent,
    FencingTokenError,
    HmacKeyRing,
    InMemoryCheckpointStore,
    InMemoryEventStore,
    OptimisticConcurrencyError,
    SignedCheckpoint,
    SQLiteCheckpointStore,
    SQLiteEventStore,
    WorkflowRunProjection,
    WorkflowState,
)


@pytest.fixture(params=["memory", "sqlite"])
def event_store(request: pytest.FixtureRequest, tmp_path) -> Iterator[InMemoryEventStore | SQLiteEventStore]:
    if request.param == "memory":
        yield InMemoryEventStore()
        return
    store = SQLiteEventStore(tmp_path / "events.sqlite")
    yield store
    store.close()


@pytest.fixture(params=["memory", "sqlite"])
def checkpoint_store(
    request: pytest.FixtureRequest, tmp_path
) -> Iterator[InMemoryCheckpointStore | SQLiteCheckpointStore]:
    if request.param == "memory":
        yield InMemoryCheckpointStore()
        return
    store = SQLiteCheckpointStore(tmp_path / "checkpoints.sqlite")
    yield store
    store.close()


def _event(*, event_type: str, dedupe: str, step_id: str = "") -> CanonicalWorkflowEvent:
    return CanonicalWorkflowEvent.build(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id=step_id,
        attempt=1 if step_id else 0,
        event_type=event_type,
        correlation_id="correlation-1",
        causation_id="command-1",
        dedupe_key=dedupe,
        payload={"api_token": "must-not-persist", "safe": True},
        occurred_at=100,
        event_id=f"event-{dedupe}",
    )


def test_event_store_orders_dedupes_redacts_and_enforces_optimistic_sequence(event_store) -> None:
    first = event_store.append(_event(event_type="workflow.run.started", dedupe="1"), expected_sequence=0)
    second = event_store.append(
        _event(event_type="workflow.step.started", dedupe="2", step_id="build"),
        expected_sequence=1,
    )
    duplicate = event_store.append(
        _event(event_type="workflow.step.started", dedupe="2", step_id="build"),
        expected_sequence=999,
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert duplicate == second
    assert second.payload == {"api_token": "[REDACTED]", "safe": True}
    assert event_store.list_events(tenant_id="tenant-a", run_id="run-1") == (first, second)
    assert event_store.list_events(tenant_id="tenant-b", run_id="run-1") == ()
    exact_event_query = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "dedupe_key": "2",
    }
    event_snapshot = event_store.list_events(
        tenant_id="tenant-a",
        run_id="run-1",
    )
    exact_event = event_store.get_by_dedupe(**exact_event_query)
    assert exact_event == second
    assert exact_event is not None
    exact_event.payload["safe"] = False
    assert event_store.get_by_dedupe(**exact_event_query) == second
    assert (
        event_store.get_by_dedupe(
            tenant_id="tenant-b",
            workflow_id="workflow-1",
            run_id="run-1",
            dedupe_key="2",
        )
        is None
    )
    assert (
        event_store.get_by_dedupe(
            tenant_id="tenant-a",
            workflow_id="workflow-1",
            run_id="run-missing",
            dedupe_key="2",
        )
        is None
    )
    with pytest.raises(OptimisticConcurrencyError, match="dedupe_binding_conflict"):
        event_store.get_by_dedupe(
            tenant_id="tenant-a",
            workflow_id="workflow-other",
            run_id="run-1",
            dedupe_key="2",
        )
    for field_name, invalid in (
        ("tenant_id", True),
        ("workflow_id", 7),
        ("run_id", " run-1"),
        ("dedupe_key", False),
        ("dedupe_key", " 2"),
        ("dedupe_key", "2\x00"),
        ("dedupe_key", "x" * 513),
    ):
        with pytest.raises((IdentityValidationError, ValueError)):
            event_store.get_by_dedupe(**{**exact_event_query, field_name: invalid})
    assert event_store.list_events(tenant_id="tenant-a", run_id="run-1") == event_snapshot

    with pytest.raises(OptimisticConcurrencyError, match="sequence_conflict"):
        event_store.append(_event(event_type="workflow.run.completed", dedupe="3"), expected_sequence=0)

    # Returned dictionaries cannot mutate the append-only in-memory reference store.
    first.payload["safe"] = False
    persisted = event_store.list_events(tenant_id="tenant-a", run_id="run-1")[0]
    assert persisted.payload["safe"] is True


def test_projection_rebuild_is_deterministic_after_duplicate_delivery(event_store) -> None:
    events = [
        event_store.append(_event(event_type="workflow.run.started", dedupe="1"), expected_sequence=0),
        event_store.append(
            _event(event_type="workflow.step.completed", dedupe="2", step_id="build"),
            expected_sequence=1,
        ),
        event_store.append(_event(event_type="workflow.run.completed", dedupe="3"), expected_sequence=2),
    ]
    projection = WorkflowRunProjection.rebuild(tenant_id="tenant-a", run_id="run-1", events=events)

    assert projection.status == "completed"
    assert projection.steps["build"]["status"] == "completed"
    assert projection.sequence == 3
    assert projection.apply(events[-1]) is False


def _checkpoint(*, revision: int, fence: int, checkpoint_id: str) -> SignedCheckpoint:
    keys = HmacKeyRing({"key": "x" * 32}, active_key_id="key")
    return SignedCheckpoint.issue(
        key_ring=keys,
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        task_id="task-1",
        plan_hash="f" * 64,
        policy_version="policy-1",
        runtime_id="native",
        runtime_version="1",
        state=WorkflowState(business_data={"revision": revision}),
        revision=revision,
        fencing_token=fence,
        checkpoint_id=checkpoint_id,
        now=100 + revision,
    )


def test_checkpoint_store_is_atomic_revisioned_tenant_bound_and_fenced(checkpoint_store) -> None:
    first = checkpoint_store.save(_checkpoint(revision=1, fence=2, checkpoint_id="cp-1"), expected_revision=0)
    second = checkpoint_store.save(_checkpoint(revision=2, fence=2, checkpoint_id="cp-2"), expected_revision=1)

    assert checkpoint_store.get_latest(tenant_id="tenant-a", run_id="run-1", task_id="task-1") == second
    assert checkpoint_store.get_latest(tenant_id="tenant-b", run_id="run-1", task_id="task-1") is None
    exact_checkpoint_query = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "checkpoint_id": "cp-1",
    }
    history_snapshot = checkpoint_store.list_history(
        tenant_id="tenant-a",
        run_id="run-1",
        task_id="task-1",
    )
    exact_checkpoint = checkpoint_store.get_by_id(**exact_checkpoint_query)
    assert exact_checkpoint == first
    assert exact_checkpoint is not None
    exact_checkpoint.state.business_data["revision"] = 999
    assert checkpoint_store.get_by_id(**exact_checkpoint_query) == first
    assert (
        checkpoint_store.get_by_id(
            tenant_id="tenant-b",
            workflow_id="workflow-1",
            run_id="run-1",
            task_id="task-1",
            checkpoint_id="cp-1",
        )
        is None
    )
    for mismatch in (
        {"workflow_id": "workflow-other"},
        {"run_id": "run-other"},
        {"task_id": "task-other"},
    ):
        query = {
            "tenant_id": "tenant-a",
            "workflow_id": "workflow-1",
            "run_id": "run-1",
            "task_id": "task-1",
            "checkpoint_id": "cp-1",
            **mismatch,
        }
        with pytest.raises(OptimisticConcurrencyError, match="checkpoint_id_binding_conflict"):
            checkpoint_store.get_by_id(**query)
    for field_name, invalid in (
        ("tenant_id", True),
        ("workflow_id", 7),
        ("run_id", " run-1"),
        ("task_id", False),
        ("task_id", " task-1"),
        ("checkpoint_id", "cp-1\x00"),
        ("checkpoint_id", "x" * 257),
    ):
        with pytest.raises((IdentityValidationError, ValueError)):
            checkpoint_store.get_by_id(**{**exact_checkpoint_query, field_name: invalid})
    assert (
        checkpoint_store.list_history(
            tenant_id="tenant-a",
            run_id="run-1",
            task_id="task-1",
        )
        == history_snapshot
    )
    assert checkpoint_store.save(second, expected_revision=999) == second

    with pytest.raises(OptimisticConcurrencyError, match="revision_conflict"):
        checkpoint_store.save(_checkpoint(revision=3, fence=2, checkpoint_id="cp-3"), expected_revision=1)
    with pytest.raises(FencingTokenError, match="stale"):
        checkpoint_store.save(_checkpoint(revision=3, fence=1, checkpoint_id="cp-stale"), expected_revision=2)

    assert first.revision == 1
