from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
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
    TRANSITION_KIND_ADVANCE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_QUARANTINED,
    TRANSITION_STATE_READY,
    TRANSITION_STATE_REJECTED,
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_result_digest,
    workflow_transition_effect_result_envelope,
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


class _SentinelPublicProjector:
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


_IDENTITY_PROJECTOR = _IdentityReceiptProjector()
_FINALIZATION_PROOF = {"observation_revision": 8}


def _effect_result(
    payload: dict[str, Any],
    *,
    stage_attempt_count: int = 1,
    mode: str = "execute",
) -> dict[str, Any]:
    return workflow_transition_effect_result_envelope(
        mode=mode,
        result_payload=payload,
        proof_payload={"test_proof": "exact"},
        stage_attempt_count=stage_attempt_count,
    )


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


def _plan(
    *,
    created_at: float = 1_000.0,
    effect_count: int = 1,
) -> tuple[WorkflowTransition, tuple[WorkflowTransitionEffect, ...]]:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_COMMAND,
        identity_key="command-a",
    )
    effects = tuple(
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=ordinal,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key="task-a" if ordinal == 1 else f"task-{ordinal}",
            payload={
                "task_id": "task-a" if ordinal == 1 else f"task-{ordinal}",
                "attempt_id": "attempt-a" if ordinal == 1 else f"attempt-{ordinal}",
            },
            created_at=created_at,
        )
        for ordinal in range(1, effect_count + 1)
    ) + (
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=effect_count + 1,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key="workflow-a",
            payload={"workflow_id": "workflow-a"},
            created_at=created_at,
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
            created_at=created_at,
        ),
        effects,
    )


def _receiptless_plan(
    *,
    created_at: float = 1_000.0,
) -> tuple[WorkflowTransition, tuple[WorkflowTransitionEffect, ...]]:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_ADVANCE,
        identity_key="advance-8",
    )
    effects = (
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=1,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key="task-a",
            payload={"task_id": "task-a", "attempt_id": "attempt-a"},
            created_at=created_at,
        ),
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=2,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key="workflow-a",
            payload={"workflow_id": "workflow-a"},
            created_at=created_at,
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
            request_payload={"advance_id": "advance-8"},
            effects=effects,
            expected_revision=7,
            expected_checkpoint_ref="checkpoint-7",
            created_at=created_at,
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


def _delete_transition_but_keep_binding_marker(
    harness: _Harness,
    transition_id: str,
) -> None:
    if harness.engine is None:
        getattr(harness.store, "_effects").pop(transition_id)
        getattr(harness.store, "_transitions").pop(transition_id)
        return
    with harness.engine.begin() as connection:
        connection.execute(
            sa.delete(WorkflowTransitionEffectDB).where(WorkflowTransitionEffectDB.transition_id == transition_id)
        )
        connection.execute(sa.delete(WorkflowTransitionOutboxDB).where(WorkflowTransitionOutboxDB.id == transition_id))


def _raw_transition_records(
    harness: _Harness,
    transition_id: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    if harness.engine is None:
        transition = getattr(harness.store, "_transitions")[transition_id]
        effects = getattr(harness.store, "_effects")[transition_id]
        return transition.to_dict(), tuple(effect.to_dict() for effect in effects)
    with Session(harness.engine) as session:
        transition = session.get(WorkflowTransitionOutboxDB, transition_id)
        assert transition is not None
        effects = (
            session.execute(
                sa.select(WorkflowTransitionEffectDB)
                .where(WorkflowTransitionEffectDB.transition_id == transition_id)
                .order_by(WorkflowTransitionEffectDB.ordinal.asc())
            )
            .scalars()
            .all()
        )
        return (
            {column.name: getattr(transition, column.name) for column in transition.__table__.columns},
            tuple(
                {column.name: getattr(effect, column.name) for column in effect.__table__.columns} for effect in effects
            ),
        )


def _corrupt_effect_result(
    harness: _Harness,
    *,
    transition_id: str,
    effect_id: str,
    result_payload: dict[str, Any],
) -> None:
    digest = workflow_transition_effect_result_digest(result_payload)
    if harness.engine is None:
        values = list(getattr(harness.store, "_effects")[transition_id])
        index = next(index for index, effect in enumerate(values) if effect.effect_id == effect_id)
        values[index] = replace(
            values[index],
            result_payload=result_payload,
            result_digest=digest,
            revision=values[index].revision + 1,
        )
        getattr(harness.store, "_effects")[transition_id] = tuple(values)
        return
    with harness.engine.begin() as connection:
        connection.execute(
            sa.update(WorkflowTransitionEffectDB)
            .where(WorkflowTransitionEffectDB.id == effect_id)
            .values(
                result_payload=result_payload,
                result_digest=digest,
                revision=WorkflowTransitionEffectDB.revision + 1,
            )
        )


def _corrupt_effect_as_applied(
    harness: _Harness,
    *,
    transition_id: str,
    effect_id: str,
    applied_generation: int,
) -> None:
    payload = _effect_result(
        {"task_id": "out-of-order"},
        stage_attempt_count=applied_generation,
    )
    digest = workflow_transition_effect_result_digest(payload)
    if harness.engine is None:
        values = list(getattr(harness.store, "_effects")[transition_id])
        index = next(index for index, effect in enumerate(values) if effect.effect_id == effect_id)
        values[index] = replace(
            values[index],
            state=EFFECT_STATE_APPLIED,
            applied_generation=applied_generation,
            result_payload=payload,
            result_digest=digest,
            revision=values[index].revision + 1,
        )
        getattr(harness.store, "_effects")[transition_id] = tuple(values)
        return
    with harness.engine.begin() as connection:
        connection.execute(
            sa.update(WorkflowTransitionEffectDB)
            .where(WorkflowTransitionEffectDB.id == effect_id)
            .values(
                state=EFFECT_STATE_APPLIED,
                applied_generation=applied_generation,
                result_payload=payload,
                result_digest=digest,
                revision=WorkflowTransitionEffectDB.revision + 1,
            )
        )


def _corrupt_final_effect_generation(
    harness: _Harness,
    *,
    transition_id: str,
    effect_id: str,
    applied_generation: int = 1,
) -> None:
    if harness.engine is None:
        values = list(getattr(harness.store, "_effects")[transition_id])
        index = next(index for index, effect in enumerate(values) if effect.effect_id == effect_id)
        values[index] = replace(
            values[index],
            state=EFFECT_STATE_APPLYING,
            applied_generation=applied_generation,
            revision=values[index].revision + 1,
        )
        getattr(harness.store, "_effects")[transition_id] = tuple(values)
        return
    with harness.engine.begin() as connection:
        connection.execute(
            sa.update(WorkflowTransitionEffectDB)
            .where(WorkflowTransitionEffectDB.id == effect_id)
            .values(
                state=EFFECT_STATE_APPLYING,
                applied_generation=applied_generation,
                revision=WorkflowTransitionEffectDB.revision + 1,
            )
        )


def _corrupt_transition_attempt_count(
    harness: _Harness,
    *,
    transition_id: str,
    attempt_count: int,
) -> None:
    if harness.engine is None:
        values = getattr(harness.store, "_transitions")
        object.__setattr__(values[transition_id], "attempt_count", attempt_count)
        return
    with harness.engine.begin() as connection:
        connection.execute(
            sa.update(WorkflowTransitionOutboxDB)
            .where(WorkflowTransitionOutboxDB.id == transition_id)
            .values(
                attempt_count=attempt_count,
                revision=WorkflowTransitionOutboxDB.revision + 1,
            )
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
@pytest.mark.parametrize("corruption", ["missing", "foreign_workflow", "terminal"])
def test_get_active_rejects_an_orphaned_or_misbound_marker(
    kind: str,
    corruption: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"active-orphan-{kind}-{corruption}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    if corruption == "missing":
        _delete_transition_but_keep_binding_marker(harness, transition.transition_id)
    elif harness.engine is None:
        stored = getattr(harness.store, "_transitions")[transition.transition_id]
        object.__setattr__(
            stored,
            "workflow_id" if corruption == "foreign_workflow" else "state",
            "workflow-b" if corruption == "foreign_workflow" else TRANSITION_STATE_COMPLETED,
        )
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(WorkflowTransitionOutboxDB.id == transition.transition_id)
                .values(
                    **(
                        {"workflow_id": "workflow-b"}
                        if corruption == "foreign_workflow"
                        else {"state": TRANSITION_STATE_COMPLETED}
                    )
                )
            )

    with pytest.raises(
        WorkflowTransitionPersistenceError,
        match="binding_marker_orphaned",
    ):
        harness.store.get_active("workflow-a")


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_stage_adopts_first_writer_timestamps_but_not_payload_time_drift(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"stage-planned-at-{kind}")
    transition, effects = _plan(created_at=1_000.0)
    staged = harness.store.stage(transition, effects, receipt_id="command-a")
    restarted_transition, restarted_effects = _plan(created_at=1_234.0)

    adopted = harness.adapter().stage(
        restarted_transition,
        restarted_effects,
        receipt_id="command-a",
    )

    assert adopted == staged
    assert adopted.transition.created_at == 1_000.0
    assert {effect.created_at for effect in adopted.effects} == {1_000.0}

    divergent_effects = (
        WorkflowTransitionEffect.build(
            transition_id=transition.transition_id,
            ordinal=1,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key="task-a",
            payload={
                "task_id": "task-a",
                "attempt_id": "attempt-a",
                "occurred_at": 1_234.0,
            },
            created_at=1_234.0,
        ),
        restarted_effects[-1],
    )
    divergent_transition = WorkflowTransition.build(
        transition_id=transition.transition_id,
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        runtime_id=transition.runtime_id,
        kind=transition.kind,
        command_id=transition.command_id,
        receipt_id=transition.receipt_id,
        admitted_command={"command_id": "command-a", "kind": "advance"},
        request_payload=transition.request_payload,
        effects=divergent_effects,
        expected_revision=transition.expected_revision,
        expected_checkpoint_ref=transition.expected_checkpoint_ref,
        created_at=1_234.0,
    )
    with pytest.raises(WorkflowTransitionPersistenceError, match="stage_conflict"):
        harness.adapter().stage(
            divergent_transition,
            divergent_effects,
            receipt_id="command-a",
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_stage_accepts_only_an_empty_or_exact_receipt_request_fingerprint(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"receipt-request-proof-{kind}")
    transition, effects = _plan()
    if harness.engine is None:
        getattr(harness.store, "_receipts")["command-a"]["request_fingerprint"] = transition.request_fingerprint
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
        stale_result = _effect_result({"task_id": "stale"})
        harness.store.finish_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
            result_payload=stale_result,
            result_digest=workflow_transition_effect_result_digest(stale_result),
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


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_quarantine_holds_marker_and_preserves_ambiguous_effects(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"quarantine-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    claimed = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    assert claimed is not None
    harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    before = harness.store.get(transition.transition_id)
    binding_before, receipt_before = _records(harness)
    assert before is not None

    quarantined = harness.store.quarantine(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=1,
        reason_code="effect_outcome_ambiguous",
    )

    assert quarantined.transition.state == TRANSITION_STATE_QUARANTINED
    assert quarantined.transition.claim_owner == ""
    assert quarantined.transition.claim_expires_at == 0.0
    assert quarantined.transition.claim_generation == 1
    assert quarantined.transition.last_error == "effect_outcome_ambiguous"
    assert quarantined.effects == before.effects
    binding, receipt = _records(harness)
    assert binding == binding_before
    assert binding["active_transition_id"] == transition.transition_id
    assert binding["command_receipt_id"] == "command-a"
    assert receipt["state"] == "pending"
    assert receipt["dispatch_owner"] == ""
    assert receipt["dispatch_lease_expires_at"] == 0.0
    assert receipt["dispatch_generation"] == 1
    assert receipt["last_heartbeat_at"] == quarantined.transition.last_heartbeat_at
    assert receipt["result_status"] == receipt_before["result_status"] == {}
    assert receipt["rejection_reason"] == receipt_before["rejection_reason"] == ""
    assert receipt["outcome_fingerprint"] == receipt_before["outcome_fingerprint"] == ""
    assert (
        harness.store.claim(
            transition.transition_id,
            owner_id="owner-b",
            lease_seconds=30.0,
        )
        is None
    )
    with pytest.raises(WorkflowTransitionPersistenceError, match="lease_conflict"):
        harness.store.heartbeat(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            lease_seconds=30.0,
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_quarantine_allows_revision_and_checkpoint_drift(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"quarantine-drift-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    if harness.engine is None:
        binding = getattr(harness.store, "_bindings")["workflow-a"]
        binding.update(
            runtime_revision=99,
            runtime_checkpoint_ref="unexpected-checkpoint",
            revision=binding["revision"] + 1,
        )
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowControlBindingDB)
                .where(WorkflowControlBindingDB.id == "workflow-a")
                .values(
                    runtime_revision=99,
                    runtime_checkpoint_ref="unexpected-checkpoint",
                    revision=WorkflowControlBindingDB.revision + 1,
                )
            )

    quarantined = harness.store.quarantine(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=1,
        reason_code="binding_revision_drift",
    )

    assert quarantined.transition.state == TRANSITION_STATE_QUARANTINED
    binding, _receipt = _records(harness)
    assert binding["runtime_revision"] == 99
    assert binding["runtime_checkpoint_ref"] == "unexpected-checkpoint"


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("tenant_id", "tenant-b"),
        ("workflow_id", "workflow-b"),
        ("run_id", "run-b"),
        ("runtime_id", "langgraph"),
        ("command_receipt_id", "different-receipt"),
    ],
)
def test_quarantine_rejects_binding_identity_loss_without_mutation(
    kind: str,
    field_name: str,
    field_value: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"quarantine-identity-{kind}-{field_name}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    if harness.engine is None:
        getattr(harness.store, "_bindings")["workflow-a"][field_name] = field_value
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowControlBindingDB)
                .where(WorkflowControlBindingDB.id == "workflow-a")
                .values({field_name: field_value})
            )
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(WorkflowTransitionPersistenceError, match="binding_cas_conflict"):
        harness.store.quarantine(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            reason_code="binding_identity_lost",
        )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("state", "pending"),
        ("dispatch_owner", "owner-b"),
        ("dispatch_generation", 99),
        ("dispatch_lease_expires_at", 1_999.0),
        ("last_heartbeat_at", 1_999.0),
    ],
)
def test_quarantine_requires_exact_receipt_lease_mirror_without_mutation(
    kind: str,
    field_name: str,
    field_value: Any,
    tmp_path: Any,
) -> None:
    harness = _harness(
        kind,
        tmp_path,
        name=f"quarantine-receipt-mirror-{kind}-{field_name}",
    )
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    if harness.engine is None:
        getattr(harness.store, "_receipts")["command-a"][field_name] = field_value
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowControlCommandReceiptDB)
                .where(WorkflowControlCommandReceiptDB.id == "command-a")
                .values({field_name: field_value})
            )
    before = _raw_transition_records(harness, transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(WorkflowTransitionPersistenceError, match="receipt_cas_conflict"):
        harness.store.quarantine(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            reason_code="receipt_mirror_lost",
        )

    assert _raw_transition_records(harness, transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_quarantine_fault_rolls_back_receipt_and_transition(
    kind: str,
    tmp_path: Any,
) -> None:
    def inject(stage: str) -> None:
        if stage == "quarantine_before_commit":
            raise RuntimeError("injected:quarantine_before_commit")

    harness = _harness(
        kind,
        tmp_path,
        name=f"quarantine-fault-{kind}",
        fault_injector=inject,
    )
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(RuntimeError, match="injected:quarantine_before_commit"):
        harness.store.quarantine(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=1,
            reason_code="effect_outcome_ambiguous",
        )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_yield_ready_requires_one_current_generation_nonfinal_effect_proof(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"yield-proof-{kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)
    for effect_id, reason in (
        (effects[0].effect_id, "yield_effect_conflict"),
        (effects[-1].effect_id, "yield_effect_conflict"),
        ("missing-effect", "effect_not_found"),
    ):
        with pytest.raises(WorkflowTransitionPersistenceError, match=reason):
            harness.store.yield_ready(
                transition.transition_id,
                effect_id,
                owner_id="owner-a",
                claim_generation=1,
                available_at=1_000.0,
            )
        assert harness.store.get(transition.transition_id) == before
        assert _records(harness) == records_before

    begun = harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    result = _effect_result({"task_id": "task-a", "queue_state": "reserved"})
    harness.store.finish_effect(
        transition.transition_id,
        begun.effect_id,
        owner_id="owner-a",
        claim_generation=1,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
    )
    yielded = harness.store.yield_ready(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
        available_at=1_000.0,
    )
    assert yielded.transition.state == TRANSITION_STATE_READY
    binding, receipt = _records(harness)
    assert binding["active_transition_id"] == transition.transition_id
    assert receipt["state"] == "pending"
    assert receipt["dispatch_generation"] == 1

    claimed = harness.store.claim(
        transition.transition_id,
        owner_id="owner-b",
        lease_seconds=30.0,
    )
    assert claimed is not None and claimed.transition.claim_generation == 2
    current = harness.store.get(transition.transition_id)
    with pytest.raises(WorkflowTransitionPersistenceError, match="yield_effect_conflict"):
        harness.store.yield_ready(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-b",
            claim_generation=2,
            available_at=1_000.0,
        )
    assert harness.store.get(transition.transition_id) == current
    with pytest.raises(WorkflowTransitionPersistenceError, match="lease_conflict"):
        harness.store.yield_ready(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
            available_at=1_000.0,
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("result_kind", "reason"),
    [
        ("raw", "result_envelope_invalid"),
        ("unhashable_mode", "result_envelope_invalid"),
        ("wrong_stage_count", "stage_attempt_conflict"),
    ],
)
def test_finish_effect_enforces_canonical_envelope_and_exact_stage_attempt(
    kind: str,
    result_kind: str,
    reason: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"finish-envelope-{kind}-{result_kind}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    if result_kind == "raw":
        result = {"task_id": "raw-unproved"}
    elif result_kind == "unhashable_mode":
        result = {
            "schema": "ananta.workflow-transition-effect-result.v1",
            "mode": [],
            "effect_result": {"task_id": "task-a"},
            "effect_proof": {"downstream_revision": 1},
            "stage_attempt_count": 1,
        }
    else:
        result = _effect_result({"task_id": "task-a"}, stage_attempt_count=2)
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(WorkflowTransitionPersistenceError, match=reason):
        harness.store.finish_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
            result_payload=result,
            result_digest=workflow_transition_effect_result_digest(result),
        )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_begin_effect_grants_only_one_nonfinal_execution_per_claim(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"begin-one-effect-{kind}")
    transition, effects = _plan(effect_count=2)
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    result = _effect_result({"task_id": "task-1"})
    harness.store.finish_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
    )
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(WorkflowTransitionPersistenceError, match="claim_progress_conflict"):
        harness.store.begin_effect(
            transition.transition_id,
            effects[1].effect_id,
            owner_id="owner-a",
            claim_generation=1,
        )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before
    harness.store.yield_ready(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
        available_at=1_000.0,
    )
    claimed = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    assert claimed is not None and claimed.transition.claim_generation == 2
    begun = harness.store.begin_effect(
        transition.transition_id,
        effects[1].effect_id,
        owner_id="owner-a",
        claim_generation=2,
    )
    assert begun.state == EFFECT_STATE_APPLYING
    assert begun.applied_generation == 2


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("first_state", ["planned", "applying"])
def test_begin_effect_rejects_an_out_of_order_later_applied_stage(
    kind: str,
    first_state: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"begin-order-{kind}-{first_state}")
    transition, effects = _plan(effect_count=2)
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    if first_state == "applying":
        harness.store.begin_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
        )
    _corrupt_effect_as_applied(
        harness,
        transition_id=transition.transition_id,
        effect_id=effects[1].effect_id,
        applied_generation=1,
    )
    before = _raw_transition_records(harness, transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(WorkflowTransitionPersistenceError, match="effect_order_conflict"):
        harness.store.begin_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
        )

    assert _raw_transition_records(harness, transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("target", "field_name", "field_value", "reason"),
    [
        ("binding", "active_transition_id", "", "binding_cas_conflict"),
        ("binding", "tenant_id", "tenant-b", "binding_cas_conflict"),
        ("binding", "workflow_id", "workflow-b", "binding_cas_conflict"),
        ("binding", "run_id", "run-b", "binding_cas_conflict"),
        ("binding", "runtime_id", "langgraph", "binding_cas_conflict"),
        ("binding", "command_receipt_id", "other", "binding_cas_conflict"),
        ("binding", "runtime_revision", 8, "binding_cas_conflict"),
        ("binding", "runtime_checkpoint_ref", "checkpoint-8", "binding_cas_conflict"),
        ("receipt", "dispatch_generation", 99, "receipt_cas_conflict"),
    ],
)
def test_begin_effect_requires_full_binding_and_receipt_authority_without_mutation(
    kind: str,
    target: str,
    field_name: str,
    field_value: Any,
    reason: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"begin-authority-{kind}-{target}-{field_name}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    mutate = _mutate_binding if target == "binding" else _mutate_receipt
    mutate(harness, **{field_name: field_value})
    before = _raw_transition_records(harness, transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(WorkflowTransitionPersistenceError, match=reason):
        harness.store.begin_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
        )

    assert _raw_transition_records(harness, transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("binding", "binding_not_found"),
        ("receipt", "receipt_not_found"),
    ],
)
def test_begin_effect_reports_missing_authority_consistently_without_mutation(
    kind: str,
    target: str,
    reason: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"begin-missing-{kind}-{target}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    before = _raw_transition_records(harness, transition.transition_id)
    if harness.engine is None:
        collection = "_bindings" if target == "binding" else "_receipts"
        key = "workflow-a" if target == "binding" else "command-a"
        getattr(harness.store, collection).pop(key)
    else:
        model = WorkflowControlBindingDB if target == "binding" else WorkflowControlCommandReceiptDB
        identifier = "workflow-a" if target == "binding" else "command-a"
        with harness.engine.begin() as connection:
            connection.execute(sa.delete(model).where(model.id == identifier))

    with pytest.raises(WorkflowTransitionPersistenceError, match=reason):
        harness.store.begin_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
        )

    assert _raw_transition_records(harness, transition.transition_id) == before


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("operation", ["release", "yield"])
def test_requeue_paths_require_current_binding_authority_without_mutation(
    kind: str,
    operation: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"requeue-authority-{kind}-{operation}")
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    if operation == "yield":
        harness.store.begin_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
        )
        result = _effect_result({"task_id": "task-a"})
        harness.store.finish_effect(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
            result_payload=result,
            result_digest=workflow_transition_effect_result_digest(result),
        )
    _mutate_binding(harness, runtime_revision=8)
    before = _raw_transition_records(harness, transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(WorkflowTransitionPersistenceError, match="binding_cas_conflict"):
        if operation == "release":
            harness.store.release(
                transition.transition_id,
                owner_id="owner-a",
                claim_generation=1,
                reason_code="retry",
                retry_at=1_002.0,
            )
        else:
            harness.store.yield_ready(
                transition.transition_id,
                effects[0].effect_id,
                owner_id="owner-a",
                claim_generation=1,
                available_at=1_000.0,
            )

    assert _raw_transition_records(harness, transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_yield_fault_rolls_back_transition_and_receipt_mirror(
    kind: str,
    tmp_path: Any,
) -> None:
    def inject(stage: str) -> None:
        if stage == "yield_before_commit":
            raise RuntimeError("injected:yield_before_commit")

    harness = _harness(
        kind,
        tmp_path,
        name=f"yield-fault-{kind}",
        fault_injector=inject,
    )
    transition, effects = _plan()
    harness.store.stage(transition, effects, receipt_id="command-a")
    assert harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    result = _effect_result({"task_id": "task-a"})
    harness.store.finish_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
    )
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(RuntimeError, match="injected:yield_before_commit"):
        harness.store.yield_ready(
            transition.transition_id,
            effects[0].effect_id,
            owner_id="owner-a",
            claim_generation=1,
            available_at=1_000.0,
        )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before


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
    result = _effect_result({"task_id": "task-a", "queue_state": "reserved"})
    harness.store.finish_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
    )
    harness.store.yield_ready(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
        available_at=1_000.0,
    )
    snapshot = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    assert snapshot is not None and snapshot.transition.claim_generation == 2
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
        finalization_proof=_FINALIZATION_PROOF,
        receipt_result=status,
    )
    return transition, outcome


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("operation", ["reject", "finalize"])
@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("tenant_id", "tenant-b"),
        ("workflow_id", "workflow-b"),
        ("run_id", "run-b"),
        ("runtime_id", "langgraph"),
    ],
)
def test_terminal_mutations_require_exact_binding_identity_without_mutation(
    kind: str,
    operation: str,
    field_name: str,
    field_value: str,
    tmp_path: Any,
) -> None:
    harness = _harness(
        kind,
        tmp_path,
        name=f"terminal-identity-{kind}-{operation}-{field_name}",
    )
    if operation == "finalize":
        transition, _outcome = _prepare_for_finalize(harness)
        generation = 2
    else:
        transition, effects = _plan()
        harness.store.stage(transition, effects, receipt_id="command-a")
        assert harness.store.claim(
            transition.transition_id,
            owner_id="owner-a",
            lease_seconds=30.0,
        )
        generation = 1
    _mutate_binding(harness, **{field_name: field_value})
    before = _raw_transition_records(harness, transition.transition_id)
    records_before = _records(harness)

    with pytest.raises(WorkflowTransitionPersistenceError, match="binding_cas_conflict"):
        if operation == "reject":
            harness.store.reject(
                transition.transition_id,
                owner_id="owner-a",
                claim_generation=generation,
                reason_code="denied",
            )
        else:
            status = {
                "status": "running",
                "revision": 8,
                "checkpoint_ref": "checkpoint-8",
            }
            harness.store.finalize(
                transition.transition_id,
                owner_id="owner-a",
                claim_generation=generation,
                binding_status=status,
                checkpoint_ref="checkpoint-8",
                finalization_proof=_FINALIZATION_PROOF,
                receipt_result=status,
            )

    assert _raw_transition_records(harness, transition.transition_id) == before
    assert _records(harness) == records_before


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
            claim_generation=2,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            finalization_proof=_FINALIZATION_PROOF,
            outcome_fingerprint=outcome,
            receipt_result={**status, "status": "unrelated"},
        )

    completed = harness.adapter().finalize(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=2,
        binding_status=status,
        checkpoint_ref="checkpoint-8",
        finalization_proof=_FINALIZATION_PROOF,
        outcome_fingerprint=outcome,
        receipt_result=status,
    )

    assert completed.transition.state == TRANSITION_STATE_COMPLETED
    assert completed.transition.result_status == status
    assert completed.effects[-1].state == EFFECT_STATE_APPLIED
    assert thaw_json(completed.effects[-1].result_payload)["finalization_proof"] == (_FINALIZATION_PROOF)
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
    assert receipt["dispatch_generation"] == 2
    assert thaw_json(completed.effects[-1].result_payload)["finalization_stage_attempt_count"] == 1


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_receiptless_finalize_projects_and_persists_full_public_proof(
    kind: str,
    tmp_path: Any,
) -> None:
    projector = _SentinelPublicProjector()
    harness = _harness(
        kind,
        tmp_path,
        name=f"receiptless-finalize-{kind}",
        receipt_projector=projector,
    )
    if harness.engine is None:
        getattr(harness.store, "_bindings")["workflow-a"]["command_receipt_id"] = ""
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowControlBindingDB)
                .where(WorkflowControlBindingDB.id == "workflow-a")
                .values(command_receipt_id="")
            )
    transition, effects = _receiptless_plan()
    harness.store.stage(transition, effects)
    claimed = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    assert claimed is not None
    applying = harness.store.begin_effect(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
    )
    result = _effect_result({"task_id": "task-a", "queue_state": "reserved"})
    harness.store.finish_effect(
        transition.transition_id,
        applying.effect_id,
        owner_id="owner-a",
        claim_generation=1,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
    )
    harness.store.yield_ready(
        transition.transition_id,
        effects[0].effect_id,
        owner_id="owner-a",
        claim_generation=1,
        available_at=1_000.0,
    )
    final_claim = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    assert final_claim is not None and final_claim.transition.claim_generation == 2
    raw_status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }
    public_status = {
        **raw_status,
        "checkpoint_ref": "public:8",
        "projection": "canonical",
    }

    completed = harness.store.finalize(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=2,
        binding_status=raw_status,
        checkpoint_ref="checkpoint-8",
        finalization_proof=_FINALIZATION_PROOF,
    )

    expected_outcome = workflow_transition_outcome_fingerprint(
        completed.transition,
        completed.effects,
        binding_status=raw_status,
        checkpoint_ref="checkpoint-8",
        finalization_proof=_FINALIZATION_PROOF,
        public_status=public_status,
    )
    assert completed.transition.outcome_fingerprint == expected_outcome
    assert thaw_json(completed.transition.result_status) == raw_status
    final_result = thaw_json(completed.effects[-1].result_payload)
    assert final_result["public_status"] == public_status
    assert final_result["finalization_stage_attempt_count"] == 1
    assert final_result["finalization_proof"] == _FINALIZATION_PROOF
    assert final_result["outcome_fingerprint"] == expected_outcome
    assert final_result["receipt_completed"] is False
    binding, receipt = _records(harness)
    assert binding["last_status"] == raw_status
    assert binding["public_status"] == public_status
    assert binding["last_transition_outcome_fingerprint"] == expected_outcome
    assert receipt["state"] == "pending"
    assert receipt["transition_id"] == ""
    assert len(projector.calls) == 1
    assert projector.calls[0]["transition"].receipt_id == ""
    assert projector.calls[0]["binding_status"] == raw_status


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("invalid_outcome", [None, False, 0, "abc", "g" * 64])
def test_finalize_accepts_only_empty_or_sha256_optional_outcome_proof(
    kind: str,
    invalid_outcome: Any,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"invalid-outcome-{kind}-{invalid_outcome!s}")
    transition, _outcome = _prepare_for_finalize(harness)
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }

    with pytest.raises(WorkflowTransitionPersistenceError, match="outcome_fingerprint_invalid"):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=2,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            finalization_proof=_FINALIZATION_PROOF,
            outcome_fingerprint=invalid_outcome,
            receipt_result=status,
        )

    snapshot = harness.store.get(transition.transition_id)
    assert snapshot is not None and snapshot.transition.state == TRANSITION_STATE_APPLYING


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("proof_kind", ["missing", "none", "empty", "nan", "oversize"])
def test_finalize_requires_bounded_structural_proof_without_mutation(
    kind: str,
    proof_kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"finalization-proof-{kind}-{proof_kind}")
    transition, _outcome = _prepare_for_finalize(harness)
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }
    arguments = {
        "owner_id": "owner-a",
        "claim_generation": 2,
        "binding_status": status,
        "checkpoint_ref": "checkpoint-8",
        "receipt_result": status,
    }

    if proof_kind == "missing":
        with pytest.raises(TypeError, match="finalization_proof"):
            harness.store.finalize(transition.transition_id, **arguments)
    else:
        proof: Any = {
            "none": None,
            "empty": {},
            "nan": {"value": float("nan")},
            "oversize": {"value": "x" * 524_289},
        }[proof_kind]
        with pytest.raises(
            WorkflowTransitionPersistenceError,
            match="finalization_proof",
        ):
            harness.store.finalize(
                transition.transition_id,
                finalization_proof=proof,
                **arguments,
            )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_finalize_defensively_rejects_digest_valid_noncanonical_effect_result(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"finalize-malformed-effect-{kind}")
    transition, _outcome = _prepare_for_finalize(harness)
    current = harness.store.get(transition.transition_id)
    assert current is not None
    _corrupt_effect_result(
        harness,
        transition_id=transition.transition_id,
        effect_id=current.effects[0].effect_id,
        result_payload={
            "schema": "ananta.workflow-transition-effect-result.v1",
            "mode": {},
            "effect_result": {"task_id": "digest-valid-but-unproved"},
            "effect_proof": {"downstream_revision": 1},
            "stage_attempt_count": 1,
        },
    )
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }

    with pytest.raises(WorkflowTransitionPersistenceError, match="result_envelope_invalid"):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=2,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            finalization_proof=_FINALIZATION_PROOF,
            receipt_result=status,
        )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("stage_attempt_count", [0, 2])
def test_finalize_rejects_digest_valid_under_or_over_counted_effect_attempts(
    kind: str,
    stage_attempt_count: int,
    tmp_path: Any,
) -> None:
    harness = _harness(
        kind,
        tmp_path,
        name=f"finalize-effect-count-{kind}-{stage_attempt_count}",
    )
    transition, _outcome = _prepare_for_finalize(harness)
    current = harness.store.get(transition.transition_id)
    assert current is not None
    _corrupt_effect_result(
        harness,
        transition_id=transition.transition_id,
        effect_id=current.effects[0].effect_id,
        result_payload={
            "schema": "ananta.workflow-transition-effect-result.v1",
            "mode": "execute",
            "effect_result": {"task_id": "task-a"},
            "effect_proof": {"downstream_revision": 1},
            "stage_attempt_count": stage_attempt_count,
        },
    )
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }

    with pytest.raises(WorkflowTransitionPersistenceError, match="result_envelope_invalid"):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=2,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            finalization_proof=_FINALIZATION_PROOF,
            receipt_result=status,
        )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_finalize_rejects_zero_finalization_stage_attempts_without_mutation(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"finalize-zero-stage-attempt-{kind}")
    transition, _outcome = _prepare_for_finalize(harness)
    _corrupt_transition_attempt_count(
        harness,
        transition_id=transition.transition_id,
        attempt_count=1,
    )
    before = _raw_transition_records(harness, transition.transition_id)
    records_before = _records(harness)
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }

    with pytest.raises(WorkflowTransitionPersistenceError, match="header_attempt_conflict"):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=2,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            finalization_proof=_FINALIZATION_PROOF,
            receipt_result=status,
        )

    assert _raw_transition_records(harness, transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("applied_generation", [1, 2])
def test_finalize_rejects_any_applying_final_effect_generation(
    kind: str,
    applied_generation: int,
    tmp_path: Any,
) -> None:
    harness = _harness(
        kind,
        tmp_path,
        name=f"finalize-applying-final-{kind}-{applied_generation}",
    )
    transition, _outcome = _prepare_for_finalize(harness)
    current = harness.store.get(transition.transition_id)
    assert current is not None
    _corrupt_final_effect_generation(
        harness,
        transition_id=transition.transition_id,
        effect_id=current.effects[-1].effect_id,
        applied_generation=applied_generation,
    )
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }

    with pytest.raises(WorkflowTransitionPersistenceError, match="finalize_effect_conflict"):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=2,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            finalization_proof=_FINALIZATION_PROOF,
            receipt_result=status,
        )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_finalize_rejects_non_monotonic_applied_generation_prefix(
    kind: str,
    tmp_path: Any,
) -> None:
    harness = _harness(kind, tmp_path, name=f"finalize-generation-prefix-{kind}")
    transition, effects = _plan(effect_count=2)
    harness.store.stage(transition, effects, receipt_id="command-a")
    for generation, effect in enumerate(effects[:-1], start=1):
        claimed = harness.store.claim(
            transition.transition_id,
            owner_id="owner-a",
            lease_seconds=30.0,
        )
        assert claimed is not None and claimed.transition.claim_generation == generation
        harness.store.begin_effect(
            transition.transition_id,
            effect.effect_id,
            owner_id="owner-a",
            claim_generation=generation,
        )
        result = _effect_result({"task_id": f"task-{generation}"})
        harness.store.finish_effect(
            transition.transition_id,
            effect.effect_id,
            owner_id="owner-a",
            claim_generation=generation,
            result_payload=result,
            result_digest=workflow_transition_effect_result_digest(result),
        )
        harness.store.yield_ready(
            transition.transition_id,
            effect.effect_id,
            owner_id="owner-a",
            claim_generation=generation,
            available_at=1_000.0,
        )
    final_claim = harness.store.claim(
        transition.transition_id,
        owner_id="owner-a",
        lease_seconds=30.0,
    )
    assert final_claim is not None and final_claim.transition.claim_generation == 3
    if harness.engine is None:
        values = list(getattr(harness.store, "_effects")[transition.transition_id])
        values[0] = replace(values[0], applied_generation=2, revision=values[0].revision + 1)
        values[1] = replace(values[1], applied_generation=1, revision=values[1].revision + 1)
        getattr(harness.store, "_effects")[transition.transition_id] = tuple(values)
    else:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.update(WorkflowTransitionEffectDB)
                .where(WorkflowTransitionEffectDB.id == effects[0].effect_id)
                .values(applied_generation=2, revision=WorkflowTransitionEffectDB.revision + 1)
            )
            connection.execute(
                sa.update(WorkflowTransitionEffectDB)
                .where(WorkflowTransitionEffectDB.id == effects[1].effect_id)
                .values(applied_generation=1, revision=WorkflowTransitionEffectDB.revision + 1)
            )
    before = harness.store.get(transition.transition_id)
    records_before = _records(harness)
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }

    with pytest.raises(WorkflowTransitionPersistenceError, match="result_envelope_invalid"):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=3,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            finalization_proof=_FINALIZATION_PROOF,
            receipt_result=status,
        )

    assert harness.store.get(transition.transition_id) == before
    assert _records(harness) == records_before


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
        finalization_proof=_FINALIZATION_PROOF,
        receipt_result=public_status,
    )

    missing_identity = dict(public_status)
    missing_identity.pop("workflow_id")
    mismatched_outcome = workflow_transition_outcome_fingerprint(
        snapshot.transition,
        snapshot.effects,
        binding_status=raw_status,
        checkpoint_ref=raw_status["checkpoint_ref"],
        finalization_proof=_FINALIZATION_PROOF,
        receipt_result=missing_identity,
    )
    with pytest.raises(WorkflowTransitionPersistenceError, match="receipt_projection_mismatch"):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=2,
            binding_status=raw_status,
            checkpoint_ref=raw_status["checkpoint_ref"],
            finalization_proof=_FINALIZATION_PROOF,
            outcome_fingerprint=mismatched_outcome,
            receipt_result=missing_identity,
        )

    completed = harness.store.finalize(
        transition.transition_id,
        owner_id="owner-a",
        claim_generation=2,
        binding_status=raw_status,
        checkpoint_ref=raw_status["checkpoint_ref"],
        finalization_proof=_FINALIZATION_PROOF,
        outcome_fingerprint=outcome,
        receipt_result=public_status,
    )
    assert thaw_json(completed.transition.result_status) == raw_status
    binding, receipt = _records(harness)
    assert binding["last_status"] == raw_status
    assert binding["public_status"] == public_status
    assert receipt["result_status"] == public_status
    assert receipt["outcome_fingerprint"] == outcome
    assert thaw_json(completed.effects[-1].result_payload)["public_status"] == public_status


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
        finalization_proof=_FINALIZATION_PROOF,
        receipt_result=unrelated_status,
    )

    with pytest.raises(
        WorkflowTransitionPersistenceError,
        match="binding_cas_conflict",
    ):
        harness.store.finalize(
            transition.transition_id,
            owner_id="owner-a",
            claim_generation=2,
            binding_status=unrelated_status,
            checkpoint_ref="unrelated-checkpoint-8",
            finalization_proof=_FINALIZATION_PROOF,
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
            claim_generation=2,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            finalization_proof=_FINALIZATION_PROOF,
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
