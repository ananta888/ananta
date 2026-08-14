from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from agent.db_models.workflow_runtime import (
    WorkflowControlBindingDB,
    WorkflowControlCommandReceiptDB,
    WorkflowTransitionEffectDB,
    WorkflowTransitionOutboxDB,
)
from agent.services.workflow_backend import WORKFLOW_STATUS_SCHEMA
from agent.services.workflow_control_command_receipt_persistence import (
    SQLAlchemyWorkflowControlCommandReceiptStore,
)
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandReceiptError,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_QUEUE_RESERVE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_READY,
    TRANSITION_STATE_REJECTED,
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_result_digest,
    workflow_transition_id,
    workflow_transition_outcome_fingerprint,
)
from agent.services.workflow_transition_persistence import (
    InMemoryWorkflowTransitionStore,
    SQLAlchemyWorkflowTransitionStore,
    WorkflowTransitionPersistenceError,
)
from agent.services.workflow_transition_public_projection import (
    WorkflowTransitionPublicStatusProjector,
)


@dataclass
class _Clock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value


class _IdentityReceiptProjector:
    def project(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs["binding_status"])


_IDENTITY_PROJECTOR = _IdentityReceiptProjector()


@dataclass
class _Harness:
    kind: str
    store: Any
    clock: _Clock
    engine: Engine | None = None
    receipt_projector: Any = _IDENTITY_PROJECTOR

    def adapter(self, *, independent_engine: bool = False) -> Any:
        if self.engine is None:
            return self.store
        engine = self.engine
        if independent_engine:
            engine = sa.create_engine(
                str(self.engine.url),
                connect_args={"check_same_thread": False, "timeout": 30.0},
            )
        return SQLAlchemyWorkflowTransitionStore(
            engine,
            clock=self.clock,
            receipt_projector=self.receipt_projector,
        )


def _plan() -> tuple[WorkflowTransition, tuple[WorkflowTransitionEffect, ...]]:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_COMMAND,
        identity_key="command-a",
    )
    effects = (
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=1,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key="task-a",
            payload={"task_id": "task-a", "attempt_id": "attempt-a"},
            created_at=1_000.0,
        ),
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=2,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key="workflow-a",
            payload={"workflow_id": "workflow-a"},
            created_at=1_000.0,
        ),
    )
    return (
        WorkflowTransition.build(
            transition_id=transition_id,
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            kind=TRANSITION_KIND_COMMAND,
            command_id="command-a",
            receipt_id="command-a",
            admitted_command={"command_id": "command-a", "kind": "advance"},
            request_payload={"command": "advance"},
            effects=effects,
            expected_revision=7,
            expected_checkpoint_ref="checkpoint-7",
            created_at=1_000.0,
        ),
        effects,
    )


def _create_sql_engine(path: str) -> Engine:
    engine = sa.create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    for table in (
        WorkflowControlBindingDB.__table__,
        WorkflowControlCommandReceiptDB.__table__,
        WorkflowTransitionOutboxDB.__table__,
        WorkflowTransitionEffectDB.__table__,
    ):
        table.create(engine)
    return engine


def _seed_sql(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        session.add(
            WorkflowControlBindingDB(
                id="workflow-a",
                tenant_id="tenant-a",
                subject_id="subject-a",
                workflow_id="workflow-a",
                run_id="run-a",
                runtime_id="local",
                plan_hash="f" * 64,
                policy_version="policy-v1",
                checkpoint_id="checkpoint-id-7",
                workflow_request={
                    "workflow_id": "workflow-a",
                    "correlation_id": "correlation-a",
                    "requested_by": "subject-a",
                    "steps": [],
                },
                execution_plan={},
                last_status={
                    "status": "running",
                    "revision": 7,
                    "checkpoint_ref": "checkpoint-7",
                },
                public_status={},
                runtime_revision=7,
                runtime_checkpoint_ref="checkpoint-7",
                command_receipt_id="command-a",
                created_at=999.0,
                updated_at=999.0,
            )
        )
        session.add(
            WorkflowControlCommandReceiptDB(
                id="command-a",
                tenant_id="tenant-a",
                workflow_id="workflow-a",
                run_id="run-a",
                actor_id="actor-a",
                command_type="advance",
                request_payload={"command": "advance"},
                expected_revision=7,
                checkpoint_ref="checkpoint-7",
                state="pending",
                created_at=999.0,
                updated_at=999.0,
            )
        )


def _harness(
    kind: str,
    tmp_path: Any,
    *,
    name: str,
    fault_injector: Any = None,
    receipt_projector: Any = _IDENTITY_PROJECTOR,
) -> _Harness:
    clock = _Clock()
    if kind == "memory":
        store = InMemoryWorkflowTransitionStore(
            clock=clock,
            fault_injector=fault_injector,
            receipt_projector=receipt_projector,
        )
        store.put_binding(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            runtime_id="local",
            runtime_revision=7,
            runtime_checkpoint_ref="checkpoint-7",
            last_status={
                "status": "running",
                "revision": 7,
                "checkpoint_ref": "checkpoint-7",
            },
            command_receipt_id="command-a",
            public_status={},
            subject_id="subject-a",
            plan_hash="f" * 64,
            policy_version="policy-v1",
            checkpoint_id="checkpoint-id-7",
            workflow_request={
                "workflow_id": "workflow-a",
                "correlation_id": "correlation-a",
                "requested_by": "subject-a",
                "steps": [],
            },
            execution_plan={},
        )
        store.put_receipt(
            receipt_id="command-a",
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            expected_revision=7,
            checkpoint_ref="checkpoint-7",
            request_payload={"command": "advance"},
        )
        return _Harness(kind, store, clock, receipt_projector=receipt_projector)

    engine = _create_sql_engine(str(tmp_path / f"{name}.db"))
    _seed_sql(engine)
    return _Harness(
        kind,
        SQLAlchemyWorkflowTransitionStore(
            engine,
            clock=clock,
            fault_injector=fault_injector,
            receipt_projector=receipt_projector,
        ),
        clock,
        engine,
        receipt_projector,
    )


def _records(harness: _Harness) -> tuple[dict[str, Any], dict[str, Any]]:
    if harness.engine is None:
        return (
            harness.store.binding_record("workflow-a"),
            harness.store.receipt_record("command-a"),
        )
    with Session(harness.engine) as session:
        binding = session.get(WorkflowControlBindingDB, "workflow-a")
        receipt = session.get(WorkflowControlCommandReceiptDB, "command-a")
        assert binding is not None and receipt is not None
        return (
            {column.name: getattr(binding, column.name) for column in binding.__table__.columns},
            {column.name: getattr(receipt, column.name) for column in receipt.__table__.columns},
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_stage_is_atomic_adoptable_and_conflict_safe(kind: str, tmp_path: Any) -> None:
    harness = _harness(kind, tmp_path, name=f"stage-{kind}")
    transition, effects = _plan()

    staged = harness.store.stage(transition, effects, receipt_id="command-a")
    adopted = harness.adapter().stage(transition, effects, receipt_id="command-a")

    assert adopted == staged
    assert harness.store.get_active("workflow-a") == staged
    binding, receipt = _records(harness)
    assert binding["active_transition_id"] == transition.transition_id
    assert receipt["transition_id"] == transition.transition_id
    assert receipt["request_fingerprint"] == transition.request_fingerprint
    assert receipt["effect_fingerprint"] == transition.effect_fingerprint

    conflicting = WorkflowTransition.build(
        transition_id=transition.transition_id,
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        runtime_id=transition.runtime_id,
        kind=transition.kind,
        command_id=transition.command_id,
        receipt_id=transition.receipt_id,
        admitted_command={"command_id": "command-a", "kind": "advance"},
        request_payload={"command": "pause"},
        effects=effects,
        expected_revision=transition.expected_revision,
        expected_checkpoint_ref=transition.expected_checkpoint_ref,
        created_at=transition.created_at,
    )
    with pytest.raises(WorkflowTransitionPersistenceError, match="stage_conflict"):
        harness.store.stage(conflicting, effects, receipt_id="command-a")


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_stage_accepts_only_an_empty_or_exact_receipt_request_fingerprint(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"receipt-request-proof-{kind}")
    transition, effects = _plan()
    if harness.engine is None:
        getattr(harness.store, "_receipts")["command-a"]["request_fingerprint"] = (
            transition.request_fingerprint
        )
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowControlCommandReceiptDB)
                .where(WorkflowControlCommandReceiptDB.id == "command-a")
                .values(request_fingerprint=transition.request_fingerprint)
            )

    staged = harness.store.stage(transition, effects, receipt_id="command-a")

    assert staged.transition.request_fingerprint == transition.request_fingerprint


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("request_fingerprint", "f" * 64),
        ("transition_id", "different-transition"),
        ("effect_fingerprint", "e" * 64),
        ("outcome_fingerprint", "a" * 64),
    ],
)
def test_stage_rejects_divergent_or_preexisting_receipt_attribution(
    kind: str,
    field_name: str,
    value: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"receipt-proof-{kind}-{field_name}")
    transition, effects = _plan()
    if field_name == "request_fingerprint" and value == transition.request_fingerprint:
        pytest.fail("the divergent fixture unexpectedly matches the transition")
    if harness.engine is None:
        getattr(harness.store, "_receipts")["command-a"][field_name] = value
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowControlCommandReceiptDB)
                .where(WorkflowControlCommandReceiptDB.id == "command-a")
                .values({field_name: value})
            )

    with pytest.raises(WorkflowTransitionPersistenceError, match="receipt_stage_conflict"):
        harness.store.stage(transition, effects, receipt_id="command-a")

    assert harness.store.get(transition.transition_id) is None
    binding, receipt = _records(harness)
    assert binding["active_transition_id"] == ""
    assert receipt[field_name] == value


def test_attributed_sql_receipt_is_inaccessible_to_every_legacy_mutation(
    tmp_path: Any,
) -> None:
    harness = _harness("sql", tmp_path, name="legacy-receipt-exclusion")
    assert harness.engine is not None
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    legacy = SQLAlchemyWorkflowControlCommandReceiptStore(
        harness.engine,
        clock=harness.clock,
    )

    persisted = legacy.get("command-a")
    assert persisted is not None and persisted.transition_id == transition.transition_id
    assert legacy.list_pending() == ()
    operations = (
        lambda: legacy.claim("command-a", owner_id="legacy-owner"),
        lambda: legacy.heartbeat(
            "command-a",
            owner_id="legacy-owner",
            dispatch_generation=0,
        ),
        lambda: legacy.release(
            "command-a",
            owner_id="legacy-owner",
            dispatch_generation=0,
        ),
        lambda: legacy.complete(
            "command-a",
            status={"revision": 99, "status": "completed"},
            owner_id="legacy-owner",
            dispatch_generation=0,
        ),
        lambda: legacy.reject(
            "command-a",
            reason_code="legacy_rejection",
            owner_id="legacy-owner",
            dispatch_generation=0,
        ),
    )
    for operation in operations:
        with pytest.raises(
            WorkflowControlCommandReceiptError,
            match="workflow_control_command_transition_pending",
        ):
            operation()

    _binding, receipt = _records(harness)
    assert receipt["state"] == "pending"
    assert receipt["transition_id"] == transition.transition_id


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    "fault_stage",
    [
        "stage_after_transition",
        "stage_after_effects",
        "stage_before_binding_cas",
        "stage_after_binding_cas",
    ],
)
def test_stage_fault_seams_roll_back_the_whole_aggregate(
    kind: str,
    fault_stage: str,
    tmp_path: Any,
) -> None:
    def inject(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(f"injected:{stage}")

    harness = _harness(
        kind,
        tmp_path,
        name=f"stage-fault-{kind}-{fault_stage}",
        fault_injector=inject,
    )
    transition, effects = _plan()

    with pytest.raises(RuntimeError, match=f"injected:{fault_stage}"):
        harness.store.stage(transition, effects, receipt_id="command-a")

    assert harness.store.get(transition.transition_id) is None
    binding, receipt = _records(harness)
    assert binding["active_transition_id"] == ""
    assert receipt["transition_id"] == ""
    assert receipt["request_fingerprint"] == ""


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_stage_rejects_live_and_expired_legacy_dispatching_receipts(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"legacy-receipt-lease-{kind}")
    transition, effects = _plan()
    if harness.engine is None:
        receipts = getattr(harness.store, "_receipts")
        receipts["command-a"].update(
            state="dispatching",
            dispatch_owner="legacy-reconciler",
            dispatch_lease_expires_at=1_010.0,
            revision=2,
        )
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowControlCommandReceiptDB)
                .where(WorkflowControlCommandReceiptDB.id == "command-a")
                .values(
                    state="dispatching",
                    dispatch_owner="legacy-reconciler",
                    dispatch_lease_expires_at=1_010.0,
                    revision=2,
                )
            )

    with pytest.raises(
        WorkflowTransitionPersistenceError,
        match="receipt_stage_conflict",
    ):
        harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.get(transition.transition_id) is None

    harness.clock.value = 1_011.0
    with pytest.raises(
        WorkflowTransitionPersistenceError,
        match="receipt_stage_conflict",
    ):
        harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.get(transition.transition_id) is None
    _binding, receipt = _records(harness)
    assert receipt["state"] == "dispatching"
    assert receipt["dispatch_owner"] == "legacy-reconciler"


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_same_owner_claim_and_same_generation_effect_begin_are_single_winner(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"same-owner-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    barrier = Barrier(2)
    claimers = (
        harness.store,
        harness.adapter(independent_engine=kind == "sql"),
    )

    def claim(store: Any) -> Any:
        barrier.wait()
        return store.claim(
            transition.transition_id,
            owner_id="drain-process-a",
            lease_seconds=10.0,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, claimers))
    winners = [value for value in claims if value is not None]
    assert len(winners) == 1
    assert winners[0].transition.claim_generation == 1

    begin_barrier = Barrier(2)

    def begin(store: Any) -> str:
        begin_barrier.wait()
        try:
            store.begin_effect(
                transition.transition_id,
                effects[0].effect_id,
                owner_id="drain-process-a",
                claim_generation=1,
            )
        except WorkflowTransitionPersistenceError as exc:
            return str(exc)
        return "won"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(begin, claimers))
    assert sorted(results) == ["won", "workflow_transition_effect_generation_conflict"]


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_attributed_receipt_lease_exactly_mirrors_transition(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"receipt-lease-mirror-{kind}")
    transition, effects = _plan()
    staged = harness.store.stage(transition, effects, receipt_id="command-a")
    _binding, receipt = _records(harness)
    assert receipt["state"] == "pending"
    assert receipt["dispatch_generation"] == staged.transition.claim_generation == 0

    claimed = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=10.0,
    )
    assert claimed is not None
    _binding, receipt = _records(harness)
    assert receipt["state"] == "dispatching"
    assert receipt["dispatch_owner"] == claimed.transition.claim_owner
    assert receipt["dispatch_generation"] == claimed.transition.claim_generation
    assert receipt["dispatch_lease_expires_at"] == claimed.transition.claim_expires_at
    assert receipt["last_heartbeat_at"] == claimed.transition.last_heartbeat_at

    harness.clock.value = 1_005.0
    heartbeat = harness.store.heartbeat(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=1,
        lease_seconds=20.0,
    )
    _binding, receipt = _records(harness)
    assert receipt["dispatch_lease_expires_at"] == heartbeat.transition.claim_expires_at
    assert receipt["last_heartbeat_at"] == heartbeat.transition.last_heartbeat_at

    released = harness.store.release(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=1,
        reason_code="retryable_queue_error",
        retry_at=1_005.0,
    )
    _binding, receipt = _records(harness)
    assert receipt["state"] == "pending"
    assert receipt["dispatch_owner"] == released.transition.claim_owner == ""
    assert receipt["dispatch_generation"] == released.transition.claim_generation == 1
    assert receipt["dispatch_lease_expires_at"] == released.transition.claim_expires_at == 0.0
    assert receipt["last_heartbeat_at"] == released.transition.last_heartbeat_at


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_expired_lease_adoption_fences_the_old_generation(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"adoption-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    first = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=10.0,
    )
    assert first is not None
    started = harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    assert started.state == EFFECT_STATE_APPLYING
    assert started.applied_generation == 1

    harness.clock.value = 1_011.0
    adopted = harness.adapter(independent_engine=kind == "sql").claim(
        transition.transition_id,
        owner_id="owner-b",
        lease_seconds=20.0,
    )
    assert adopted is not None
    assert adopted.transition.claim_generation == 2
    assert adopted.transition.attempt_count == 2
    with pytest.raises(WorkflowTransitionPersistenceError, match="lease_conflict"):
        harness.store.heartbeat(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            lease_seconds=30.0,
        )
    with pytest.raises(WorkflowTransitionPersistenceError, match="lease_conflict"):
        harness.store.finish_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
            result_payload={"task_id": "stale"},
            result_digest=workflow_transition_effect_result_digest({"task_id": "stale"}),
        )

    resumed = harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-b",
        claim_generation=2,
    )
    assert resumed.applied_generation == 2
    with pytest.raises(
        WorkflowTransitionPersistenceError,
        match="effect_generation_conflict",
    ):
        harness.store.begin_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-b",
            claim_generation=2,
        )
    heartbeat = harness.store.heartbeat(
        transition.transition_id,
        owner_id="owner-b",
        claim_generation=2,
        lease_seconds=30.0,
    )
    assert heartbeat.transition.claim_expires_at == 1_041.0


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_effect_begin_requires_first_nonterminal_ordinal_and_reject_is_unambiguous(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"effect-order-{kind}")
    base, _base_effects = _plan()
    effects = (
        WorkflowTransitionEffect.build(
            transition_id=base.transition_id,
            ordinal=1,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key="task-a",
            payload={"task_id": "task-a"},
            created_at=1_000.0,
        ),
        WorkflowTransitionEffect.build(
            transition_id=base.transition_id,
            ordinal=2,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key="task-b",
            payload={"task_id": "task-b"},
            created_at=1_000.0,
        ),
        WorkflowTransitionEffect.build(
            transition_id=base.transition_id,
            ordinal=3,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key="workflow-a",
            payload={"workflow_id": "workflow-a"},
            created_at=1_000.0,
        ),
    )
    transition = WorkflowTransition.build(
        transition_id=base.transition_id,
        tenant_id=base.tenant_id,
        workflow_id=base.workflow_id,
        run_id=base.run_id,
        runtime_id=base.runtime_id,
        kind=base.kind,
        command_id=base.command_id,
        receipt_id=base.receipt_id,
        admitted_command={"command_id": "command-a", "kind": "advance"},
        request_payload={"command": "advance"},
        effects=effects,
        expected_revision=base.expected_revision,
        expected_checkpoint_ref=base.expected_checkpoint_ref,
        created_at=base.created_at,
    )
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )

    with pytest.raises(WorkflowTransitionPersistenceError, match="effect_order_conflict"):
        harness.store.begin_effect(
            transition.transition_id,
            effects[1].effect_id,
            owner_id="owner-a",
            claim_generation=1,
        )
    harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    with pytest.raises(WorkflowTransitionPersistenceError, match="effect_recovery_required"):
        harness.store.reject(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            reason_code="ambiguous_effect",
        )

    snapshot = harness.store.get(transition.transition_id)
    assert snapshot is not None and snapshot.transition.state == TRANSITION_STATE_APPLYING
    _binding, receipt = _records(harness)
    assert receipt["state"] == "dispatching"
    assert receipt["rejection_reason"] == ""


def _prepare_for_finalize(harness: _Harness) -> tuple[WorkflowTransition, str]:
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    claim = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    assert claim is not None
    harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    result = {"task_id": "task-a", "queue_state": "reserved"}
    harness.store.finish_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
    )
    snapshot = harness.store.get(transition.transition_id)
    assert snapshot is not None
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }
    outcome = workflow_transition_outcome_fingerprint(
        snapshot.transition,
        snapshot.effects,
        binding_status=status,
        checkpoint_ref="checkpoint-8",
        receipt_result=status,
    )
    return transition, outcome


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_finalize_publishes_exact_binding_receipt_and_effect_proofs(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"finalize-{kind}")
    transition, outcome = _prepare_for_finalize(harness)
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }

    with pytest.raises(
        WorkflowTransitionPersistenceError,
        match="receipt_projection_mismatch",
    ):
        harness.adapter().finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            outcome_fingerprint=outcome,
            receipt_result={**status, "status": "unrelated"},
        )

    completed = harness.adapter().finalize(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=1,
        binding_status=status,
        checkpoint_ref="checkpoint-8",
        outcome_fingerprint=outcome,
        receipt_result=status,
    )

    assert completed.transition.state == TRANSITION_STATE_COMPLETED
    assert completed.transition.result_status == status
    assert completed.effects[-1].state == EFFECT_STATE_APPLIED
    binding, receipt = _records(harness)
    assert binding["active_transition_id"] == ""
    assert binding["runtime_revision"] == 8
    assert binding["runtime_checkpoint_ref"] == "checkpoint-8"
    assert binding["last_transition_id"] == transition.transition_id
    assert binding["last_transition_request_fingerprint"] == transition.request_fingerprint
    assert binding["last_transition_effect_fingerprint"] == transition.effect_fingerprint
    assert binding["last_transition_outcome_fingerprint"] == outcome
    assert receipt["state"] == "completed"
    assert receipt["result_status"] == status
    assert receipt["transition_id"] == transition.transition_id
    assert receipt["outcome_fingerprint"] == outcome
    assert receipt["dispatch_generation"] == 1


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_finalize_atomically_persists_raw_native_and_canonical_public_status(
    kind: str,
    tmp_path: Any,
) -> None:
    projector = WorkflowTransitionPublicStatusProjector()
    harness = _harness(
        kind,
        tmp_path,
        name=f"raw-public-finalize-{kind}",
        receipt_projector=projector,
    )
    transition, _identity_outcome = _prepare_for_finalize(harness)
    snapshot = harness.store.get(transition.transition_id)
    assert snapshot is not None
    raw_status = {
        "schema": WORKFLOW_STATUS_SCHEMA,
        "backend": "local",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "plan_hash": "f" * 64,
        "status": "running",
        "revision": 8,
        "checkpoint_ref": f"wfc-{'a' * 32}",
        "steps": [],
        "updated_at": 1_001.0,
    }
    binding_before, _receipt_before = _records(harness)
    public_status = dict(
        projector.project(
            transition=snapshot.transition,
            binding=binding_before,
            binding_status=raw_status,
            previous_public_status=None,
        )
    )
    assert public_status["checkpoint_ref"] == "local:workflow-a:8"
    assert public_status["source_observation"]["backend"] == "local"
    outcome = workflow_transition_outcome_fingerprint(
        snapshot.transition,
        snapshot.effects,
        binding_status=raw_status,
        checkpoint_ref=raw_status["checkpoint_ref"],
        receipt_result=public_status,
    )

    missing_identity = dict(public_status)
    missing_identity.pop("workflow_id")
    mismatched_outcome = workflow_transition_outcome_fingerprint(
        snapshot.transition,
        snapshot.effects,
        binding_status=raw_status,
        checkpoint_ref=raw_status["checkpoint_ref"],
        receipt_result=missing_identity,
    )
    with pytest.raises(WorkflowTransitionPersistenceError, match="receipt_projection_mismatch"):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            binding_status=raw_status,
            checkpoint_ref=raw_status["checkpoint_ref"],
            outcome_fingerprint=mismatched_outcome,
            receipt_result=missing_identity,
        )

    completed = harness.store.finalize(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=1,
        binding_status=raw_status,
        checkpoint_ref=raw_status["checkpoint_ref"],
        outcome_fingerprint=outcome,
        receipt_result=public_status,
    )
    assert thaw_json(completed.transition.result_status) == raw_status
    binding, receipt = _records(harness)
    assert binding["last_status"] == raw_status
    assert binding["public_status"] == public_status
    assert receipt["result_status"] == public_status
    assert receipt["outcome_fingerprint"] == outcome


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_unrelated_binding_revision_cannot_complete_the_receipt(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"unrelated-revision-{kind}")
    transition, _outcome = _prepare_for_finalize(harness)
    unrelated_status = {
        "status": "paused",
        "revision": 8,
        "checkpoint_ref": "unrelated-checkpoint-8",
    }
    if harness.engine is None:
        bindings = getattr(harness.store, "_bindings")
        bindings["workflow-a"].update(
            runtime_revision=8,
            runtime_checkpoint_ref="unrelated-checkpoint-8",
            last_status=unrelated_status,
            revision=bindings["workflow-a"]["revision"] + 1,
        )
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowControlBindingDB)
                .where(WorkflowControlBindingDB.id == "workflow-a")
                .values(
                    runtime_revision=8,
                    runtime_checkpoint_ref="unrelated-checkpoint-8",
                    last_status=unrelated_status,
                    revision=WorkflowControlBindingDB.revision + 1,
                )
            )
    snapshot = harness.store.get(transition.transition_id)
    assert snapshot is not None
    unrelated_outcome = workflow_transition_outcome_fingerprint(
        snapshot.transition,
        snapshot.effects,
        binding_status=unrelated_status,
        checkpoint_ref="unrelated-checkpoint-8",
        receipt_result=unrelated_status,
    )

    with pytest.raises(
        WorkflowTransitionPersistenceError,
        match="binding_cas_conflict",
    ):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            binding_status=unrelated_status,
            checkpoint_ref="unrelated-checkpoint-8",
            outcome_fingerprint=unrelated_outcome,
            receipt_result=unrelated_status,
        )

    current = harness.store.get(transition.transition_id)
    assert current is not None and current.transition.state == TRANSITION_STATE_APPLYING
    _binding, receipt = _records(harness)
    assert receipt["state"] == "dispatching"
    assert receipt["outcome_fingerprint"] == ""


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    "fault_stage",
    [
        "finalize_before_binding_cas",
        "finalize_after_binding_cas",
        "finalize_after_receipt_cas",
        "finalize_before_transition_cas",
    ],
)
def test_finalize_fault_seams_roll_back_every_proof(
    kind: str,
    fault_stage: str,
    tmp_path: Any,
) -> None:
    def inject(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(f"injected:{stage}")

    harness = _harness(
        kind,
        tmp_path,
        name=f"finalize-fault-{kind}-{fault_stage}",
        fault_injector=inject,
    )
    transition, outcome = _prepare_for_finalize(harness)
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }

    with pytest.raises(RuntimeError, match=f"injected:{fault_stage}"):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            outcome_fingerprint=outcome,
            receipt_result=status,
        )

    snapshot = harness.store.get(transition.transition_id)
    assert snapshot is not None
    assert snapshot.transition.state == TRANSITION_STATE_APPLYING
    assert snapshot.effects[-1].state == EFFECT_STATE_PLANNED
    binding, receipt = _records(harness)
    assert binding["active_transition_id"] == transition.transition_id
    assert binding["runtime_revision"] == 7
    assert receipt["state"] == "dispatching"
    assert receipt["outcome_fingerprint"] == ""


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_reject_fault_seam_rolls_back_every_terminal_mutation(
    kind: str,
    tmp_path: Any,
) -> None:
    def inject(stage: str) -> None:
        if stage == "reject_before_commit":
            raise RuntimeError("injected:reject_before_commit")

    harness = _harness(
        kind,
        tmp_path,
        name=f"reject-fault-{kind}",
        fault_injector=inject,
    )
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=10.0,
    )

    with pytest.raises(RuntimeError, match="injected:reject_before_commit"):
        harness.store.reject(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            reason_code="policy_rejected",
        )

    snapshot = harness.store.get(transition.transition_id)
    assert snapshot is not None
    assert snapshot.transition.state == TRANSITION_STATE_APPLYING
    assert all(effect.state == EFFECT_STATE_PLANNED for effect in snapshot.effects)
    binding, receipt = _records(harness)
    assert binding["active_transition_id"] == transition.transition_id
    assert receipt["state"] == "dispatching"


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_stage_same_plan_after_release_adopts_retry_scheduled_snapshot(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"stage-after-release-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    claimed = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=10.0,
    )
    assert claimed is not None

    released = harness.store.release(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=claimed.transition.claim_generation,
        reason_code="retryable_queue_error",
        retry_at=1_025.0,
    )

    restarted_store = harness.adapter()
    adopted = restarted_store.stage(transition, effects, receipt_id="command-a")

    assert adopted == released
    assert adopted.transition.state == TRANSITION_STATE_READY
    assert adopted.transition.available_at == 1_025.0
    assert adopted.transition.claim_generation == 1
    assert adopted.transition.attempt_count == 1
    assert adopted.transition.last_error == "retryable_queue_error"
    assert restarted_store.get(transition.transition_id) == released


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_release_and_reject_have_generation_fenced_terminal_semantics(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"release-reject-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=10.0,
    )
    released = harness.store.release(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=1,
        reason_code="retryable_queue_error",
        retry_at=1_005.0,
    )
    assert released.transition.state == TRANSITION_STATE_READY
    assert (
        harness.store.claim(
            transition.transition_id,
            owner_id="owner-b",
            lease_seconds=10.0,
        )
        is None
    )

    harness.clock.value = 1_006.0
    claimed = harness.store.claim(
        transition.transition_id,
        owner_id="owner-b",
        lease_seconds=10.0,
    )
    assert claimed is not None and claimed.transition.claim_generation == 2
    with pytest.raises(WorkflowTransitionPersistenceError, match="lease_conflict"):
        harness.store.reject(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            reason_code="stale_owner",
        )
    rejected = harness.store.reject(
        transition.transition_id,
        owner_id="owner-b",
        claim_generation=2,
        reason_code="policy_rejected",
    )
    assert rejected.transition.state == TRANSITION_STATE_REJECTED
    assert all(effect.state == "rejected" for effect in rejected.effects)
    binding, receipt = _records(harness)
    assert binding["active_transition_id"] == ""
    assert binding["last_transition_id"] == transition.transition_id
    assert receipt["state"] == "rejected"
    assert receipt["dispatch_generation"] == 2

    duplicate_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_COMMAND,
        identity_key="different-transition-id",
    )
    duplicate_effects = (
        WorkflowTransitionEffect.build(
            transition_id=duplicate_id,
            ordinal=1,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key="task-duplicate",
            payload={"task_id": "task-duplicate"},
            created_at=1_006.0,
        ),
        WorkflowTransitionEffect.build(
            transition_id=duplicate_id,
            ordinal=2,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key="workflow-a",
            payload={"workflow_id": "workflow-a"},
            created_at=1_006.0,
        ),
    )
    duplicate = WorkflowTransition.build(
        transition_id=duplicate_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_COMMAND,
        command_id="command-a",
        admitted_command={"command_id": "command-a", "kind": "advance"},
        request_payload={"command": "advance"},
        effects=duplicate_effects,
        expected_revision=7,
        expected_checkpoint_ref="checkpoint-7",
        created_at=1_006.0,
    )
    with pytest.raises(WorkflowTransitionPersistenceError, match="stage_conflict"):
        harness.store.stage(duplicate, duplicate_effects)
