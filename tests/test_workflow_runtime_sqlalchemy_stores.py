from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.workflow_runtime import (
    WorkflowAuthorizationGrantDB,
    WorkflowExecutionAttemptHistoryDB,
    WorkflowExecutionOwnershipDB,
    WorkflowProviderBudgetDB,
    WorkflowProviderBudgetReservationDB,
    WorkflowRetryBudgetDB,
    WorkflowRetryConsumptionDB,
    WorkflowRuntimeCheckpointDB,
    WorkflowRuntimeEventDB,
    WorkflowRuntimeOutboxDB,
    WorkflowRuntimeReadModelDB,
    WorkflowSideEffectLedgerDB,
)
from agent.services.identity_validation import IdentityValidationError
from agent.services.workflow_authorization_grant_service import (
    InMemoryWorkflowAuthorizationGrantService,
    SQLAlchemyWorkflowAuthorizationGrantService,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    CanonicalWorkflowEvent,
    ContractValidationError,
    FencingTokenError,
    HmacKeyRing,
    InMemoryEventStore,
    InMemorySideEffectLedger,
    InvalidTransitionError,
    LegacyWorkflowBackendEventAdapter,
    OptimisticConcurrencyError,
    ProviderAttemptScope,
    ProviderBudgetError,
    ProviderBudgetLimits,
    ProviderProfileAttemptReservation,
    ProviderScopedBudgetReservation,
    RuntimeAuthorizationEnvelope,
    SignedCheckpoint,
    SQLAlchemyCheckpointStore,
    SQLAlchemyEventStore,
    SQLAlchemyExecutionOwnershipStore,
    SQLAlchemyProviderBudgetStore,
    SQLAlchemySideEffectLedger,
    SQLiteEventStore,
    WorkflowState,
)
from agent.services.workflow_runtime_operations_models import WorkflowRuntimeOperationRecord
from agent.services.workflow_runtime_read_model_persistence import (
    SQLAlchemyWorkflowRuntimeReadModelRepository,
)
from agent.services.workflow_runtime_read_model_service import (
    InMemoryWorkflowRuntimeReadModelRepository,
)
from agent.services.workflow_worker_gateway_service import (
    WorkflowWorkerGatewayError,
    WorkflowWorkerGatewayService,
)
from ananta_contracts.provider_execution import (
    ProviderExecutionBinding,
    ProviderProfileAttemptPlanEntry,
    ProviderProfileExecutionBinding,
)
from ananta_contracts.workflow_worker_gateway import (
    WORKFLOW_WORKER_COMMAND_SCHEMA,
)

_TABLES = [
    WorkflowAuthorizationGrantDB.__table__,
    WorkflowRuntimeEventDB.__table__,
    WorkflowRuntimeReadModelDB.__table__,
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
    exact_event_query = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "dedupe_key": "delivery-1",
    }
    event_snapshot = store.list_events(tenant_id="tenant-a", run_id="run-1")
    outbox_snapshot = store.outbox.list_messages(tenant_id="tenant-a")
    exact_event = store.get_by_dedupe(**exact_event_query)
    assert exact_event == stored
    assert exact_event is not None
    exact_event.payload["result_ref"] = "artifact://mutated"
    assert store.get_by_dedupe(**exact_event_query) == stored
    for field_name, invalid in (
        ("tenant_id", True),
        ("workflow_id", 7),
        ("run_id", " run-1"),
        ("dedupe_key", False),
        ("dedupe_key", " delivery-1"),
        ("dedupe_key", "delivery-1\x00"),
        ("dedupe_key", "x" * 513),
    ):
        with pytest.raises((IdentityValidationError, ValueError)):
            store.get_by_dedupe(**{**exact_event_query, field_name: invalid})
    assert store.list_events(tenant_id="tenant-a", run_id="run-1") == event_snapshot
    assert store.outbox.list_messages(tenant_id="tenant-a") == outbox_snapshot
    assert (
        store.get_by_dedupe(
            tenant_id="tenant-b",
            workflow_id="workflow-1",
            run_id="run-1",
            dedupe_key="delivery-1",
        )
        is None
    )
    with pytest.raises(OptimisticConcurrencyError, match="dedupe_binding_conflict"):
        store.get_by_dedupe(
            tenant_id="tenant-a",
            workflow_id="workflow-other",
            run_id="run-1",
            dedupe_key="delivery-1",
        )

    messages = store.outbox.list_messages(tenant_id="tenant-a")
    assert len(messages) == 1
    assert messages[0].payload["event_id"] == "event-1"
    assert store.outbox.list_messages(tenant_id="tenant-b") == ()

    claimed = store.outbox.claim_batch(tenant_id="tenant-a", consumer_id="publisher-1", now=100, lease_seconds=10)
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


@pytest.mark.parametrize("field_name", ("tenant_id", "run_id"))
@pytest.mark.parametrize(
    ("invalid_value", "reason_suffix"),
    (
        (7, "not_canonical"),
        (" tenant-a", "not_canonical"),
        ("x" * 161, "too_long"),
    ),
    ids=("non-string", "whitespace-alias", "overlong"),
)
def test_event_store_implementations_share_strict_list_identity_semantics(
    runtime_engine,
    tmp_path,
    field_name: str,
    invalid_value,
    reason_suffix: str,
) -> None:
    sqlite_store = SQLiteEventStore(tmp_path / "events.sqlite")
    stores = (
        InMemoryEventStore(),
        sqlite_store,
        SQLAlchemyEventStore(runtime_engine),
    )
    candidate = _event(dedupe="identity-delivery", event_id="identity-event")
    expected_reason = f"{field_name}_{reason_suffix}"
    observed_errors: list[tuple[type[Exception], str, str]] = []

    try:
        for store in stores:
            stored = store.append(candidate, expected_sequence=0)
            assert store.list_events(tenant_id="tenant-a", run_id="run-1") == (stored,)

            query = {"tenant_id": "tenant-a", "run_id": "run-1"}
            query[field_name] = invalid_value
            with pytest.raises(IdentityValidationError) as raised:
                store.list_events(**query)  # type: ignore[arg-type]
            observed_errors.append(
                (
                    type(raised.value),
                    raised.value.reason_code,
                    raised.value.field_name,
                )
            )
    finally:
        sqlite_store.close()

    assert observed_errors == [
        (IdentityValidationError, expected_reason, field_name),
    ] * len(stores)


@pytest.mark.parametrize("field_name", ("tenant_id", "workflow_id", "run_id"))
@pytest.mark.parametrize(
    ("invalid_value", "reason_suffix"),
    (
        (7, "not_canonical"),
        (" workflow-1", "not_canonical"),
        ("x" * 161, "too_long"),
    ),
    ids=("non-string", "whitespace-alias", "overlong"),
)
def test_legacy_event_adapter_preserves_strict_canonical_identity_policy(
    field_name: str,
    invalid_value,
    reason_suffix: str,
) -> None:
    raw = {
        "event_id": "legacy-event-1",
        "event_type": "step_completed",
        "workflow_id": "workflow-1",
        "timestamp": 100,
        "details": {"step_id": "step-1"},
    }
    arguments = {
        "tenant_id": "tenant-a",
        "run_id": "run-1",
        "correlation_id": "correlation-1",
        "causation_id": "causation-1",
    }
    if field_name == "workflow_id":
        raw[field_name] = invalid_value
    else:
        arguments[field_name] = invalid_value

    with pytest.raises(ContractValidationError) as raised:
        LegacyWorkflowBackendEventAdapter.adapt(raw, **arguments)  # type: ignore[arg-type]

    assert [(issue.code, issue.path) for issue in raised.value.issues] == [
        (f"{field_name}_{reason_suffix}", field_name)
    ]


def test_read_model_repositories_share_exact_fail_closed_identity_semantics(runtime_engine) -> None:
    record = WorkflowRuntimeOperationRecord.from_mapping(
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "workflow_id": "workflow-a",
            "task_id": "task-a",
            "runtime": "ananta-native",
            "mode": "delegated",
            "status": "running",
            "source_sequence": 1,
            "updated_at": 100.0,
        }
    )
    repositories = (
        InMemoryWorkflowRuntimeReadModelRepository(),
        SQLAlchemyWorkflowRuntimeReadModelRepository(runtime_engine),
    )

    for repository in repositories:
        repository.upsert(record)
        assert repository.get(tenant_id="tenant-a", run_id="run-a") is not None

        with pytest.raises(IdentityValidationError) as tenant_alias:
            repository.get(tenant_id=" tenant-a", run_id="run-a")
        assert tenant_alias.value.reason_code == "tenant_id_not_canonical"

        with pytest.raises(IdentityValidationError) as run_type:
            repository.get(tenant_id="tenant-a", run_id=7)  # type: ignore[arg-type]
        assert run_type.value.reason_code == "run_id_not_canonical"

        with pytest.raises(IdentityValidationError) as tenant_list_alias:
            repository.list_for_tenant(tenant_id="tenant-a ")
        assert tenant_list_alias.value.reason_code == "tenant_id_not_canonical"

        with pytest.raises(IdentityValidationError) as direct_record:
            repository.upsert(replace(record, run_id=7))  # type: ignore[arg-type]
        assert direct_record.value.reason_code == "run_id_not_canonical"


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
    exact_checkpoint_query = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "checkpoint_id": "cp-1",
    }
    history_snapshot = store.list_history(
        tenant_id="tenant-a",
        run_id="run-1",
        task_id="task-1",
    )
    exact_checkpoint = store.get_by_id(**exact_checkpoint_query)
    assert exact_checkpoint == first
    assert exact_checkpoint is not None
    exact_checkpoint.state.business_data["revision"] = 999
    assert store.get_by_id(**exact_checkpoint_query) == first
    assert (
        store.get_by_id(
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
            store.get_by_id(**query)
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
            store.get_by_id(**{**exact_checkpoint_query, field_name: invalid})
    assert (
        store.list_history(
            tenant_id="tenant-a",
            run_id="run-1",
            task_id="task-1",
        )
        == history_snapshot
    )
    assert store.list_history(tenant_id="tenant-a", run_id="run-1", task_id="task-1") == (
        first,
        second,
    )
    with pytest.raises(OptimisticConcurrencyError, match="revision_conflict"):
        store.save(_checkpoint(revision=3, fence=4, checkpoint_id="cp-3"), expected_revision=1)
    with pytest.raises(FencingTokenError, match="stale"):
        store.save(_checkpoint(revision=3, fence=3, checkpoint_id="cp-stale"), expected_revision=2)


def test_authorization_grant_exact_read_port_has_memory_sql_binding_parity(
    runtime_engine,
) -> None:
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=HmacKeyRing({"key": "x" * 32}, active_key_id="key"),
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="f" * 64,
        policy_version="policy-1",
        now=100,
        ttl_seconds=300,
        envelope_id="rae-grant-1",
        nonce="nonce-grant-1",
    )
    factories = (
        lambda clock: InMemoryWorkflowAuthorizationGrantService(clock=clock),
        lambda clock: SQLAlchemyWorkflowAuthorizationGrantService(
            runtime_engine,
            clock=clock,
        ),
    )
    for factory in factories:
        clock_calls: list[float] = []

        def clock() -> float:
            clock_calls.append(101.0)
            return 101.0

        service = factory(clock)
        stored = service.grant(envelope)
        grant_clock_calls = tuple(clock_calls)
        exact = {
            "tenant_id": "tenant-a",
            "workflow_id": "workflow-1",
            "run_id": "run-1",
            "step_id": "step-1",
            "envelope_id": "rae-grant-1",
        }
        assert service.get(**exact) == stored
        assert service.get(**{**exact, "envelope_id": "rae-missing"}) is None
        for mismatch in (
            {"tenant_id": "tenant-other"},
            {"workflow_id": "workflow-other"},
            {"run_id": "run-other"},
            {"step_id": "step-other"},
        ):
            with pytest.raises(RuntimeError, match="grant_binding_conflict"):
                service.get(**{**exact, **mismatch})
        for field_name, invalid in (
            ("tenant_id", True),
            ("workflow_id", 7),
            ("run_id", " run-1"),
            ("step_id", False),
            ("envelope_id", " rae-grant-1"),
            ("envelope_id", "rae-grant-1\x00"),
            ("envelope_id", "x" * 257),
        ):
            with pytest.raises((IdentityValidationError, ValueError)):
                service.get(**{**exact, field_name: invalid})
        assert tuple(clock_calls) == grant_clock_calls
        assert service.get(**exact).revision == stored.revision

        revoked = service.revoke(
            envelope.envelope_id,
            reason_code="policy_revoked",
            expected_revision=stored.revision,
        )
        assert service.get(**exact) == revoked


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
    assert (
        ledger.plan(
            tenant_id="tenant-a",
            workflow_id="workflow-1",
            run_id="run-1",
            step_id="step-1",
            declared_operation="git.push:origin/main",
            side_effect_class="non_idempotent_write",
        )
        == planned
    )
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
    assert [
        item.fencing_token for item in store.list_history(tenant_id="tenant-a", run_id="run-1", step_id="step-1")
    ] == [1, 2, 2]

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
    assert (
        store.reconcile(
            tenant_id="tenant-a",
            run_id="provider-run",
            policy_version="policy-v1",
            reservation_id="provider-call-1",
            actual_total_tokens=25,
        )
        == reconciled
    )

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


def test_provider_budget_rejects_oversized_first_reservation_without_poisoning_run(
    runtime_engine,
) -> None:
    store = SQLAlchemyProviderBudgetStore(runtime_engine)
    limits = ProviderBudgetLimits(
        maximum_attempts=2,
        maximum_tokens=100,
        maximum_cost_micros=1_000,
    )

    with pytest.raises(
        ProviderBudgetError,
        match="provider_token_budget_exceeded",
    ):
        store.reserve(
            tenant_id="tenant-a",
            run_id="oversized-first-run",
            policy_version="policy-v1",
            reservation_id="oversized-first",
            limits=limits,
            reserved_tokens=101,
            reserved_cost_micros=100,
        )

    valid = SQLAlchemyProviderBudgetStore(runtime_engine).reserve(
        tenant_id="tenant-a",
        run_id="oversized-first-run",
        policy_version="policy-v1",
        reservation_id="valid-after-denial",
        limits=limits,
        reserved_tokens=100,
        reserved_cost_micros=100,
    )
    assert valid.attempts == 1
    assert valid.tokens == 100


def test_sql_profile_attempt_and_aggregate_reservation_commit_atomically(
    runtime_engine,
) -> None:
    store = SQLAlchemyProviderBudgetStore(runtime_engine)
    limits = ProviderBudgetLimits(
        maximum_attempts=0,
        maximum_tokens=2,
        maximum_cost_micros=1_000,
    )
    phi_scope = ProviderAttemptScope(
        scope_id="profile-scope-phi",
        maximum_attempts=1,
    )
    failed_phi = ProviderProfileAttemptReservation(
        current=phi_scope,
        reservation_id="phi-aggregate-denied",
    )

    with pytest.raises(
        ProviderBudgetError,
        match="provider_token_budget_exceeded",
    ):
        store.reserve(
            tenant_id="tenant-a",
            run_id="atomic-profile-run",
            policy_version="policy-v1",
            reservation_id="aggregate-phi-denied",
            limits=limits,
            reserved_tokens=3,
            reserved_cost_micros=0,
            profile_attempt=failed_phi,
        )

    locked_gemma = ProviderProfileAttemptReservation(
        current=ProviderAttemptScope(
            scope_id="profile-scope-gemma",
            maximum_attempts=1,
        ),
        reservation_id="gemma-still-locked",
        predecessors=(phi_scope,),
    )
    with pytest.raises(
        ProviderBudgetError,
        match="provider_attempt_plan_sequence_denied",
    ):
        store.reserve(
            tenant_id="tenant-a",
            run_id="atomic-profile-run",
            policy_version="policy-v1",
            reservation_id="aggregate-gemma-locked",
            limits=limits,
            reserved_tokens=1,
            reserved_cost_micros=0,
            profile_attempt=locked_gemma,
        )

    valid_phi = ProviderProfileAttemptReservation(
        current=phi_scope,
        reservation_id="phi-valid",
    )
    first = store.reserve(
        tenant_id="tenant-a",
        run_id="atomic-profile-run",
        policy_version="policy-v1",
        reservation_id="aggregate-phi-valid",
        limits=limits,
        reserved_tokens=1,
        reserved_cost_micros=0,
        profile_attempt=valid_phi,
    )
    replay = SQLAlchemyProviderBudgetStore(runtime_engine).reserve(
        tenant_id="tenant-a",
        run_id="atomic-profile-run",
        policy_version="policy-v1",
        reservation_id="aggregate-phi-valid",
        limits=limits,
        reserved_tokens=1,
        reserved_cost_micros=0,
        profile_attempt=valid_phi,
    )
    assert first == replay
    assert replay.profile_attempts == 1
    assert replay.attempts == 1
    assert replay.tokens == 1

    valid_gemma = replace(
        locked_gemma,
        reservation_id="gemma-after-valid-phi",
    )
    fallback = store.reserve(
        tenant_id="tenant-a",
        run_id="atomic-profile-run",
        policy_version="policy-v1",
        reservation_id="aggregate-gemma-valid",
        limits=limits,
        reserved_tokens=1,
        reserved_cost_micros=0,
        profile_attempt=valid_gemma,
    )
    assert fallback.profile_attempts == 1
    assert fallback.attempts == 2
    assert fallback.tokens == 2


def test_sql_concurrent_profile_reservations_consume_exactly_one_slot(
    runtime_engine,
) -> None:
    limits = ProviderBudgetLimits(
        maximum_attempts=0,
        maximum_tokens=10,
        maximum_cost_micros=1_000,
    )
    scope = ProviderAttemptScope(
        scope_id="concurrent-profile-scope",
        maximum_attempts=1,
    )

    def reserve(index: int):
        profile_attempt = ProviderProfileAttemptReservation(
            current=scope,
            reservation_id=f"concurrent-profile-{index}",
        )
        try:
            snapshot = SQLAlchemyProviderBudgetStore(runtime_engine).reserve(
                tenant_id="tenant-a",
                run_id="concurrent-profile-run",
                policy_version="policy-v1",
                reservation_id=f"concurrent-aggregate-{index}",
                limits=limits,
                reserved_tokens=1,
                reserved_cost_micros=0,
                profile_attempt=profile_attempt,
            )
            return index, snapshot
        except ProviderBudgetError as exc:
            return index, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, range(2)))

    successes = [
        (index, value)
        for index, value in results
        if not isinstance(value, ProviderBudgetError)
    ]
    failures = [
        value
        for _index, value in results
        if isinstance(value, ProviderBudgetError)
    ]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].reason_code in {
        "provider_retry_budget_exceeded",
        "provider_budget_concurrent_update",
    }
    winner_index, winner = successes[0]
    assert winner.profile_attempts == 1
    assert winner.attempts == 1
    assert winner.tokens == 1

    winner_attempt = ProviderProfileAttemptReservation(
        current=scope,
        reservation_id=f"concurrent-profile-{winner_index}",
    )
    replay = SQLAlchemyProviderBudgetStore(runtime_engine).reserve(
        tenant_id="tenant-a",
        run_id="concurrent-profile-run",
        policy_version="policy-v1",
        reservation_id=f"concurrent-aggregate-{winner_index}",
        limits=limits,
        reserved_tokens=1,
        reserved_cost_micros=0,
        profile_attempt=winner_attempt,
    )
    assert replay.attempts == 1
    assert replay.tokens == 1
    assert replay.profile_attempts == 1


def test_sql_scoped_node_budget_is_atomic_concurrent_and_replay_safe(
    runtime_engine,
) -> None:
    run_limits = ProviderBudgetLimits(
        maximum_attempts=0,
        maximum_tokens=100,
        maximum_cost_micros=1_000,
    )
    node_budget = ProviderScopedBudgetReservation(
        scope_id="provider-node-scope",
        limits=ProviderBudgetLimits(
            maximum_attempts=0,
            maximum_tokens=30,
            maximum_cost_micros=300,
        ),
    )

    def reserve(index: int):
        try:
            snapshot = SQLAlchemyProviderBudgetStore(
                runtime_engine
            ).reserve(
                tenant_id="tenant-a",
                run_id="scoped-provider-run",
                policy_version="policy-v1",
                reservation_id=f"scoped-call-{index}",
                limits=run_limits,
                reserved_tokens=20,
                reserved_cost_micros=150,
                scoped_budget=node_budget,
            )
            return index, snapshot
        except ProviderBudgetError as exc:
            return index, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, range(2)))

    successes = [
        (index, value)
        for index, value in results
        if not isinstance(value, ProviderBudgetError)
    ]
    failures = [
        value
        for _index, value in results
        if isinstance(value, ProviderBudgetError)
    ]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].reason_code in {
        "provider_token_budget_exceeded",
        "provider_budget_concurrent_update",
    }

    winner_index, winner = successes[0]
    replay = SQLAlchemyProviderBudgetStore(runtime_engine).reserve(
        tenant_id="tenant-a",
        run_id="scoped-provider-run",
        policy_version="policy-v1",
        reservation_id=f"scoped-call-{winner_index}",
        limits=run_limits,
        reserved_tokens=20,
        reserved_cost_micros=150,
        scoped_budget=node_budget,
    )
    assert replay == winner
    assert replay.attempts == 1
    assert replay.tokens == 20

    final = SQLAlchemyProviderBudgetStore(runtime_engine).reserve(
        tenant_id="tenant-a",
        run_id="scoped-provider-run",
        policy_version="policy-v1",
        reservation_id="scoped-call-final",
        limits=run_limits,
        reserved_tokens=10,
        reserved_cost_micros=150,
        scoped_budget=node_budget,
    )
    assert final.attempts == 2
    assert final.tokens == 30
    assert final.cost_micros == 300


def test_sql_legacy_run_only_reservation_requires_a_drain_before_scoped_rollout(
    runtime_engine,
) -> None:
    store = SQLAlchemyProviderBudgetStore(runtime_engine)
    run_limits = ProviderBudgetLimits(
        maximum_attempts=0,
        maximum_tokens=100,
        maximum_cost_micros=1_000,
    )
    store.reserve(
        tenant_id="tenant-a",
        run_id="legacy-in-flight-run",
        policy_version="policy-v1",
        reservation_id="legacy-in-flight-call",
        limits=run_limits,
        reserved_tokens=20,
        reserved_cost_micros=100,
    )
    node_budget = ProviderScopedBudgetReservation(
        scope_id="legacy-missing-node-scope",
        limits=ProviderBudgetLimits(
            maximum_attempts=0,
            maximum_tokens=30,
            maximum_cost_micros=300,
        ),
    )

    with pytest.raises(
        ProviderBudgetError,
        match="provider_scoped_budget_migration_required",
    ):
        store.reserve(
            tenant_id="tenant-a",
            run_id="legacy-in-flight-run",
            policy_version="policy-v1",
            reservation_id="legacy-in-flight-call",
            limits=run_limits,
            reserved_tokens=20,
            reserved_cost_micros=100,
            scoped_budget=node_budget,
        )
    with pytest.raises(
        ProviderBudgetError,
        match="provider_scoped_budget_migration_required",
    ):
        store.reconcile(
            tenant_id="tenant-a",
            run_id="legacy-in-flight-run",
            policy_version="policy-v1",
            reservation_id="legacy-in-flight-call",
            actual_total_tokens=15,
            scoped_budget=node_budget,
        )


def test_provider_budget_persists_token_cost_aggregate_with_attempts_neutral(
    runtime_engine,
) -> None:
    """Profile attempts live in scoped ownership ledgers, not this aggregate."""

    limits = ProviderBudgetLimits(
        maximum_attempts=0,
        maximum_tokens=100,
        maximum_cost_micros=1_000,
    )
    first = SQLAlchemyProviderBudgetStore(runtime_engine).reserve(
        tenant_id="tenant-a",
        run_id="shared-provider-run",
        policy_version="policy-v1",
        reservation_id="step-a-phi-1",
        limits=limits,
        reserved_tokens=20,
        reserved_cost_micros=100,
    )
    second_store = SQLAlchemyProviderBudgetStore(runtime_engine)
    second = second_store.reserve(
        tenant_id="tenant-a",
        run_id="shared-provider-run",
        policy_version="policy-v1",
        reservation_id="step-b-gemma-1",
        limits=limits,
        reserved_tokens=30,
        reserved_cost_micros=200,
    )
    duplicate = SQLAlchemyProviderBudgetStore(runtime_engine).reserve(
        tenant_id="tenant-a",
        run_id="shared-provider-run",
        policy_version="policy-v1",
        reservation_id="step-b-gemma-1",
        limits=limits,
        reserved_tokens=30,
        reserved_cost_micros=200,
    )

    assert first.attempts == 1
    assert second == duplicate
    assert second.attempts == 2
    assert second.tokens == 50
    assert second.cost_micros == 300
    assert second.limits.maximum_attempts == 0


def test_gateway_profile_sequence_persists_across_sql_store_instances(
    runtime_engine,
) -> None:
    now = time.time()
    key_ring = HmacKeyRing(
        {"key-1": b"x" * 32},
        active_key_id="key-1",
    )
    profile_bindings = tuple(
        ProviderProfileExecutionBinding(
            profile_id=profile_id,
            binding=ProviderExecutionBinding(
                provider_id="ollama",
                model_id=model_id,
                source="hub_model_profile_routing",
                reason_code="hub_provider_profile_selected",
            ),
        )
        for profile_id, model_id in (
            ("phi-primary", "phi4-mini:latest"),
            ("gemma-fallback", "gemma4:e4b-it-qat"),
        )
    )
    plan = tuple(
        ProviderProfileAttemptPlanEntry.from_profile_binding(
            binding,
            maximum_attempts=maximum,
        )
        for binding, maximum in zip(
            profile_bindings,
            (2, 1),
            strict=True,
        )
    )
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=key_ring,
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="sequence-run",
        step_id="step-1",
        plan_hash="a" * 64,
        policy_version="policy-v1",
        allowed_provider_bindings=tuple(
            entry.binding_authorization for entry in plan
        ),
        provider_attempt_plan=plan,
        budgets={
            "attempts": 1,
            "provider_attempts": 3,
            "tokens": 100,
            "cost_micros": 1_000,
        },
        now=now,
        ttl_seconds=600,
    )
    grants = InMemoryWorkflowAuthorizationGrantService(clock=lambda: now + 1)
    grants.grant(envelope)
    ownership = SQLAlchemyExecutionOwnershipStore(runtime_engine)
    claim = ownership.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="sequence-run",
        step_id="step-1",
        owner_id="hub-native:sequence-run:step-1",
        lease_seconds=300,
        maximum_retries=0,
        now=now,
    )
    base = {
        "schema": WORKFLOW_WORKER_COMMAND_SCHEMA,
        "binding": {
            "tenant_id": "tenant-a",
            "workflow_id": "workflow-1",
            "run_id": "sequence-run",
            "step_id": "step-1",
            "plan_hash": "a" * 64,
            "policy_version": "policy-v1",
            "authorization_envelope": envelope.to_dict(),
        },
        "attempt_id": claim.ownership.attempt_id,
        "fencing_token": claim.ownership.fencing_token,
    }

    def gateway() -> WorkflowWorkerGatewayService:
        return WorkflowWorkerGatewayService(
            authorization=AuthorizationVerifier(key_ring),
            ownership=SQLAlchemyExecutionOwnershipStore(runtime_engine),
            ledger=InMemorySideEffectLedger(),
            events=InMemoryEventStore(),
            provider_budgets=SQLAlchemyProviderBudgetStore(runtime_engine),
            authorization_revalidator=grants,
            clock=lambda: now + 1,
        )

    def reserve(
        entry: ProviderProfileAttemptPlanEntry,
        reservation_id: str,
    ) -> dict:
        return {
            **base,
            "command": "provider_budget_reserve",
            "reservation_id": reservation_id,
            "maximum_attempts": 3,
            "maximum_tokens": 100,
            "maximum_cost_micros": 1_000,
            "reserved_tokens": 1,
            "reserved_cost_micros": 0,
            "provider_profile_id": entry.profile_id,
            "provider_binding_id": entry.binding_id,
            "provider_id": entry.provider_id,
            "model_id": entry.model_id,
        }

    phi, gemma = plan
    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="provider_attempt_plan_sequence_denied",
    ):
        gateway().execute(reserve(gemma, "gemma-first"))

    gateway().execute(reserve(phi, "phi-1"))
    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="provider_attempt_plan_sequence_denied",
    ):
        gateway().execute(reserve(gemma, "gemma-interleaved"))

    gateway().execute(reserve(phi, "phi-2"))
    fallback = gateway().execute(reserve(gemma, "gemma-after-phi"))

    assert fallback["attempts"] == 1
    assert fallback["maximum_attempts"] == 1
