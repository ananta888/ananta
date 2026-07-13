from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.services.workflow_execution_ownership_service import (
    WorkflowExecutionOwnershipService,
)
from agent.services.workflow_runtime import (
    FencingTokenError,
    InMemoryEventStore,
    InMemoryExecutionOwnershipStore,
    InvalidTransitionError,
    SQLiteExecutionOwnershipStore,
    ownership_event,
)


@pytest.fixture(params=["memory", "sqlite"])
def ownership_store(
    request: pytest.FixtureRequest, tmp_path
) -> Iterator[InMemoryExecutionOwnershipStore | SQLiteExecutionOwnershipStore]:
    if request.param == "memory":
        yield InMemoryExecutionOwnershipStore()
        return
    value = SQLiteExecutionOwnershipStore(tmp_path / "ownership.sqlite")
    yield value
    value.close()


def _claim(store, *, owner: str, now: float):
    return store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        owner_id=owner,
        lease_seconds=10,
        maximum_retries=2,
        now=now,
    )


def test_active_lease_excludes_split_brain_and_recovery_increments_fence(ownership_store) -> None:
    first = _claim(ownership_store, owner="worker-a", now=100)
    blocked = _claim(ownership_store, owner="worker-b", now=105)
    recovered = _claim(ownership_store, owner="worker-b", now=111)

    assert first.acquired is True and first.ownership.fencing_token == 1
    assert blocked.acquired is False and blocked.reason == "lease_held"
    assert recovered.acquired is True and recovered.ownership.fencing_token == 2
    assert recovered.ownership.attempt_id != first.ownership.attempt_id

    with pytest.raises(FencingTokenError, match="stale"):
        ownership_store.acknowledge_result(
            tenant_id="tenant-a",
            run_id="run-1",
            step_id="step-1",
            attempt_id=first.ownership.attempt_id,
            owner_id="worker-a",
            fencing_token=1,
            expected_revision=first.ownership.revision,
            result_ack_key="result-old",
            now=112,
        )

    completed = ownership_store.acknowledge_result(
        tenant_id="tenant-a",
        run_id="run-1",
        step_id="step-1",
        attempt_id=recovered.ownership.attempt_id,
        owner_id="worker-b",
        fencing_token=2,
        expected_revision=recovered.ownership.revision,
        result_ack_key="result-new",
        now=112,
    )
    duplicate = ownership_store.acknowledge_result(
        tenant_id="tenant-a",
        run_id="run-1",
        step_id="step-1",
        attempt_id=recovered.ownership.attempt_id,
        owner_id="worker-b",
        fencing_token=2,
        expected_revision=recovered.ownership.revision,
        result_ack_key="result-new",
        now=113,
    )
    assert completed.status == "completed"
    assert duplicate == completed


def test_heartbeat_and_orphan_reconciliation_fail_closed(ownership_store) -> None:
    claimed = _claim(ownership_store, owner="worker-a", now=100).ownership
    heartbeat = ownership_store.heartbeat(
        tenant_id="tenant-a",
        run_id="run-1",
        step_id="step-1",
        attempt_id=claimed.attempt_id,
        owner_id="worker-a",
        fencing_token=claimed.fencing_token,
        expected_revision=claimed.revision,
        lease_seconds=10,
        now=105,
    )
    assert heartbeat.lease_expires_at == 115
    assert ownership_store.reconcile_orphan(
        tenant_id="tenant-a", run_id="run-1", step_id="step-1", now=114
    ) is None
    orphan = ownership_store.reconcile_orphan(
        tenant_id="tenant-a", run_id="run-1", step_id="step-1", now=116
    )
    assert orphan is not None and orphan.status == "orphaned"
    event = ownership_event(orphan, correlation_id="corr", causation_id="reconciler")
    assert event.event_type == "workflow.step.orphaned"
    assert event.payload["fencing_token"] == claimed.fencing_token

    with pytest.raises(FencingTokenError):
        ownership_store.heartbeat(
            tenant_id="tenant-a",
            run_id="run-1",
            step_id="step-1",
            attempt_id=claimed.attempt_id,
            owner_id="worker-a",
            fencing_token=claimed.fencing_token,
            expected_revision=orphan.revision,
            lease_seconds=10,
            now=117,
        )


def test_retry_budget_is_combined_and_deduplicated_across_runtime_layers(ownership_store) -> None:
    first = ownership_store.consume_retry(
        tenant_id="tenant-a",
        run_id="run-1",
        retry_id="retry-1",
        category="temporal_activity",
        maximum=2,
    )
    duplicate = ownership_store.consume_retry(
        tenant_id="tenant-a",
        run_id="run-1",
        retry_id="retry-1",
        category="temporal_activity",
        maximum=2,
    )
    second = ownership_store.consume_retry(
        tenant_id="tenant-a",
        run_id="run-1",
        retry_id="retry-2",
        category="provider",
        maximum=2,
    )

    assert first.used == duplicate.used == 1
    assert second.used == 2 and second.remaining == 0
    with pytest.raises(InvalidTransitionError, match="maximum_mismatch"):
        ownership_store.get_retry_budget(tenant_id="tenant-a", run_id="run-1", maximum=3)
    with pytest.raises(InvalidTransitionError, match="exhausted"):
        ownership_store.consume_retry(
            tenant_id="tenant-a",
            run_id="run-1",
            retry_id="retry-3",
            category="tool",
            maximum=2,
        )

    with pytest.raises(InvalidTransitionError, match="binding_mismatch"):
        ownership_store.consume_retry(
            tenant_id="tenant-a",
            run_id="run-1",
            retry_id="retry-1",
            category="worker",
            maximum=2,
        )


def test_parallel_owners_receive_exactly_one_active_lease(ownership_store) -> None:
    def claim(owner: str):
        return _claim(ownership_store, owner=owner, now=100)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("worker-a", "worker-b")))

    assert sorted(claim.acquired for claim in claims) == [False, True]
    assert {claim.reason for claim in claims} == {"acquired", "lease_held"}
    assert {claim.ownership.fencing_token for claim in claims} == {1}


def test_dead_letter_and_manual_recovery_create_new_attempts_and_canonical_events(
    ownership_store,
) -> None:
    claimed = _claim(ownership_store, owner="worker-a", now=100).ownership
    dead_letter = ownership_store.fail_attempt(
        tenant_id="tenant-a",
        run_id="run-1",
        step_id="step-1",
        attempt_id=claimed.attempt_id,
        owner_id="worker-a",
        fencing_token=claimed.fencing_token,
        expected_revision=claimed.revision,
        failure_code="delivery_limit_reached",
        dead_letter=True,
        now=101,
    )
    recovered = _claim(ownership_store, owner="operator-recovery", now=102).ownership

    dead_letter_event = ownership_event(
        dead_letter,
        correlation_id="corr",
        causation_id="dead-letter-monitor",
    )
    recovery_event = ownership_event(
        recovered,
        correlation_id="corr",
        causation_id="manual-resume-command",
    )

    assert dead_letter_event.event_type == "workflow.step.dead_lettered"
    assert recovery_event.event_type == "workflow.step.ownership_claimed"
    assert recovered.attempt_id != claimed.attempt_id
    assert recovered.fencing_token == claimed.fencing_token + 1


def test_every_retry_layer_draws_from_one_run_budget(ownership_store) -> None:
    categories = ("temporal_activity", "hub_task", "worker", "tool", "provider")

    snapshots = [
        ownership_store.consume_retry(
            tenant_id="tenant-budget",
            run_id="run-budget",
            retry_id=f"{category}:attempt-2",
            category=category,
            maximum=len(categories),
        )
        for category in categories
    ]

    assert [snapshot.used for snapshot in snapshots] == [1, 2, 3, 4, 5]
    with pytest.raises(InvalidTransitionError, match="exhausted"):
        ownership_store.consume_retry(
            tenant_id="tenant-budget",
            run_id="run-budget",
            retry_id="provider:attempt-3",
            category="provider",
            maximum=len(categories),
        )


def test_observable_service_persists_dead_letter_orphan_and_manual_resume_events() -> None:
    store = InMemoryExecutionOwnershipStore()
    events = InMemoryEventStore()
    service = WorkflowExecutionOwnershipService(store, events)
    claimed = service.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-events",
        step_id="step-1",
        owner_id="worker-a",
        lease_seconds=10,
        maximum_retries=3,
        correlation_id="correlation-1",
        causation_id="dispatch-1",
        now=100,
    ).ownership
    dead_letter = service.fail_attempt(
        tenant_id="tenant-a",
        run_id="run-events",
        step_id="step-1",
        attempt_id=claimed.attempt_id,
        owner_id=claimed.owner_id,
        fencing_token=claimed.fencing_token,
        expected_revision=claimed.revision,
        failure_code="delivery_limit_reached",
        dead_letter=True,
        correlation_id="correlation-1",
        causation_id="dead-letter-monitor",
        now=101,
    )
    resumed = service.manual_resume(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-events",
        step_id="step-1",
        owner_id="operator-worker",
        lease_seconds=10,
        maximum_retries=3,
        command_id="manual-resume-command-1",
        correlation_id="correlation-1",
        now=102,
    ).ownership

    persisted = events.list_events(tenant_id="tenant-a", run_id="run-events")
    assert [event.event_type for event in persisted] == [
        "workflow.step.ownership_claimed",
        "workflow.step.dead_lettered",
        "workflow.step.ownership_claimed",
    ]
    assert persisted[-1].causation_id == "manual-resume-command-1"
    assert dead_letter.status == "dead_letter"
    assert resumed.attempt_id != claimed.attempt_id
