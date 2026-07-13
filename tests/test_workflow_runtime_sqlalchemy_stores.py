from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.workflow_runtime import (
    WorkflowExecutionAttemptHistoryDB,
    WorkflowExecutionOwnershipDB,
    WorkflowProviderBudgetDB,
    WorkflowProviderBudgetReservationDB,
    WorkflowRetryBudgetDB,
    WorkflowRetryConsumptionDB,
    WorkflowRuntimeCheckpointDB,
    WorkflowRuntimeEventDB,
    WorkflowRuntimeOutboxDB,
    WorkflowSideEffectLedgerDB,
)
from agent.services.workflow_runtime import (
    CanonicalWorkflowEvent,
    FencingTokenError,
    HmacKeyRing,
    InvalidTransitionError,
    OptimisticConcurrencyError,
    ProviderBudgetError,
    ProviderBudgetLimits,
    SignedCheckpoint,
    SQLAlchemyCheckpointStore,
    SQLAlchemyEventStore,
    SQLAlchemyExecutionOwnershipStore,
    SQLAlchemyProviderBudgetStore,
    SQLAlchemySideEffectLedger,
    WorkflowState,
)

_TABLES = [
    WorkflowRuntimeEventDB.__table__,
    WorkflowRuntimeCheckpointDB.__table__,
    WorkflowSideEffectLedgerDB.__table__,
    WorkflowExecutionOwnershipDB.__table__,
    WorkflowExecutionAttemptHistoryDB.__table__,
    WorkflowRetryBudgetDB.__table__,
    WorkflowRetryConsumptionDB.__table__,
    WorkflowRuntimeOutboxDB.__table__,
    WorkflowProviderBudgetDB.__table__,
    WorkflowProviderBudgetReservationDB.__table__,
]

_POSTGRES_CONTRACT_URL = os.getenv("ANANTA_TEST_WORKFLOW_POSTGRES_URL", "").strip()
_RUNTIME_ENGINE_BACKENDS = ("sqlite", "postgresql") if _POSTGRES_CONTRACT_URL else ("sqlite",)


@pytest.fixture(params=_RUNTIME_ENGINE_BACKENDS)
def runtime_engine(request: pytest.FixtureRequest):
    if request.param == "postgresql":
        engine = create_engine(_POSTGRES_CONTRACT_URL)
    else:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    SQLModel.metadata.drop_all(engine, tables=_TABLES)
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    yield engine
    SQLModel.metadata.drop_all(engine, tables=_TABLES)
    engine.dispose()


def _event(*, dedupe: str, event_id: str, tenant_id: str = "tenant-a") -> CanonicalWorkflowEvent:
    return CanonicalWorkflowEvent.build(
        tenant_id=tenant_id,
        workflow_id="workflow-1",
        run_id="run-1",
        event_type="workflow.step.completed",
        correlation_id="correlation-1",
        causation_id="command-1",
        dedupe_key=dedupe,
        step_id="step-1",
        payload={"token": "never-store", "result_ref": "artifact://safe"},
        occurred_at=100,
        event_id=event_id,
    )


def test_event_store_has_atomic_dedupe_sequence_tenant_and_outbox_semantics(runtime_engine) -> None:
    store = SQLAlchemyEventStore(runtime_engine)
    candidate = _event(dedupe="delivery-1", event_id="event-1")

    stored = store.append(candidate, expected_sequence=0)
    duplicate = store.append(candidate, expected_sequence=999)

    assert stored == duplicate
    assert stored.sequence == 1
    assert stored.payload == {"token": "[REDACTED]", "result_ref": "artifact://safe"}
    assert store.list_events(tenant_id="tenant-a", run_id="run-1") == (stored,)
    assert store.list_events(tenant_id="tenant-b", run_id="run-1") == ()

    messages = store.outbox.list_messages(tenant_id="tenant-a")
    assert len(messages) == 1
    assert messages[0].payload["event_id"] == "event-1"
    assert store.outbox.list_messages(tenant_id="tenant-b") == ()

    claimed = store.outbox.claim_batch(
        tenant_id="tenant-a", consumer_id="publisher-1", now=100, lease_seconds=10
    )
    assert len(claimed) == 1 and claimed[0].status == "processing"
    with pytest.raises(OptimisticConcurrencyError, match="compare_and_set"):
        store.outbox.acknowledge(
            tenant_id="tenant-a",
            message_id=claimed[0].id,
            consumer_id="publisher-1",
            expected_revision=1,
            now=101,
        )
    published = store.outbox.acknowledge(
        tenant_id="tenant-a",
        message_id=claimed[0].id,
        consumer_id="publisher-1",
        expected_revision=claimed[0].revision,
        now=101,
    )
    assert published.status == "published"


def test_event_append_cas_allows_one_concurrent_next_sequence(runtime_engine) -> None:
    store = SQLAlchemyEventStore(runtime_engine)
    first = _event(dedupe="delivery-a", event_id="event-a")
    second = _event(dedupe="delivery-b", event_id="event-b")

    def append(event):
        try:
            return store.append(event, expected_sequence=0)
        except OptimisticConcurrencyError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, (first, second)))

    assert sum(isinstance(result, CanonicalWorkflowEvent) for result in results) == 1
    assert sum(isinstance(result, OptimisticConcurrencyError) for result in results) == 1
    assert len(store.list_events(tenant_id="tenant-a", run_id="run-1")) == 1
    assert len(store.outbox.list_messages(tenant_id="tenant-a")) == 1


def _checkpoint(*, revision: int, fence: int, checkpoint_id: str) -> SignedCheckpoint:
    return SignedCheckpoint.issue(
        key_ring=HmacKeyRing({"key": "x" * 32}, active_key_id="key"),
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


def test_checkpoint_store_has_revision_cas_tenant_isolation_and_fencing(runtime_engine) -> None:
    store = SQLAlchemyCheckpointStore(runtime_engine)
    first = store.save(_checkpoint(revision=1, fence=4, checkpoint_id="cp-1"), expected_revision=0)
    second = store.save(_checkpoint(revision=2, fence=4, checkpoint_id="cp-2"), expected_revision=1)

    assert store.save(second, expected_revision=999) == second
    assert store.get_latest(tenant_id="tenant-a", run_id="run-1", task_id="task-1") == second
    assert store.get_latest(tenant_id="tenant-b", run_id="run-1", task_id="task-1") is None
    assert store.list_history(tenant_id="tenant-a", run_id="run-1", task_id="task-1") == (
        first,
        second,
    )
    with pytest.raises(OptimisticConcurrencyError, match="revision_conflict"):
        store.save(_checkpoint(revision=3, fence=4, checkpoint_id="cp-3"), expected_revision=1)
    with pytest.raises(FencingTokenError, match="stale"):
        store.save(_checkpoint(revision=3, fence=3, checkpoint_id="cp-stale"), expected_revision=2)


def test_side_effect_ledger_enforces_exactly_once_claim_cas_and_fencing(runtime_engine) -> None:
    ledger = SQLAlchemySideEffectLedger(runtime_engine)
    planned = ledger.plan(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        declared_operation="git.push:origin/main",
        side_effect_class="non_idempotent_write",
    )
    assert ledger.plan(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        declared_operation="git.push:origin/main",
        side_effect_class="non_idempotent_write",
    ) == planned
    authorized = ledger.authorize(
        planned.operation_id,
        expected_revision=planned.revision,
        fencing_token=7,
        authorization_envelope_id="envelope-1",
    )

    def claim_once():
        return ledger.claim(
            planned.operation_id,
            expected_revision=authorized.revision,
            fencing_token=7,
            attempt_id="attempt-1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: claim_once(), range(2)))
    assert sorted(claim.acquired for claim in claims) == [False, True]
    assert {claim.reason for claim in claims} == {"acquired", "already_claimed"}
    started = next(claim.record for claim in claims if claim.acquired)

    with pytest.raises(FencingTokenError):
        ledger.complete(
            planned.operation_id,
            expected_revision=started.revision,
            fencing_token=6,
            attempt_id="attempt-1",
            result_ref="artifact://invalid",
        )
    completed = ledger.complete(
        planned.operation_id,
        expected_revision=started.revision,
        fencing_token=7,
        attempt_id="attempt-1",
        result_ref="artifact://result",
    )
    assert completed.status == "completed"
    assert ledger.get(tenant_id="tenant-b", operation_id=planned.operation_id) is None


def test_ownership_retry_budget_recovery_fencing_history_and_tenant_isolation(runtime_engine) -> None:
    store = SQLAlchemyExecutionOwnershipStore(runtime_engine)
    first = store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        owner_id="worker-a",
        lease_seconds=10,
        maximum_retries=2,
        now=100,
    )
    blocked = store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        owner_id="worker-b",
        lease_seconds=10,
        maximum_retries=2,
        now=105,
    )
    recovered = store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        owner_id="worker-b",
        lease_seconds=10,
        maximum_retries=2,
        now=111,
    )
    assert first.acquired is True and blocked.reason == "lease_held"
    assert recovered.acquired is True and recovered.ownership.fencing_token == 2
    assert store.get(tenant_id="tenant-b", run_id="run-1", step_id="step-1") is None
    with pytest.raises(FencingTokenError, match="stale"):
        store.acknowledge_result(
            tenant_id="tenant-a",
            run_id="run-1",
            step_id="step-1",
            attempt_id=first.ownership.attempt_id,
            owner_id="worker-a",
            fencing_token=1,
            expected_revision=first.ownership.revision,
            result_ack_key="stale-result",
            now=112,
        )
    completed = store.acknowledge_result(
        tenant_id="tenant-a",
        run_id="run-1",
        step_id="step-1",
        attempt_id=recovered.ownership.attempt_id,
        owner_id="worker-b",
        fencing_token=2,
        expected_revision=recovered.ownership.revision,
        result_ack_key="result-1",
        now=112,
    )
    assert completed.status == "completed"
    assert [item.fencing_token for item in store.list_history(
        tenant_id="tenant-a", run_id="run-1", step_id="step-1"
    )] == [1, 2, 2]

    first_retry = store.consume_retry(
        tenant_id="tenant-a",
        run_id="retry-run",
        retry_id="retry-1",
        category="provider",
        maximum=1,
    )
    duplicate = store.consume_retry(
        tenant_id="tenant-a",
        run_id="retry-run",
        retry_id="retry-1",
        category="provider",
        maximum=1,
    )
    assert first_retry == duplicate and duplicate.used == 1
    with pytest.raises(InvalidTransitionError, match="exhausted"):
        store.consume_retry(
            tenant_id="tenant-a",
            run_id="retry-run",
            retry_id="retry-2",
            category="temporal_activity",
            maximum=1,
        )


def test_provider_budget_is_persistent_idempotent_reconciled_and_fail_closed(runtime_engine) -> None:
    store = SQLAlchemyProviderBudgetStore(runtime_engine)
    limits = ProviderBudgetLimits(
        maximum_attempts=2,
        maximum_tokens=100,
        maximum_cost_micros=1_000,
    )
    first = store.reserve(
        tenant_id="tenant-a",
        run_id="provider-run",
        policy_version="policy-v1",
        reservation_id="provider-call-1",
        limits=limits,
        reserved_tokens=40,
        reserved_cost_micros=300,
    )
    duplicate = SQLAlchemyProviderBudgetStore(runtime_engine).reserve(
        tenant_id="tenant-a",
        run_id="provider-run",
        policy_version="policy-v1",
        reservation_id="provider-call-1",
        limits=limits,
        reserved_tokens=40,
        reserved_cost_micros=300,
    )
    assert first == duplicate
    assert duplicate.attempts == 1

    reconciled = store.reconcile(
        tenant_id="tenant-a",
        run_id="provider-run",
        policy_version="policy-v1",
        reservation_id="provider-call-1",
        actual_total_tokens=25,
    )
    assert reconciled.reconciled is True
    assert reconciled.tokens == 25
    assert store.reconcile(
        tenant_id="tenant-a",
        run_id="provider-run",
        policy_version="policy-v1",
        reservation_id="provider-call-1",
        actual_total_tokens=25,
    ) == reconciled

    store.reserve(
        tenant_id="tenant-a",
        run_id="provider-run",
        policy_version="policy-v1",
        reservation_id="provider-call-2",
        limits=limits,
        reserved_tokens=70,
        reserved_cost_micros=600,
    )
    with pytest.raises(ProviderBudgetError, match="retry_budget_exceeded"):
        store.reserve(
            tenant_id="tenant-a",
            run_id="provider-run",
            policy_version="policy-v1",
            reservation_id="provider-call-3",
            limits=limits,
            reserved_tokens=1,
            reserved_cost_micros=1,
        )
    with pytest.raises(ProviderBudgetError, match="binding_mismatch"):
        store.reserve(
            tenant_id="tenant-a",
            run_id="provider-run",
            policy_version="policy-v1",
            reservation_id="provider-call-1",
            limits=limits,
            reserved_tokens=41,
            reserved_cost_micros=300,
        )
