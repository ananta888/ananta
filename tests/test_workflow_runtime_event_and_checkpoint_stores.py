from __future__ import annotations

from collections.abc import Iterator

import pytest

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
    assert checkpoint_store.save(second, expected_revision=999) == second

    with pytest.raises(OptimisticConcurrencyError, match="revision_conflict"):
        checkpoint_store.save(_checkpoint(revision=3, fence=2, checkpoint_id="cp-3"), expected_revision=1)
    with pytest.raises(FencingTokenError, match="stale"):
        checkpoint_store.save(_checkpoint(revision=3, fence=1, checkpoint_id="cp-stale"), expected_revision=2)

    assert first.revision == 1
