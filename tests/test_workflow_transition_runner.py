from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
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
from agent.services.workflow_transition_effect_execution import (
    BoundedWorkflowTransitionRetryPolicy,
    EffectAlreadyApplied,
    EffectApplied,
    EffectExecutable,
    EffectRetry,
    FinalizationObserved,
    FinalizationRetry,
    RetryAt,
    WorkflowTransitionEffectExecutorRegistry,
    WorkflowTransitionEffectHandler,
    WorkflowTransitionEffectRegistration,
    WorkflowTransitionFinalizationObserverRegistry,
    WorkflowTransitionFinalizationRegistration,
    workflow_transition_effect_stage_attempt_count,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_QUEUE_RESERVE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    TRANSITION_KIND_ADVANCE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_QUARANTINED,
    TRANSITION_STATE_READY,
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_result_digest,
    workflow_transition_id,
)
from agent.services.workflow_transition_persistence import (
    InMemoryWorkflowTransitionStore,
    SQLAlchemyWorkflowTransitionStore,
    WorkflowTransitionPersistenceError,
)
from agent.services.workflow_transition_runner import (
    RUN_OUTCOME_COMPLETED,
    RUN_OUTCOME_FENCED,
    RUN_OUTCOME_PROGRESSED,
    RUN_OUTCOME_QUARANTINED,
    RUN_OUTCOME_RETRY_SCHEDULED,
    WorkflowTransitionRunner,
)

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Clock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value


class _CanonicalProjector:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def project(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        raw = dict(kwargs["binding_status"])
        return {
            **raw,
            "checkpoint_ref": f"public:{raw['revision']}",
            "projection": "canonical",
        }


class _Observer:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[Any] = []

    def observe_or_adopt(self, observation: Any, *, heartbeat: Any) -> Any:
        self.calls.append(observation)
        return self.result(observation, heartbeat) if callable(self.result) else self.result


class _Executor:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[Any] = []

    def execute(self, attempt: Any, *, executable: Any, heartbeat: Any) -> Any:
        self.calls.append((attempt, executable))
        return self.result(attempt, executable, heartbeat) if callable(self.result) else self.result


class _FinalizationObserver:
    def __init__(self, result: Any | None = None) -> None:
        self.result = result or FinalizationObserved(
            {
                "status": "running",
                "revision": 8,
                "checkpoint_ref": "checkpoint-8",
            },
            "checkpoint-8",
            {"observation_revision": 8},
        )
        self.calls: list[Any] = []

    def observe(self, attempt: Any, *, heartbeat: Any) -> Any:
        self.calls.append(attempt)
        return self.result(attempt, heartbeat) if callable(self.result) else self.result


class _RecordingRetryPolicy:
    def __init__(self, delay: float = 2.0) -> None:
        self.delay = delay
        self.attempts: list[int] = []

    def next_retry(self, *, attempt_count: int, decision_at: float) -> RetryAt:
        self.attempts.append(attempt_count)
        return RetryAt(decision_at + self.delay)

    def authorize_attempt(self, *, attempt_count: int) -> bool:
        return attempt_count >= 1


@dataclass
class _Harness:
    store: Any
    clock: _Clock
    projector: _CanonicalProjector
    engine: Engine | None = None

    def binding(self) -> dict[str, Any]:
        if self.engine is None:
            value = self.store.binding_record("workflow-a")
            assert value is not None
            return value
        with Session(self.engine) as session:
            row = session.get(WorkflowControlBindingDB, "workflow-a")
            assert row is not None
            return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _plan(
    *,
    effect_count: int = 1,
) -> tuple[WorkflowTransition, tuple[WorkflowTransitionEffect, ...]]:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_ADVANCE,
        identity_key="advance-a",
    )
    effects = tuple(
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=ordinal,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key=f"task-{ordinal}",
            payload={"task_id": f"task-{ordinal}"},
            created_at=1_000.0,
        )
        for ordinal in range(1, effect_count + 1)
    ) + (
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=effect_count + 1,
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
            kind=TRANSITION_KIND_ADVANCE,
            request_payload={"advance_id": "advance-a"},
            effects=effects,
            expected_revision=7,
            expected_checkpoint_ref="checkpoint-7",
            created_at=1_000.0,
        ),
        effects,
    )


def _receipt_plan() -> tuple[WorkflowTransition, tuple[WorkflowTransitionEffect, ...]]:
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
            idempotency_key="task-1",
            payload={"task_id": "task-1"},
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


def _harness(
    kind: str,
    tmp_path: Path,
    *,
    name: str,
    receipt: bool = False,
) -> _Harness:
    clock = _Clock()
    projector = _CanonicalProjector()
    if kind == "memory":
        store = InMemoryWorkflowTransitionStore(
            clock=clock,
            receipt_projector=projector,
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
            public_status={},
            command_receipt_id="command-a" if receipt else "",
        )
        if receipt:
            store.put_receipt(
                receipt_id="command-a",
                tenant_id="tenant-a",
                workflow_id="workflow-a",
                run_id="run-a",
                expected_revision=7,
                checkpoint_ref="checkpoint-7",
                request_payload={"command": "advance"},
            )
        return _Harness(store, clock, projector)

    engine = _create_sql_engine(str(tmp_path / f"{name}.db"))
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
                command_receipt_id="command-a" if receipt else "",
                created_at=999.0,
                updated_at=999.0,
            )
        )
        if receipt:
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
    store = SQLAlchemyWorkflowTransitionStore(
        engine,
        clock=clock,
        receipt_projector=projector,
    )
    return _Harness(store, clock, projector, engine)


def _mutate_binding(harness: _Harness, **values: Any) -> None:
    if harness.engine is None:
        getattr(harness.store, "_bindings")["workflow-a"].update(values)
        return
    with harness.engine.begin() as connection:
        connection.execute(
            sa.update(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == "workflow-a").values(**values)
        )


def _mutate_receipt(harness: _Harness, **values: Any) -> None:
    if harness.engine is None:
        getattr(harness.store, "_receipts")["command-a"].update(values)
        return
    with harness.engine.begin() as connection:
        connection.execute(
            sa.update(WorkflowControlCommandReceiptDB)
            .where(WorkflowControlCommandReceiptDB.id == "command-a")
            .values(**values)
        )


def _corrupt_later_effect_as_applied(
    harness: _Harness,
    *,
    transition_id: str,
    effect_id: str,
) -> None:
    result_payload = {
        "schema": "ananta.workflow-transition-effect-result.v1",
        "mode": "execute",
        "effect_result": {"task_id": "out-of-order"},
        "effect_proof": {"downstream_revision": 1},
        "stage_attempt_count": 1,
    }
    digest = workflow_transition_effect_result_digest(result_payload)
    if harness.engine is None:
        effects = list(getattr(harness.store, "_effects")[transition_id])
        index = next(index for index, effect in enumerate(effects) if effect.effect_id == effect_id)
        effects[index] = replace(
            effects[index],
            state=EFFECT_STATE_APPLIED,
            applied_generation=1,
            result_payload=result_payload,
            result_digest=digest,
            revision=effects[index].revision + 1,
        )
        getattr(harness.store, "_effects")[transition_id] = tuple(effects)
        return
    with harness.engine.begin() as connection:
        connection.execute(
            sa.update(WorkflowTransitionEffectDB)
            .where(WorkflowTransitionEffectDB.id == effect_id)
            .values(
                state=EFFECT_STATE_APPLIED,
                applied_generation=1,
                result_payload=result_payload,
                result_digest=digest,
                revision=WorkflowTransitionEffectDB.revision + 1,
            )
        )


def _runner(
    harness: _Harness,
    *,
    observer: _Observer,
    executor: _Executor,
    finalization: _FinalizationObserver | None = None,
    retry_policy: Any | None = None,
    owner_id: str = "runner-a",
    lease_seconds: float = 30.0,
    fault_injector: Any = None,
) -> WorkflowTransitionRunner:
    handler = WorkflowTransitionEffectHandler(observer, executor)
    return WorkflowTransitionRunner(
        reads=harness.store,
        leases=harness.store,
        effects=harness.store,
        completion=harness.store,
        quarantine=harness.store,
        effect_registry=WorkflowTransitionEffectExecutorRegistry(
            (
                WorkflowTransitionEffectRegistration(
                    TRANSITION_RUNTIME_NATIVE,
                    EFFECT_QUEUE_RESERVE,
                    handler,
                ),
            )
        ),
        finalization_registry=WorkflowTransitionFinalizationObserverRegistry(
            (
                WorkflowTransitionFinalizationRegistration(
                    TRANSITION_RUNTIME_NATIVE,
                    TRANSITION_KIND_ADVANCE,
                    finalization or _FinalizationObserver(),
                ),
            )
        ),
        retry_policy=retry_policy or BoundedWorkflowTransitionRetryPolicy(3, 2.0, 2.0, 10.0),
        owner_id=owner_id,
        lease_seconds=lease_seconds,
        clock=harness.clock,
        fault_injector=fault_injector,
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_runner_applies_one_effect_per_claim_then_finalizes_raw_and_public(
    kind: str,
    tmp_path: Path,
) -> None:
    harness = _harness(kind, tmp_path, name=f"happy-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    observer = _Observer(EffectExecutable({"absence_revision": 1}))
    executor = _Executor(
        EffectApplied(
            {"task_id": "task-1", "queue_state": "reserved"},
            {"downstream_revision": 2},
        )
    )
    finalization = _FinalizationObserver()
    runner = _runner(
        harness,
        observer=observer,
        executor=executor,
        finalization=finalization,
    )

    progressed = runner.run(transition.transition_id)
    completed = runner.run(transition.transition_id)

    assert progressed.outcome == RUN_OUTCOME_PROGRESSED
    assert progressed.snapshot.transition.state == TRANSITION_STATE_READY
    assert progressed.snapshot.transition.last_error == ""
    assert progressed.snapshot.effects[0].state == EFFECT_STATE_APPLIED
    envelope = thaw_json(progressed.snapshot.effects[0].result_payload)
    assert envelope == {
        "schema": "ananta.workflow-transition-effect-result.v1",
        "mode": "execute",
        "effect_result": {"task_id": "task-1", "queue_state": "reserved"},
        "effect_proof": {"downstream_revision": 2},
        "stage_attempt_count": 1,
    }
    assert progressed.snapshot.effects[0].result_digest == (workflow_transition_effect_result_digest(envelope))
    assert completed.outcome == RUN_OUTCOME_COMPLETED
    assert completed.snapshot.transition.state == TRANSITION_STATE_COMPLETED
    assert len(observer.calls) == len(executor.calls) == len(finalization.calls) == 1
    binding = harness.binding()
    assert binding["last_status"]["checkpoint_ref"] == "checkpoint-8"
    assert binding["public_status"]["checkpoint_ref"] == "public:8"
    assert binding["public_status"]["projection"] == "canonical"
    assert thaw_json(completed.snapshot.effects[-1].result_payload)["public_status"] == (binding["public_status"])
    assert thaw_json(completed.snapshot.effects[-1].result_payload)["finalization_stage_attempt_count"] == 1
    assert thaw_json(completed.snapshot.effects[-1].result_payload)["finalization_proof"] == {"observation_revision": 8}


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("drift", ["revision", "marker", "receipt"])
def test_pre_begin_authority_loss_never_calls_the_executor(
    kind: str,
    drift: str,
    tmp_path: Path,
) -> None:
    receipt = drift == "receipt"
    harness = _harness(
        kind,
        tmp_path,
        name=f"pre-begin-authority-{kind}-{drift}",
        receipt=receipt,
    )
    transition, effects = _receipt_plan() if receipt else _plan()
    harness.store.stage(
        transition,
        effects,
        receipt_id="command-a" if receipt else "",
    )

    def observe(_observation: Any, _heartbeat: Any) -> EffectExecutable:
        if drift == "revision":
            _mutate_binding(harness, runtime_revision=8)
        elif drift == "marker":
            _mutate_binding(harness, active_transition_id="")
        else:
            _mutate_receipt(harness, dispatch_generation=99)
        return EffectExecutable({"absence_revision": 1})

    observer = _Observer(observe)
    executor = _Executor(EffectApplied({"task_id": "must-not-run"}, {"downstream_revision": 1}))
    result = _runner(harness, observer=observer, executor=executor).run(transition.transition_id)

    assert len(observer.calls) == 1
    assert executor.calls == []
    assert result.snapshot.effects[0].state == EFFECT_STATE_PLANNED
    assert result.outcome == (RUN_OUTCOME_QUARANTINED if drift == "revision" else RUN_OUTCOME_FENCED)


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("operation", ["release", "yield"])
def test_requeue_binding_drift_immediately_quarantines(
    kind: str,
    operation: str,
    tmp_path: Path,
) -> None:
    harness = _harness(kind, tmp_path, name=f"requeue-drift-{kind}-{operation}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)

    if operation == "release":

        def observe(_observation: Any, _heartbeat: Any) -> EffectRetry:
            _mutate_binding(harness, runtime_revision=8)
            return EffectRetry("downstream_not_ready")

        observer = _Observer(observe)
        executor = _Executor(pytest.fail)
    else:
        observer = _Observer(EffectExecutable({"absence_revision": 1}))

        def execute(_attempt: Any, _proof: Any, _heartbeat: Any) -> EffectApplied:
            _mutate_binding(harness, runtime_revision=8)
            return EffectApplied(
                {"task_id": "task-1"},
                {"downstream_revision": 1},
            )

        executor = _Executor(execute)

    result = _runner(harness, observer=observer, executor=executor).run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_QUARANTINED
    assert result.snapshot.transition.last_error == "binding_state_drift"
    assert result.snapshot.effects[0].state == (
        EFFECT_STATE_PLANNED if operation == "release" else EFFECT_STATE_APPLIED
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("operation", ["release", "yield"])
def test_requeue_stale_lease_is_fenced_after_quarantine_attempt(
    kind: str,
    operation: str,
    tmp_path: Path,
) -> None:
    harness = _harness(kind, tmp_path, name=f"requeue-stale-{kind}-{operation}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    method_name = "release" if operation == "release" else "yield_ready"
    original = getattr(harness.store, method_name)

    def expire_before_requeue(*args: Any, **kwargs: Any) -> Any:
        harness.clock.value += 31.0
        return original(*args, **kwargs)

    setattr(harness.store, method_name, expire_before_requeue)
    observer = _Observer(
        EffectRetry("downstream_not_ready") if operation == "release" else EffectExecutable({"absence_revision": 1})
    )
    executor = _Executor(
        pytest.fail if operation == "release" else EffectApplied({"task_id": "task-1"}, {"downstream_revision": 1})
    )

    result = _runner(harness, observer=observer, executor=executor).run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_FENCED
    assert result.snapshot.effects[0].state == (
        EFFECT_STATE_PLANNED if operation == "release" else EFFECT_STATE_APPLIED
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_applied_proof_is_durable_before_a_post_call_heartbeat_failure(
    kind: str,
    tmp_path: Path,
) -> None:
    harness = _harness(kind, tmp_path, name=f"post-call-heartbeat-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    original_heartbeat = harness.store.heartbeat
    heartbeat_calls = 0
    yield_calls = 0

    def heartbeat(*args: Any, **kwargs: Any) -> Any:
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        if heartbeat_calls == 4:
            raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
        return original_heartbeat(*args, **kwargs)

    original_yield = harness.store.yield_ready

    def yield_ready(*args: Any, **kwargs: Any) -> Any:
        nonlocal yield_calls
        yield_calls += 1
        return original_yield(*args, **kwargs)

    harness.store.heartbeat = heartbeat
    harness.store.yield_ready = yield_ready
    result = _runner(
        harness,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(
            EffectApplied(
                {"task_id": "task-1"},
                {"downstream_revision": 2},
            )
        ),
    ).run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_QUARANTINED
    assert yield_calls == 0
    assert result.snapshot.effects[0].state == EFFECT_STATE_APPLIED
    envelope = thaw_json(result.snapshot.effects[0].result_payload)
    assert envelope["effect_result"] == {"task_id": "task-1"}
    assert envelope["effect_proof"] == {"downstream_revision": 2}


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("first_state", ["planned", "applying"])
def test_out_of_order_applied_effect_quarantines_before_observation(
    kind: str,
    first_state: str,
    tmp_path: Path,
) -> None:
    harness = _harness(kind, tmp_path, name=f"runner-order-{kind}-{first_state}")
    transition, effects = _plan(effect_count=2)
    harness.store.stage(transition, effects)
    assert harness.store.claim(
        transition.transition_id,
        owner_id="crashed-runner",
        lease_seconds=10.0,
    )
    if first_state == "applying":
        harness.store.begin_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="crashed-runner",
            claim_generation=1,
        )
    _corrupt_later_effect_as_applied(
        harness,
        transition_id=transition.transition_id,
        effect_id=effects[1].effect_id,
    )
    harness.clock.value = 1_011.0
    observer = _Observer(EffectExecutable({"absence_revision": 2}))
    executor = _Executor(EffectApplied({"task_id": "must-not-run"}, {"downstream_revision": 2}))

    result = _runner(
        harness,
        observer=observer,
        executor=executor,
        owner_id="recovery-runner",
    ).run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_QUARANTINED
    assert observer.calls == []
    assert executor.calls == []
    assert result.snapshot.effects[0].state == (
        EFFECT_STATE_PLANNED if first_state == "planned" else EFFECT_STATE_APPLYING
    )
    assert result.snapshot.effects[1].state == EFFECT_STATE_APPLIED


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("observed_mode", ["absent", "applied"])
def test_expired_pre_send_claim_is_adopted_with_exact_external_proof(
    kind: str,
    observed_mode: str,
    tmp_path: Path,
) -> None:
    harness = _harness(kind, tmp_path, name=f"pre-send-{kind}-{observed_mode}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    claimed = harness.store.claim(
        transition.transition_id,
        owner_id="crashed-runner",
        lease_seconds=10.0,
    )
    assert claimed is not None
    harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="crashed-runner",
        claim_generation=1,
    )
    harness.clock.value = 1_011.0
    external_mutations: list[str] = ["task-1"] if observed_mode == "applied" else []
    observer_result: Any = (
        EffectAlreadyApplied(
            {"task_id": "task-1", "queue_state": "reserved"},
            {"ledger_revision": 7},
        )
        if observed_mode == "applied"
        else EffectExecutable({"authoritative_absence_revision": 7})
    )

    def execute(_attempt: Any, _proof: Any, _heartbeat: Any) -> EffectApplied:
        external_mutations.append("task-1")
        return EffectApplied(
            {"task_id": "task-1", "queue_state": "reserved"},
            {"ledger_revision": 8},
        )

    executor = _Executor(execute)
    runner = _runner(
        harness,
        observer=_Observer(observer_result),
        executor=executor,
        owner_id="recovery-runner",
    )

    progressed = runner.run(transition.transition_id)

    assert progressed.outcome == RUN_OUTCOME_PROGRESSED
    assert external_mutations == ["task-1"]
    assert len(executor.calls) == (0 if observed_mode == "applied" else 1)
    envelope = thaw_json(progressed.snapshot.effects[0].result_payload)
    assert envelope["mode"] == ("adopt" if observed_mode == "applied" else "execute")
    assert envelope["stage_attempt_count"] == 2
    assert envelope["effect_proof"] == (
        {"ledger_revision": 7} if observed_mode == "applied" else {"ledger_revision": 8}
    )


def test_crash_boundaries_preserve_pre_begin_and_post_finish_proofs(tmp_path: Path) -> None:
    harness = _harness("memory", tmp_path, name="crash-boundaries")
    transition, effects = _plan()
    harness.store.stage(transition, effects)

    def crash(stage: str) -> None:
        if stage == "after_effect_observation":
            raise KeyboardInterrupt("crash-after-observation")

    crashing = _runner(
        harness,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(EffectApplied({"task_id": "task-1"}, {"downstream_revision": 2})),
        fault_injector=crash,
    )
    with pytest.raises(KeyboardInterrupt, match="crash-after-observation"):
        crashing.run(transition.transition_id)
    after_observation = harness.store.get(transition.transition_id)
    assert after_observation is not None
    assert after_observation.transition.state == TRANSITION_STATE_APPLYING
    assert after_observation.effects[0].state == EFFECT_STATE_PLANNED

    harness.clock.value = 1_031.0
    external_mutations: list[str] = []

    def execute(_attempt: Any, _proof: Any, _heartbeat: Any) -> EffectApplied:
        external_mutations.append("task-1")
        return EffectApplied({"task_id": "task-1"}, {"downstream_revision": 2})

    def crash_after_finish(stage: str) -> None:
        if stage == "after_effect_finish":
            raise KeyboardInterrupt("crash-after-finish")

    second = _runner(
        harness,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(execute),
        owner_id="runner-b",
        fault_injector=crash_after_finish,
    )
    with pytest.raises(KeyboardInterrupt, match="crash-after-finish"):
        second.run(transition.transition_id)
    after_finish = harness.store.get(transition.transition_id)
    assert after_finish is not None
    assert after_finish.effects[0].state == EFFECT_STATE_APPLIED
    assert external_mutations == ["task-1"]

    harness.clock.value = 1_062.0
    finalization = _FinalizationObserver()
    recovered = _runner(
        harness,
        observer=_Observer(EffectExecutable({"unused": True})),
        executor=_Executor(lambda *_args: pytest.fail("applied effect re-executed")),
        finalization=finalization,
        owner_id="runner-c",
    ).run(transition.transition_id)
    assert recovered.outcome == RUN_OUTCOME_COMPLETED
    assert external_mutations == ["task-1"]
    assert len(finalization.calls) == 1


def test_post_send_crash_is_adopted_without_reexecution(tmp_path: Path) -> None:
    harness = _harness("memory", tmp_path, name="post-send")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    external_mutations: list[str] = []

    def execute(_attempt: Any, _proof: Any, _heartbeat: Any) -> EffectApplied:
        external_mutations.append("task-1")
        return EffectApplied({"task_id": "task-1"}, {"downstream_revision": 2})

    def crash(stage: str) -> None:
        if stage == "after_effect_execution":
            raise KeyboardInterrupt("crash-after-send")

    with pytest.raises(KeyboardInterrupt, match="crash-after-send"):
        _runner(
            harness,
            observer=_Observer(EffectExecutable({"absence_revision": 1})),
            executor=_Executor(execute),
            fault_injector=crash,
        ).run(transition.transition_id)
    ambiguous = harness.store.get(transition.transition_id)
    assert ambiguous is not None and ambiguous.effects[0].state == EFFECT_STATE_APPLYING

    harness.clock.value = 1_031.0
    adopted = _runner(
        harness,
        observer=_Observer(
            EffectAlreadyApplied(
                {"task_id": "task-1"},
                {"downstream_revision": 2},
            )
        ),
        executor=_Executor(lambda *_args: pytest.fail("ambiguous effect re-executed")),
        owner_id="runner-b",
    ).run(transition.transition_id)
    assert adopted.outcome == RUN_OUTCOME_PROGRESSED
    assert external_mutations == ["task-1"]
    assert thaw_json(adopted.snapshot.effects[0].result_payload)["mode"] == "adopt"


def test_stage_local_retry_budget_survives_prior_stage_retry_and_success(
    tmp_path: Path,
) -> None:
    harness = _harness("memory", tmp_path, name="stage-retry-budget")
    transition, effects = _plan(effect_count=3)
    harness.store.stage(transition, effects)
    calls: dict[int, int] = {}

    def observe(observation: Any, _heartbeat: Any) -> Any:
        ordinal = observation.effect.ordinal
        calls[ordinal] = calls.get(ordinal, 0) + 1
        if ordinal in {1, 3} and calls[ordinal] == 1:
            return EffectRetry("downstream_not_ready")
        return EffectExecutable({"absence_revision": calls[ordinal]})

    executor = _Executor(
        lambda attempt, _proof, _heartbeat: EffectApplied(
            {"task_id": f"task-{attempt.effect.ordinal}"},
            {"downstream_revision": attempt.effect.ordinal},
        )
    )
    retry = _RecordingRetryPolicy()
    runner = _runner(
        harness,
        observer=_Observer(observe),
        executor=executor,
        retry_policy=retry,
    )

    assert runner.run(transition.transition_id).outcome == RUN_OUTCOME_RETRY_SCHEDULED
    harness.clock.value = 1_002.0
    first = runner.run(transition.transition_id)
    second = runner.run(transition.transition_id)
    third_retry = runner.run(transition.transition_id)

    assert first.outcome == second.outcome == RUN_OUTCOME_PROGRESSED
    assert third_retry.outcome == RUN_OUTCOME_RETRY_SCHEDULED
    assert retry.attempts == [1, 1]
    assert workflow_transition_effect_stage_attempt_count(thaw_json(first.snapshot.effects[0].result_payload)) == 2
    assert workflow_transition_effect_stage_attempt_count(thaw_json(second.snapshot.effects[1].result_payload)) == 1


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_finalization_retry_count_and_proof_change_the_terminal_outcome(
    kind: str,
    tmp_path: Path,
) -> None:
    baseline = _harness(kind, tmp_path, name=f"final-count-baseline-{kind}")
    baseline_transition, baseline_effects = _plan()
    baseline.store.stage(baseline_transition, baseline_effects)
    baseline_runner = _runner(
        baseline,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(EffectApplied({"task_id": "task-1"}, {"downstream_revision": 1})),
    )
    assert baseline_runner.run(baseline_transition.transition_id).outcome == RUN_OUTCOME_PROGRESSED
    baseline_completed = baseline_runner.run(baseline_transition.transition_id)
    assert baseline_completed.outcome == RUN_OUTCOME_COMPLETED

    retried = _harness(kind, tmp_path, name=f"final-count-retried-{kind}")
    retried_transition, retried_effects = _plan()
    retried.store.stage(retried_transition, retried_effects)
    observations = iter(
        (
            FinalizationRetry("runtime_observation_pending"),
            FinalizationObserved(
                {
                    "status": "running",
                    "revision": 8,
                    "checkpoint_ref": "checkpoint-8",
                },
                "checkpoint-8",
                {"observation_revision": 9},
            ),
        )
    )
    finalization = _FinalizationObserver(lambda _attempt, _heartbeat: next(observations))
    retried_runner = _runner(
        retried,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(EffectApplied({"task_id": "task-1"}, {"downstream_revision": 1})),
        finalization=finalization,
    )
    assert retried_runner.run(retried_transition.transition_id).outcome == RUN_OUTCOME_PROGRESSED
    assert retried_runner.run(retried_transition.transition_id).outcome == RUN_OUTCOME_RETRY_SCHEDULED
    retried.clock.value = 1_002.0
    retried_completed = retried_runner.run(retried_transition.transition_id)
    assert retried_completed.outcome == RUN_OUTCOME_COMPLETED

    baseline_final = thaw_json(baseline_completed.snapshot.effects[-1].result_payload)
    retried_final = thaw_json(retried_completed.snapshot.effects[-1].result_payload)
    assert baseline_final["finalization_stage_attempt_count"] == 1
    assert retried_final["finalization_stage_attempt_count"] == 2
    assert retried_final["finalization_proof"] == {"observation_revision": 9}
    assert (
        baseline_completed.snapshot.transition.outcome_fingerprint
        != retried_completed.snapshot.transition.outcome_fingerprint
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_retry_exhaustion_and_unknown_registry_result_quarantine_without_outcome(
    kind: str,
    tmp_path: Path,
) -> None:
    harness = _harness(kind, tmp_path, name=f"poison-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    runner = _runner(
        harness,
        observer=_Observer(EffectRetry("downstream_not_ready")),
        executor=_Executor(EffectApplied({"task_id": "unused"}, {"downstream_revision": 1})),
        retry_policy=BoundedWorkflowTransitionRetryPolicy(1, 2.0, 2.0, 10.0),
    )

    quarantined = runner.run(transition.transition_id)

    assert quarantined.outcome == RUN_OUTCOME_QUARANTINED
    assert quarantined.snapshot.transition.state == TRANSITION_STATE_QUARANTINED
    assert quarantined.snapshot.transition.outcome_fingerprint == ""
    assert all(effect.state == EFFECT_STATE_PLANNED for effect in quarantined.snapshot.effects)
    binding = harness.binding()
    assert binding["active_transition_id"] == transition.transition_id
    assert binding["last_transition_id"] == ""


def test_heartbeat_prevents_takeover_and_stale_yield_is_fenced(tmp_path: Path) -> None:
    harness = _harness("memory", tmp_path, name="heartbeat-fence")
    transition, effects = _plan()
    harness.store.stage(transition, effects)

    def execute(_attempt: Any, _proof: Any, heartbeat: Any) -> EffectApplied:
        harness.clock.value = 1_008.0
        heartbeat.heartbeat()
        harness.clock.value = 1_016.0
        assert (
            harness.store.claim(
                transition.transition_id,
                owner_id="runner-b",
                lease_seconds=10.0,
            )
            is None
        )
        return EffectApplied({"task_id": "task-1"}, {"downstream_revision": 2})

    progressed = _runner(
        harness,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(execute),
        lease_seconds=10.0,
    ).run(transition.transition_id)
    assert progressed.outcome == RUN_OUTCOME_PROGRESSED

    with pytest.raises(WorkflowTransitionPersistenceError, match="lease_conflict"):
        harness.store.yield_ready(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="runner-a",
            claim_generation=1,
            available_at=1_016.0,
        )


def test_runner_is_confined_to_the_framework_adapters_and_one_composition() -> None:
    framework_files = {
        (ROOT / "agent/services/workflow_transition_effect_execution.py").resolve(),
        (ROOT / "agent/services/workflow_transition_outbox.py").resolve(),
        (ROOT / "agent/services/workflow_transition_runner.py").resolve(),
    }
    # Exactly one module may assemble the runner's registries.  Keeping that
    # single seam is what stops transition machinery from spreading across the
    # codebase now that the Native slice is composable.
    composition_files = {
        (ROOT / "agent/services/workflow_transition_native_composition.py").resolve(),
    }
    # Effect adapters legitimately import the execution contracts.  Every new
    # adapter must be listed here deliberately; nothing else may reach for them.
    adapter_files = {
        (ROOT / "agent/services/workflow_transition_authorization_grant.py").resolve(),
        (ROOT / "agent/services/workflow_transition_event_effect.py").resolve(),
        (ROOT / "agent/services/workflow_transition_ownership_reservation.py").resolve(),
        (ROOT / "agent/services/workflow_transition_side_effect_authorization.py").resolve(),
    }
    runner_composition = (
        "WorkflowTransitionRunner",
        "WorkflowTransitionEffectExecutorRegistry",
        "WorkflowTransitionFinalizationObserverRegistry",
        "WorkflowTransitionCompletionPort",
        "workflow_transition_runner",
    )
    execution_contracts = ("workflow_transition_effect_execution",)
    for path in (ROOT / "agent").rglob("*.py"):
        resolved = path.resolve()
        if resolved in framework_files:
            continue
        source = path.read_text(encoding="utf-8")
        if resolved not in composition_files:
            leaked = [symbol for symbol in runner_composition if symbol in source]
            assert not leaked, f"{path.relative_to(ROOT)} references {leaked}"
        if resolved in adapter_files or resolved in composition_files:
            continue
        leaked = [symbol for symbol in execution_contracts if symbol in source]
        assert not leaked, f"{path.relative_to(ROOT)} references {leaked}"


def test_invalid_retry_schedule_quarantines_and_subclass_result_never_begins(
    tmp_path: Path,
) -> None:
    harness = _harness("memory", tmp_path, name="closed-runner-results")
    transition, effects = _plan()
    harness.store.stage(transition, effects)

    class _UnexpectedExecutable(EffectExecutable):
        pass

    runner = _runner(
        harness,
        observer=_Observer(_UnexpectedExecutable({"absence_revision": 1})),
        executor=_Executor(EffectApplied({"task_id": "task-1"}, {"downstream_revision": 1})),
    )
    result = runner.run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_QUARANTINED
    assert result.snapshot.effects[0].state == EFFECT_STATE_PLANNED
    assert result.snapshot.transition.outcome_fingerprint == ""


@pytest.mark.parametrize(
    "policy_behavior",
    ["raise", "equal", "past", "too_far", "unknown"],
)
def test_invalid_retry_policy_results_fail_closed_without_begin(
    policy_behavior: str,
    tmp_path: Path,
) -> None:
    harness = _harness("memory", tmp_path, name=f"invalid-retry-{policy_behavior}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)

    class _InvalidRetryPolicy:
        def authorize_attempt(self, *, attempt_count: int) -> bool:
            return True

        def next_retry(self, *, attempt_count: int, decision_at: float) -> Any:
            if policy_behavior == "raise":
                raise RuntimeError("invalid policy")
            if policy_behavior == "equal":
                return RetryAt(decision_at)
            if policy_behavior == "past":
                return RetryAt(decision_at - 1.0)
            if policy_behavior == "too_far":
                return RetryAt(decision_at + 31_536_001.0)
            return object()

    result = _runner(
        harness,
        observer=_Observer(EffectRetry("downstream_not_ready")),
        executor=_Executor(pytest.fail),
        retry_policy=_InvalidRetryPolicy(),
    ).run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_QUARANTINED
    assert result.snapshot.effects[0].state == EFFECT_STATE_PLANNED


def test_retry_clock_exception_fails_closed_without_begin(tmp_path: Path) -> None:
    harness = _harness("memory", tmp_path, name="invalid-retry-clock")
    transition, effects = _plan()
    harness.store.stage(transition, effects)

    def broken_clock() -> float:
        raise RuntimeError("clock unavailable")

    harness.clock = broken_clock  # type: ignore[assignment]
    result = _runner(
        harness,
        observer=_Observer(EffectRetry("downstream_not_ready")),
        executor=_Executor(pytest.fail),
    ).run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_QUARANTINED
    assert result.snapshot.effects[0].state == EFFECT_STATE_PLANNED


@pytest.mark.parametrize("authorize_behavior", ["raise", "non_boolean"])
def test_invalid_attempt_authorization_never_begins_or_executes(
    authorize_behavior: str,
    tmp_path: Path,
) -> None:
    harness = _harness("memory", tmp_path, name=f"invalid-authorize-{authorize_behavior}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)

    class _InvalidAuthorizationPolicy(_RecordingRetryPolicy):
        def authorize_attempt(self, *, attempt_count: int) -> Any:
            if authorize_behavior == "raise":
                raise RuntimeError("authorization unavailable")
            return 1

    result = _runner(
        harness,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(pytest.fail),
        retry_policy=_InvalidAuthorizationPolicy(),
    ).run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_QUARANTINED
    assert result.snapshot.effects[0].state == EFFECT_STATE_PLANNED
    assert result.snapshot.transition.last_error == "retry_policy_invalid"


@pytest.mark.parametrize("observation_mode", ["executable", "already_applied"])
def test_pre_begin_crashes_consume_execution_budget_but_not_adoption_budget(
    observation_mode: str,
    tmp_path: Path,
) -> None:
    harness = _harness("memory", tmp_path, name=f"pre-begin-budget-{observation_mode}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    policy = BoundedWorkflowTransitionRetryPolicy(3, 2.0, 2.0, 10.0)

    def crash(stage: str) -> None:
        if stage == "after_effect_observation":
            raise KeyboardInterrupt("pre-begin crash")

    for generation in range(1, 4):
        with pytest.raises(KeyboardInterrupt, match="pre-begin crash"):
            _runner(
                harness,
                observer=_Observer(EffectExecutable({"absence_revision": generation})),
                executor=_Executor(pytest.fail),
                retry_policy=policy,
                owner_id=f"runner-{generation}",
                fault_injector=crash,
            ).run(transition.transition_id)
        harness.clock.value += 31.0

    observed = (
        EffectExecutable({"absence_revision": 4})
        if observation_mode == "executable"
        else EffectAlreadyApplied(
            {"task_id": "task-1", "queue_state": "reserved"},
            {"downstream_revision": 4},
        )
    )
    result = _runner(
        harness,
        observer=_Observer(observed),
        executor=_Executor(pytest.fail),
        retry_policy=policy,
        owner_id="runner-4",
    ).run(transition.transition_id)

    if observation_mode == "executable":
        assert result.outcome == RUN_OUTCOME_QUARANTINED
        assert result.snapshot.effects[0].state == EFFECT_STATE_PLANNED
        assert result.snapshot.transition.last_error == "retry_attempts_exhausted"
    else:
        assert result.outcome == RUN_OUTCOME_PROGRESSED
        assert result.snapshot.effects[0].state == EFFECT_STATE_APPLIED
        assert workflow_transition_effect_stage_attempt_count(thaw_json(result.snapshot.effects[0].result_payload)) == 4


def test_malformed_applied_envelope_quarantines_before_final_observation(
    tmp_path: Path,
) -> None:
    harness = _harness("memory", tmp_path, name="runner-malformed-applied")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    progressed = _runner(
        harness,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(EffectApplied({"task_id": "task-1"}, {"downstream_revision": 1})),
    ).run(transition.transition_id)
    assert progressed.outcome == RUN_OUTCOME_PROGRESSED
    stored = list(getattr(harness.store, "_effects")[transition.transition_id])
    raw_result = {
        "schema": "ananta.workflow-transition-effect-result.v1",
        "mode": [],
        "effect_result": {"task_id": "digest-valid-but-unproved"},
        "effect_proof": {"downstream_revision": 1},
        "stage_attempt_count": 1,
    }
    stored[0] = replace(
        stored[0],
        result_payload=raw_result,
        result_digest=workflow_transition_effect_result_digest(raw_result),
        revision=stored[0].revision + 1,
    )
    getattr(harness.store, "_effects")[transition.transition_id] = tuple(stored)
    finalization = _FinalizationObserver()

    result = _runner(
        harness,
        observer=_Observer(pytest.fail),
        executor=_Executor(pytest.fail),
        finalization=finalization,
    ).run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_QUARANTINED
    assert finalization.calls == []
    assert result.snapshot.transition.outcome_fingerprint == ""


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_corrupt_applying_final_effect_quarantines_before_final_observation(
    kind: str,
    tmp_path: Path,
) -> None:
    harness = _harness(kind, tmp_path, name=f"runner-applying-final-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    progressed = _runner(
        harness,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(EffectApplied({"task_id": "task-1"}, {"downstream_revision": 1})),
    ).run(transition.transition_id)
    assert progressed.outcome == RUN_OUTCOME_PROGRESSED
    final_effect = effects[-1]
    if harness.engine is None:
        stored = list(getattr(harness.store, "_effects")[transition.transition_id])
        stored[-1] = replace(
            stored[-1],
            state=EFFECT_STATE_APPLYING,
            applied_generation=2,
            revision=stored[-1].revision + 1,
        )
        getattr(harness.store, "_effects")[transition.transition_id] = tuple(stored)
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowTransitionEffectDB)
                .where(WorkflowTransitionEffectDB.id == final_effect.effect_id)
                .values(
                    state=EFFECT_STATE_APPLYING,
                    applied_generation=2,
                    revision=WorkflowTransitionEffectDB.revision + 1,
                )
            )
    finalization = _FinalizationObserver()

    result = _runner(
        harness,
        observer=_Observer(pytest.fail),
        executor=_Executor(pytest.fail),
        finalization=finalization,
    ).run(transition.transition_id)

    assert result.outcome == RUN_OUTCOME_QUARANTINED
    assert finalization.calls == []
    assert result.snapshot.effects[-1].state == EFFECT_STATE_APPLYING


def test_drain_claims_each_step_only_when_it_is_ready_to_run(tmp_path: Path) -> None:
    harness = _harness("memory", tmp_path, name="drain-one-at-a-time")
    transition, effects = _plan()
    harness.store.stage(transition, effects)
    claim_limits: list[int] = []
    original_claim_due = harness.store.claim_due

    def claim_due(**kwargs: Any) -> Any:
        claim_limits.append(kwargs["limit"])
        return original_claim_due(**kwargs)

    harness.store.claim_due = claim_due

    def execute(_attempt: Any, _proof: Any, _heartbeat: Any) -> EffectApplied:
        assert claim_limits == [1]
        return EffectApplied({"task_id": "task-1"}, {"downstream_revision": 1})

    runner = _runner(
        harness,
        observer=_Observer(EffectExecutable({"absence_revision": 1})),
        executor=_Executor(execute),
    )

    results = runner.drain(limit=2)

    assert [result.outcome for result in results] == [
        RUN_OUTCOME_PROGRESSED,
        RUN_OUTCOME_COMPLETED,
    ]
    assert claim_limits == [1, 1]
    assert all(result.outcome != RUN_OUTCOME_FENCED for result in results)
