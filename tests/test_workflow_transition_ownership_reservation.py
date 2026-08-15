from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlmodel import SQLModel, create_engine

from agent.db_models.workflow_runtime import (
    WorkflowExecutionAttemptHistoryDB,
    WorkflowExecutionOwnershipDB,
    WorkflowRetryBudgetDB,
    WorkflowRetryConsumptionDB,
    WorkflowTransitionOwnershipReservationDB,
)
from agent.services import workflow_transition_ownership_reservation as reservation
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.ownership import (
    ExecutionOwnership,
    InMemoryExecutionOwnershipStore,
    SQLiteExecutionOwnershipStore,
    WorkflowTransitionOwnershipReservationConflict,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipReservationStale,
    WorkflowTransitionOwnershipReservationUnavailable,
)
from agent.services.workflow_runtime.sqlalchemy_ownership import (
    SQLAlchemyExecutionOwnershipStore,
)
from agent.services.workflow_runtime.sqlalchemy_support import stable_row_id
from agent.services.workflow_transition_effect_execution import (
    EffectAlreadyApplied,
    EffectApplied,
    EffectExecutable,
    EffectQuarantine,
    EffectRetry,
    WorkflowTransitionEffectAttempt,
    WorkflowTransitionEffectObservation,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    TRANSITION_KIND_ADVANCE,
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_APPLYING,
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_result_digest,
    workflow_transition_effect_result_envelope,
    workflow_transition_id,
)
from agent.services.workflow_transition_ownership_reservation import (
    WorkflowTransitionOwnershipReservationError,
    WorkflowTransitionOwnershipReservationExecutor,
    WorkflowTransitionOwnershipReservationObserver,
    assert_current_workflow_transition_ownership_reservation_validity,
    assert_durable_workflow_transition_ownership_reservation_proof,
    build_workflow_transition_ownership_reservation_effect,
    workflow_transition_ownership_reservation_receipt_from_result,
)

_TABLES = [
    WorkflowExecutionOwnershipDB.__table__,
    WorkflowExecutionAttemptHistoryDB.__table__,
    WorkflowRetryBudgetDB.__table__,
    WorkflowRetryConsumptionDB.__table__,
    WorkflowTransitionOwnershipReservationDB.__table__,
]
_KNOWN_TRANSITION_ID = "wft-db42f659175fd3eee2521061d9c89a9b3be58d08c76a2eda8d4ec9f41e380d3b"
_KNOWN_INTENT_DIGEST = "27b19f93ba9387637deb7a805cd939ce5ca97f053d50ee96a4de2c4b7f2c11bf"
_KNOWN_OWNER_ID = "wfto-23a52f13f648b1d803faca99315a671f82b25d7a9617fd4e9f55540e891d3e57"
_KNOWN_OPERATION_FENCE_ID = "wftof-f59592ebb71977d3f1861de9f29a6427fc00e056c37433b8513d3ba2e9a073b9"
_KNOWN_EFFECT_ID = "wfx-cd319d03b0bbde9f595c7c1b74dc367d6694919629ec5e35d58c2d70c813121b"
_KNOWN_ATTEMPT_ID = "wfta-0e18cb3f0010b5afef0ec6cf17183f86990a33b35e17fe6afb198690abb82913"
_KNOWN_RECEIPT_ID = "wftor-486a204691aa30a31dfabb60b76745b59f08b9feb19bf3bbc68a192bc9f2ae31"
_MAX_OWNERSHIP_COUNTER = 2_147_483_647
_KNOWN_EFFECT_BYTES = (
    '{"applied_generation":0,"created_at":1000.0,"effect_id":"wfx-cd319d03b0bbde9f595c7c1b74dc367d'
    '6694919629ec5e35d58c2d70c813121b","idempotency_key":"wftof-f59592ebb71977d3f1861de9f29a6427fc'
    '00e056c37433b8513d3ba2e9a073b9","kind":"ownership_reserve","ordinal":1,"payload":{"attempt_id":"'
    'wfta-0e18cb3f0010b5afef0ec6cf17183f86990a33b35e17fe6afb198690abb82913","effect_id":"wfx-cd319'
    'd03b0bbde9f595c7c1b74dc367d6694919629ec5e35d58c2d70c813121b","effect_ordinal":1,"lease_seconds":'
    '100.0,"maximum_retries":3,"operation_fence_id":"wftof-f59592ebb71977d3f1861de9f29a6427fc00e056c'
    '37433b8513d3ba2e9a073b9","owner_id":"wfto-23a52f13f648b1d803faca99315a671f82b25d7a9617fd4e9f'
    '55540e891d3e57","ownership_intent_digest":"27b19f93ba9387637deb7a805cd939ce5ca97f053d50ee96a4de2c4b'
    '7f2c11bf","receipt_id":"wftor-486a204691aa30a31dfabb60b76745b59f08b9feb19bf3bbc68a192bc9f2ae31",'
    '"retry_id":"wftof-f59592ebb71977d3f1861de9f29a6427fc00e056c37433b8513d3ba2e9a073b9","run_id":"'
    'run-a","runtime_id":"ananta-native","schema":"ananta.workflow_transition_ownership_reservation_effect.v1",'
    '"step_id":"step-a","tenant_id":"tenant-a","transition_id":"wft-db42f659175fd3eee2521061d9c89a9b3be58d'
    '08c76a2eda8d4ec9f41e380d3b","workflow_id":"workflow-a"},"payload_digest":"083ee0a7383a6561dc28869ba05'
    'c27f23ccfdbb7c27d9158148e09f371b8eed6","result_digest":"","result_payload":{},"revision":1,"schema":"'
    'ananta.workflow-transition-effect.v1","state":"planned","transition_id":"wft-db42f659175fd3eee2521061d9c89'
    'a9b3be58d08c76a2eda8d4ec9f41e380d3b","updated_at":1000.0}'
)


@dataclass
class _StoreCase:
    name: str
    store: Any
    engine: sa.Engine | None = None
    path: Path | None = None


@pytest.fixture(params=("memory", "sqlite", "sql"))
def reservation_store(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> _StoreCase:
    if request.param == "memory":
        return _StoreCase("memory", InMemoryExecutionOwnershipStore())
    if request.param == "sqlite":
        path = tmp_path / "transition-ownership.sqlite"
        store = SQLiteExecutionOwnershipStore(path)
        request.addfinalizer(store.close)
        return _StoreCase("sqlite", store, path=path)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'transition-ownership-sql.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    request.addfinalizer(engine.dispose)
    return _StoreCase("sql", SQLAlchemyExecutionOwnershipStore(engine), engine=engine)


class _Heartbeat:
    def heartbeat(self) -> None:
        return None


def _plan(
    *,
    identity_key: str = "ownership-transition-a",
    step_id: str = "step-a",
    lease_seconds: float = 100.0,
    maximum_retries: int = 3,
    runtime_id: str = TRANSITION_RUNTIME_NATIVE,
) -> tuple[WorkflowTransition, WorkflowTransitionEffect]:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        kind=TRANSITION_KIND_ADVANCE,
        identity_key=identity_key,
    )
    effect = build_workflow_transition_ownership_reservation_effect(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        ordinal=1,
        step_id=step_id,
        lease_seconds=lease_seconds,
        maximum_retries=maximum_retries,
        planned_at=1_000.0,
    )
    transition = WorkflowTransition.build(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        kind=TRANSITION_KIND_ADVANCE,
        request_payload={"request_id": identity_key},
        effects=(effect,),
        expected_revision=0,
        expected_checkpoint_ref="checkpoint-0",
        created_at=1_000.0,
    )
    return transition, effect


def _claimed(transition: WorkflowTransition, generation: int) -> WorkflowTransition:
    return replace(
        transition,
        state=TRANSITION_STATE_APPLYING,
        claim_owner=f"runner-{generation}",
        claim_generation=generation,
        claim_expires_at=1_200.0 + generation,
        last_heartbeat_at=1_000.0 + generation,
        attempt_count=generation,
        revision=transition.revision + generation,
        updated_at=1_000.0 + generation,
    )


def _applying(effect: WorkflowTransitionEffect, generation: int) -> WorkflowTransitionEffect:
    return replace(
        effect,
        state=EFFECT_STATE_APPLYING,
        applied_generation=generation,
        revision=effect.revision + 1,
        updated_at=1_000.0 + generation,
    )


def _observation(
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
) -> WorkflowTransitionEffectObservation:
    return WorkflowTransitionEffectObservation(
        transition,
        effect,
        transition.claim_generation,
    )


def _attempt(
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
) -> WorkflowTransitionEffectAttempt:
    return WorkflowTransitionEffectAttempt(
        transition,
        effect,
        transition.claim_generation,
    )


def _intent(
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
) -> WorkflowTransitionOwnershipReservationIntent:
    return reservation._intent_from_effect(effect, transition=transition)


def _persisted_effect(
    effect: WorkflowTransitionEffect,
    result: EffectApplied | EffectAlreadyApplied,
    *,
    generation: int,
    mode: str = "execute",
) -> WorkflowTransitionEffect:
    envelope = workflow_transition_effect_result_envelope(
        mode=mode,
        result_payload=result.result_payload,
        proof_payload=result.proof_payload,
        stage_attempt_count=generation,
    )
    return replace(
        effect,
        state=EFFECT_STATE_APPLIED,
        applied_generation=generation,
        result_payload=envelope,
        result_digest=workflow_transition_effect_result_digest(envelope),
        revision=effect.revision + 2,
        updated_at=1_010.0 + generation,
    )


def _initial_execution(
    store: Any,
    *,
    runtime_id: str = TRANSITION_RUNTIME_NATIVE,
    identity_key: str = "ownership-transition-a",
    step_id: str = "step-a",
) -> tuple[
    WorkflowTransition,
    WorkflowTransitionEffect,
    EffectExecutable,
    EffectApplied,
    WorkflowTransitionOwnershipReservationIntent,
]:
    transition, planned = _plan(
        runtime_id=runtime_id,
        identity_key=identity_key,
        step_id=step_id,
    )
    claimed = _claimed(transition, 1)
    observed = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(_observation(claimed, planned), heartbeat=_Heartbeat())
    assert type(observed) is EffectExecutable
    applying = _applying(planned, 1)
    applied = WorkflowTransitionOwnershipReservationExecutor(
        authority=store,
        clock=lambda: 1_002.0,
    ).execute(
        _attempt(claimed, applying),
        executable=observed,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied
    return claimed, applying, observed, applied, _intent(claimed, applying)


def _delete_current(case: _StoreCase, intent: WorkflowTransitionOwnershipReservationIntent) -> None:
    key = (intent.tenant_id, intent.run_id, intent.step_id)
    if case.name == "memory":
        case.store._current.pop(key)
        return
    if case.name == "sqlite":
        case.store._connection.execute(
            """
            DELETE FROM workflow_execution_ownership
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
            """,
            key,
        )
        return
    assert case.engine is not None
    with Session(case.engine) as session, session.begin():
        session.execute(
            sa.delete(WorkflowExecutionOwnershipDB).where(
                WorkflowExecutionOwnershipDB.tenant_id == intent.tenant_id,
                WorkflowExecutionOwnershipDB.run_id == intent.run_id,
                WorkflowExecutionOwnershipDB.step_id == intent.step_id,
            )
        )


def _delete_current_and_history(
    case: _StoreCase,
    intent: WorkflowTransitionOwnershipReservationIntent,
) -> None:
    _delete_current(case, intent)
    key = (intent.tenant_id, intent.run_id, intent.step_id)
    if case.name == "memory":
        case.store._history.pop(key)
        return
    if case.name == "sqlite":
        case.store._connection.execute(
            """
            DELETE FROM workflow_execution_attempt_history
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
            """,
            key,
        )
        return
    assert case.engine is not None
    with Session(case.engine) as session, session.begin():
        session.execute(
            sa.delete(WorkflowExecutionAttemptHistoryDB).where(
                WorkflowExecutionAttemptHistoryDB.tenant_id == intent.tenant_id,
                WorkflowExecutionAttemptHistoryDB.run_id == intent.run_id,
                WorkflowExecutionAttemptHistoryDB.step_id == intent.step_id,
            )
        )


def _stored_receipt_records(case: _StoreCase) -> tuple[tuple[str, str, str], ...]:
    if case.name == "memory":
        receipts = case.store._transition_reservation_receipts.values()
        return tuple(
            sorted(
                (
                    receipt.receipt_id,
                    receipt.receipt_digest,
                    canonical_json(receipt.to_dict()),
                )
                for receipt in receipts
            )
        )
    if case.name == "sqlite":
        rows = case.store._connection.execute(
            """
            SELECT receipt_id, receipt_digest, receipt_json
            FROM workflow_transition_ownership_reservations
            ORDER BY receipt_id
            """
        ).fetchall()
        return tuple(
            (
                str(row["receipt_id"]),
                str(row["receipt_digest"]),
                canonical_json(json.loads(str(row["receipt_json"]))),
            )
            for row in rows
        )
    assert case.engine is not None
    with Session(case.engine) as session:
        rows = (
            session.execute(
                sa.select(WorkflowTransitionOwnershipReservationDB).order_by(
                    WorkflowTransitionOwnershipReservationDB.receipt_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(
            (
                row.receipt_id,
                row.receipt_digest,
                canonical_json(dict(row.receipt)),
            )
            for row in rows
        )


def _sql_reservation_state(engine: sa.Engine) -> tuple[object, ...]:
    with Session(engine) as session:
        current = session.execute(sa.select(WorkflowExecutionOwnershipDB)).scalars().all()
        history = session.execute(sa.select(WorkflowExecutionAttemptHistoryDB)).scalars().all()
        budgets = session.execute(sa.select(WorkflowRetryBudgetDB)).scalars().all()
        consumptions = session.execute(sa.select(WorkflowRetryConsumptionDB)).scalars().all()
        receipts = session.execute(sa.select(WorkflowTransitionOwnershipReservationDB)).scalars().all()
        return (
            tuple(
                sorted(
                    (
                        row.id,
                        row.revision,
                        row.fencing_token,
                        canonical_json(dict(row.ownership)),
                    )
                    for row in current
                )
            ),
            tuple(
                sorted(
                    (
                        row.id,
                        row.revision,
                        row.fencing_token,
                        canonical_json(dict(row.ownership)),
                    )
                    for row in history
                )
            ),
            tuple(
                sorted(
                    (
                        row.id,
                        row.tenant_id,
                        row.run_id,
                        row.used,
                        row.maximum,
                        row.revision,
                        row.updated_at,
                    )
                    for row in budgets
                )
            ),
            tuple(
                sorted(
                    (
                        row.id,
                        row.tenant_id,
                        row.run_id,
                        row.retry_id,
                        row.category,
                        row.consumed_at,
                    )
                    for row in consumptions
                )
            ),
            tuple(
                sorted(
                    (
                        row.receipt_id,
                        row.receipt_digest,
                        canonical_json(dict(row.receipt)),
                    )
                    for row in receipts
                )
            ),
        )


def _exact_history(
    case: _StoreCase,
    *,
    tenant_id: str,
    run_id: str,
    step_id: str,
) -> tuple[ExecutionOwnership, ...]:
    key = (tenant_id, run_id, step_id)
    if case.name == "memory":
        return tuple(
            ExecutionOwnership.from_exact_mapping(value.to_dict()) for value in case.store._history.get(key, ())
        )
    if case.name == "sqlite":
        rows = case.store._connection.execute(
            """
            SELECT tenant_id, run_id, step_id, revision, attempt_id, ownership_json
            FROM workflow_execution_attempt_history
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
            ORDER BY revision
            """,
            key,
        ).fetchall()
        values: list[ExecutionOwnership] = []
        for row in rows:
            raw = json.loads(str(row["ownership_json"]))
            assert isinstance(raw, dict)
            value = ExecutionOwnership.from_exact_mapping(raw)
            assert (
                row["tenant_id"],
                row["run_id"],
                row["step_id"],
                row["revision"],
                row["attempt_id"],
            ) == (
                value.tenant_id,
                value.run_id,
                value.step_id,
                value.revision,
                value.attempt_id,
            )
            values.append(value)
        return tuple(values)
    return case.store.list_history(
        tenant_id=tenant_id,
        run_id=run_id,
        step_id=step_id,
    )


def _seed_exact_current(case: _StoreCase, current: ExecutionOwnership) -> None:
    key = (current.tenant_id, current.run_id, current.step_id)
    if case.name == "memory":
        case.store._current[key] = current
        case.store._history[key] = [current]
        return
    if case.name == "sqlite":
        payload = canonical_json(current.to_dict())
        case.store._connection.execute(
            """
            INSERT INTO workflow_execution_ownership
            (tenant_id, run_id, step_id, revision, fencing_token, ownership_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (*key, current.revision, current.fencing_token, payload),
        )
        case.store._connection.execute(
            """
            INSERT INTO workflow_execution_attempt_history
            (tenant_id, run_id, step_id, revision, attempt_id, ownership_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (*key, current.revision, current.attempt_id, payload),
        )
        return
    assert case.engine is not None
    with Session(case.engine) as session, session.begin():
        session.add(
            WorkflowExecutionOwnershipDB(
                id=stable_row_id("wfro", *key),
                tenant_id=current.tenant_id,
                workflow_id=current.workflow_id,
                run_id=current.run_id,
                step_id=current.step_id,
                attempt_id=current.attempt_id,
                owner_id=current.owner_id,
                status=current.status,
                revision=current.revision,
                fencing_token=current.fencing_token,
                lease_expires_at=current.lease_expires_at,
                last_heartbeat_at=current.last_heartbeat_at,
                ownership=current.to_dict(),
            )
        )
        session.add(
            WorkflowExecutionAttemptHistoryDB(
                id=stable_row_id("wfrh", *key, current.revision),
                tenant_id=current.tenant_id,
                workflow_id=current.workflow_id,
                run_id=current.run_id,
                step_id=current.step_id,
                attempt_id=current.attempt_id,
                owner_id=current.owner_id,
                status=current.status,
                revision=current.revision,
                fencing_token=current.fencing_token,
                recorded_at=current.last_heartbeat_at,
                ownership=current.to_dict(),
            )
        )


def _tamper_projection(
    case: _StoreCase,
    *,
    projection: str,
    intent: WorkflowTransitionOwnershipReservationIntent,
) -> None:
    key = (intent.tenant_id, intent.run_id, intent.step_id)
    if case.name == "memory":
        receipt = case.store._transition_reservation_receipts[intent.receipt_id]
        if projection == "current":
            current = case.store._current[key]
            case.store._current[key] = replace(
                current,
                lease_expires_at=current.lease_expires_at + 1.0,
            )
        elif projection == "history":
            history = case.store._history[key]
            history[-1] = replace(history[-1], owner_id="tampered-owner")
        else:
            case.store._transition_reservation_receipts[intent.receipt_id] = type(receipt).build(
                intent=receipt.intent,
                creator_claim_generation=receipt.creator_claim_generation + 1,
                prior_ownership=receipt.prior_ownership,
                acquired_ownership=receipt.acquired_ownership,
                retry_consumption=receipt.retry_consumption,
                retry_budget_used_before=receipt.retry_budget_used_before,
                retry_budget_used_after=receipt.retry_budget_used_after,
                reserved_at=receipt.reserved_at,
            )
        return
    if case.name == "sqlite":
        if projection == "current":
            case.store._connection.execute(
                """
                UPDATE workflow_execution_ownership SET revision = revision + 1
                WHERE tenant_id = ? AND run_id = ? AND step_id = ?
                """,
                key,
            )
        elif projection == "history":
            case.store._connection.execute(
                """
                UPDATE workflow_execution_attempt_history SET revision = revision + 1
                WHERE tenant_id = ? AND run_id = ? AND step_id = ?
                  AND attempt_id = ?
                """,
                (*key, intent.attempt_id),
            )
        else:
            case.store._connection.execute(
                """
                UPDATE workflow_transition_ownership_reservations
                SET owner_id = 'tampered-owner' WHERE receipt_id = ?
                """,
                (intent.receipt_id,),
            )
        return
    assert case.engine is not None
    with Session(case.engine) as session, session.begin():
        if projection == "current":
            session.execute(
                sa.update(WorkflowExecutionOwnershipDB)
                .where(
                    WorkflowExecutionOwnershipDB.tenant_id == intent.tenant_id,
                    WorkflowExecutionOwnershipDB.run_id == intent.run_id,
                    WorkflowExecutionOwnershipDB.step_id == intent.step_id,
                )
                .values(revision=WorkflowExecutionOwnershipDB.revision + 1)
            )
        elif projection == "history":
            session.execute(
                sa.update(WorkflowExecutionAttemptHistoryDB)
                .where(
                    WorkflowExecutionAttemptHistoryDB.tenant_id == intent.tenant_id,
                    WorkflowExecutionAttemptHistoryDB.run_id == intent.run_id,
                    WorkflowExecutionAttemptHistoryDB.step_id == intent.step_id,
                    WorkflowExecutionAttemptHistoryDB.attempt_id == intent.attempt_id,
                )
                .values(owner_id="tampered-owner")
            )
        else:
            session.execute(
                sa.update(WorkflowTransitionOwnershipReservationDB)
                .where(WorkflowTransitionOwnershipReservationDB.receipt_id == intent.receipt_id)
                .values(owner_id="tampered-owner")
            )
        return


def test_planning_ids_are_deterministic_and_generation_stable() -> None:
    transition, first = _plan()
    other_transition, second = _plan()

    assert transition == other_transition
    assert first == second
    generation_one = _intent(_claimed(transition, 1), first)
    generation_nine = _intent(_claimed(transition, 9), first)
    assert generation_one == generation_nine
    assert generation_one.retry_id == generation_one.operation_fence_id
    assert generation_one.attempt_id.startswith("wfta-")
    assert generation_one.owner_id.startswith("wfto-")
    assert generation_one.receipt_id.startswith("wftor-")
    assert transition.transition_id == _KNOWN_TRANSITION_ID
    assert generation_one.ownership_intent_digest == _KNOWN_INTENT_DIGEST
    assert generation_one.owner_id == _KNOWN_OWNER_ID
    assert generation_one.operation_fence_id == _KNOWN_OPERATION_FENCE_ID
    assert generation_one.effect_id == _KNOWN_EFFECT_ID
    assert generation_one.attempt_id == _KNOWN_ATTEMPT_ID
    assert generation_one.receipt_id == _KNOWN_RECEIPT_ID
    assert canonical_json(first.to_dict()) == _KNOWN_EFFECT_BYTES


def test_effect_ordinal_retains_the_int64_domain_above_ownership_counter_maximum() -> None:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_ADVANCE,
        identity_key="ordinal-int64-boundary",
    )
    effect = build_workflow_transition_ownership_reservation_effect(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        ordinal=_MAX_OWNERSHIP_COUNTER + 1,
        step_id="step-a",
        lease_seconds=100.0,
        maximum_retries=3,
        planned_at=1_000.0,
    )

    assert effect.ordinal == _MAX_OWNERSHIP_COUNTER + 1
    assert effect.payload["effect_ordinal"] == _MAX_OWNERSHIP_COUNTER + 1


@pytest.mark.parametrize(
    "runtime_id",
    (TRANSITION_RUNTIME_NATIVE, TRANSITION_RUNTIME_LANGGRAPH),
)
def test_native_and_langgraph_planning_and_execution_have_contract_parity(
    runtime_id: str,
) -> None:
    store = InMemoryExecutionOwnershipStore()
    claimed, applying, _executable, applied, intent = _initial_execution(
        store,
        runtime_id=runtime_id,
        identity_key=f"ownership-{runtime_id}",
    )
    receipt = workflow_transition_ownership_reservation_receipt_from_result(applied.result_payload)
    assert claimed.runtime_id == runtime_id
    assert intent.runtime_id == runtime_id
    assert receipt.intent.runtime_id == runtime_id
    assert_durable_workflow_transition_ownership_reservation_proof(
        transition=claimed,
        effect=_persisted_effect(applying, applied, generation=1),
        reads=store,
    )


def test_cross_runtime_absence_and_durable_evidence_cannot_be_replayed() -> None:
    store = InMemoryExecutionOwnershipStore()
    native_claimed, native_applying, native_absence, native_applied, _native_intent = _initial_execution(
        store,
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        identity_key="ownership-native",
        step_id="step-native",
    )
    langgraph_transition, langgraph_planned = _plan(
        runtime_id=TRANSITION_RUNTIME_LANGGRAPH,
        identity_key="ownership-langgraph",
        step_id="step-langgraph",
    )
    langgraph_claimed = _claimed(langgraph_transition, 1)
    replayed_absence = WorkflowTransitionOwnershipReservationExecutor(
        authority=store,
        clock=lambda: 1_002.0,
    ).execute(
        _attempt(langgraph_claimed, _applying(langgraph_planned, 1)),
        executable=native_absence,
        heartbeat=_Heartbeat(),
    )
    assert type(replayed_absence) is EffectQuarantine

    langgraph_executable = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(
        _observation(langgraph_claimed, langgraph_planned),
        heartbeat=_Heartbeat(),
    )
    assert type(langgraph_executable) is EffectExecutable
    langgraph_applying = _applying(langgraph_planned, 1)
    langgraph_applied = WorkflowTransitionOwnershipReservationExecutor(
        authority=store,
        clock=lambda: 1_002.0,
    ).execute(
        _attempt(langgraph_claimed, langgraph_applying),
        executable=langgraph_executable,
        heartbeat=_Heartbeat(),
    )
    assert type(langgraph_applied) is EffectApplied
    native_envelope = workflow_transition_effect_result_envelope(
        mode="execute",
        result_payload=native_applied.result_payload,
        proof_payload=native_applied.proof_payload,
        stage_attempt_count=1,
    )
    replayed_durable = replace(
        langgraph_applying,
        state=EFFECT_STATE_APPLIED,
        applied_generation=1,
        result_payload=native_envelope,
        result_digest=workflow_transition_effect_result_digest(native_envelope),
        revision=langgraph_applying.revision + 2,
        updated_at=1_011.0,
    )
    with pytest.raises(WorkflowTransitionOwnershipReservationError):
        assert_durable_workflow_transition_ownership_reservation_proof(
            transition=langgraph_claimed,
            effect=replayed_durable,
            reads=store,
        )
    assert native_claimed.runtime_id == TRANSITION_RUNTIME_NATIVE
    assert native_applying.transition_id != langgraph_applying.transition_id


@pytest.mark.parametrize(
    ("planned_at", "lease_seconds"),
    (
        (
            float.fromhex("0x1.fffffffffffffp+1023"),
            float.fromhex("0x1.fffffffffffffp+1023"),
        ),
        (1e300, 1.0),
    ),
)
def test_builder_rejects_non_finite_or_non_advancing_lease_end(
    planned_at: float,
    lease_seconds: float,
) -> None:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_ADVANCE,
        identity_key="overflow",
    )
    with pytest.raises(
        WorkflowTransitionOwnershipReservationError,
        match="lease_end_invalid",
    ):
        build_workflow_transition_ownership_reservation_effect(
            transition_id=transition_id,
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            ordinal=1,
            step_id="step-a",
            lease_seconds=lease_seconds,
            maximum_retries=3,
            planned_at=planned_at,
        )


@pytest.mark.parametrize(
    "collision",
    ("extra_column", "wrong_index", "weakened_check", "extra_check"),
)
def test_direct_sqlite_rejects_reservation_schema_collisions(
    tmp_path: Path,
    collision: str,
) -> None:
    path = tmp_path / "schema-collision.sqlite"
    SQLiteExecutionOwnershipStore(path).close()
    connection = sqlite3.connect(path)
    try:
        if collision == "extra_column":
            connection.execute(
                """
                ALTER TABLE workflow_transition_ownership_reservations
                ADD COLUMN unexpected TEXT
                """
            )
        else:
            if collision == "wrong_index":
                connection.execute("DROP INDEX ix_workflow_transition_ownership_res_owner")
                connection.execute(
                    """
                    CREATE INDEX ix_workflow_transition_ownership_res_owner
                    ON workflow_transition_ownership_reservations (run_id)
                    """
                )
            else:
                table_sql_row = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'workflow_transition_ownership_reservations'
                    """
                ).fetchone()
                assert table_sql_row is not None
                table_sql = str(table_sql_row[0])
                index_sql = tuple(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT sql FROM sqlite_master
                        WHERE type = 'index'
                          AND tbl_name = 'workflow_transition_ownership_reservations'
                          AND sql IS NOT NULL
                        ORDER BY name
                        """
                    ).fetchall()
                )
                if collision == "weakened_check":
                    replacement_sql = table_sql.replace(
                        "lease_expires_at > reserved_at",
                        "(lease_expires_at > reserved_at) OR 1=1",
                        1,
                    )
                    assert replacement_sql != table_sql
                else:
                    closing = table_sql.rfind(")")
                    assert closing > 0
                    replacement_sql = (
                        table_sql[:closing] + ", CONSTRAINT ck_workflow_transition_ownership_res_extra "
                        "CHECK (maximum_retries < 2147483647)" + table_sql[closing:]
                    )
                connection.execute("DROP TABLE workflow_transition_ownership_reservations")
                connection.execute(replacement_sql)
                for statement in index_sql:
                    connection.execute(statement)
        connection.commit()
        before_schema = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql FROM sqlite_master
                WHERE tbl_name = 'workflow_transition_ownership_reservations'
                ORDER BY type, name
                """
            ).fetchall()
        )
    finally:
        connection.close()
    with pytest.raises(
        WorkflowTransitionOwnershipReservationConflict,
        match="direct_schema_conflict",
    ):
        SQLiteExecutionOwnershipStore(path)
    connection = sqlite3.connect(path)
    try:
        after_schema = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql FROM sqlite_master
                WHERE tbl_name = 'workflow_transition_ownership_reservations'
                ORDER BY type, name
                """
            ).fetchall()
        )
    finally:
        connection.close()
    assert after_schema == before_schema


def test_executor_quarantines_late_clock_lease_overflow_without_commit() -> None:
    store = InMemoryExecutionOwnershipStore()
    transition, planned = _plan()
    claimed = _claimed(transition, 1)
    executable = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(_observation(claimed, planned), heartbeat=_Heartbeat())
    assert type(executable) is EffectExecutable

    result = WorkflowTransitionOwnershipReservationExecutor(
        authority=store,
        clock=lambda: float.fromhex("0x1.fffffffffffffp+1023"),
    ).execute(
        _attempt(claimed, _applying(planned, 1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )

    assert type(result) is EffectQuarantine
    assert result.reason_code == "ownership_reservation_clock_invalid"
    assert store.read_transition_reservation_history(_intent(claimed, planned)).receipt is None


def test_initial_commit_receipt_proofs_and_exact_adoption_are_lsp_equivalent(
    reservation_store: _StoreCase,
) -> None:
    claimed, applying, executable, applied, intent = _initial_execution(reservation_store.store)
    receipt = workflow_transition_ownership_reservation_receipt_from_result(applied.result_payload)
    assert receipt.intent == intent
    assert receipt.creator_claim_generation == 1
    assert receipt.prior_ownership is None
    assert receipt.retry_consumption is None
    assert receipt.retry_budget_used_before == receipt.retry_budget_used_after == 0

    persisted = _persisted_effect(applying, applied, generation=1)
    proof = assert_durable_workflow_transition_ownership_reservation_proof(
        transition=claimed,
        effect=persisted,
        reads=reservation_store.store,
    )
    assert proof.resource_id == receipt.receipt_id
    assert proof.resource_digest == receipt.receipt_digest
    assert (
        assert_current_workflow_transition_ownership_reservation_validity(
            transition=claimed,
            effect=persisted,
            reads=reservation_store.store,
            clock=lambda: 1_003.0,
        )
        is None
    )

    generation_two = _claimed(claimed, 2)
    older_attempt = replace(
        applying,
        applied_generation=1,
        updated_at=1_004.0,
    )
    adopted = WorkflowTransitionOwnershipReservationObserver(
        reads=reservation_store.store,
        clock=lambda: 1_004.0,
    ).observe_or_adopt(
        _observation(generation_two, older_attempt),
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectAlreadyApplied
    assert adopted.result_payload == applied.result_payload

    retried_attempt = _applying(applying, 2)
    before = reservation_store.store.get_retry_budget(
        tenant_id=intent.tenant_id,
        run_id=intent.run_id,
        maximum=intent.maximum_retries,
    )
    rerun = WorkflowTransitionOwnershipReservationExecutor(
        authority=reservation_store.store,
        clock=lambda: 1_005.0,
    ).execute(
        _attempt(generation_two, retried_attempt),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    after = reservation_store.store.get_retry_budget(
        tenant_id=intent.tenant_id,
        run_id=intent.run_id,
        maximum=intent.maximum_retries,
    )
    assert type(rerun) is EffectApplied
    assert rerun.result_payload == applied.result_payload
    assert after == before


def test_receipt_hydration_rejects_initial_budget_snapshot_above_maximum() -> None:
    store = InMemoryExecutionOwnershipStore()
    _claimed_value, _applying, _executable, applied, _intent_value = _initial_execution(store)
    receipt = workflow_transition_ownership_reservation_receipt_from_result(applied.result_payload)
    tampered = receipt.to_dict()
    over_maximum = receipt.intent.maximum_retries + 1
    tampered["retry_budget_used_before"] = over_maximum
    tampered["retry_budget_used_after"] = over_maximum

    with pytest.raises(
        WorkflowTransitionOwnershipReservationConflict,
        match="receipt_retry_budget_invalid",
    ):
        WorkflowTransitionOwnershipReservationReceipt.from_mapping(tampered)


@pytest.mark.parametrize("field", ("revision", "fencing_token"))
def test_receipt_hydration_rejects_acquired_counter_above_ownership_maximum(
    field: str,
) -> None:
    store = InMemoryExecutionOwnershipStore()
    _claimed_value, _applying, _executable, applied, _intent_value = _initial_execution(store)
    receipt = workflow_transition_ownership_reservation_receipt_from_result(applied.result_payload)
    tampered = receipt.to_dict()
    acquired = tampered["acquired_ownership"]
    assert isinstance(acquired, dict)
    acquired[field] = _MAX_OWNERSHIP_COUNTER + 1

    with pytest.raises(
        WorkflowTransitionOwnershipReservationConflict,
        match=rf"{field}_invalid",
    ):
        WorkflowTransitionOwnershipReservationReceipt.from_mapping(tampered)


@pytest.mark.parametrize("heartbeat_at", (1_003.0, 1_001.0))
def test_current_validity_accepts_legacy_shorter_or_regressed_live_heartbeat(
    reservation_store: _StoreCase,
    heartbeat_at: float,
) -> None:
    claimed, applying, _executable, applied, intent = _initial_execution(reservation_store.store)
    acquired = reservation_store.store.get(
        tenant_id=intent.tenant_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
    )
    assert acquired is not None
    renewed = reservation_store.store.heartbeat(
        tenant_id=intent.tenant_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        attempt_id=acquired.attempt_id,
        owner_id=acquired.owner_id,
        fencing_token=acquired.fencing_token,
        expected_revision=acquired.revision,
        lease_seconds=10.0,
        now=heartbeat_at,
    )
    assert renewed.lease_expires_at < acquired.lease_expires_at
    assert (renewed.last_heartbeat_at < acquired.last_heartbeat_at) is (heartbeat_at < acquired.last_heartbeat_at)
    persisted = _persisted_effect(applying, applied, generation=1)
    assert (
        assert_current_workflow_transition_ownership_reservation_validity(
            transition=claimed,
            effect=persisted,
            reads=reservation_store.store,
            clock=lambda: 1_004.0,
        )
        is None
    )


@pytest.mark.parametrize("terminal_status", ("failed", "completed"))
def test_strict_reservation_classifies_broad_terminal_with_regressed_now(
    reservation_store: _StoreCase,
    terminal_status: str,
) -> None:
    store = reservation_store.store
    claimed_legacy = store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        owner_id="legacy-owner",
        lease_seconds=100.0,
        maximum_retries=3,
        now=1_000.0,
    )
    assert claimed_legacy.acquired is True
    legacy = claimed_legacy.ownership
    mutation = {
        "tenant_id": legacy.tenant_id,
        "run_id": legacy.run_id,
        "step_id": legacy.step_id,
        "attempt_id": legacy.attempt_id,
        "owner_id": legacy.owner_id,
        "fencing_token": legacy.fencing_token,
        "expected_revision": legacy.revision,
        "now": 900.0,
    }
    if terminal_status == "failed":
        terminal = store.fail_attempt(
            **mutation,
            failure_code="legacy-failure",
        )
    else:
        terminal = store.acknowledge_result(
            **mutation,
            result_ack_key="legacy-result",
        )
    assert terminal.status == terminal_status
    assert terminal.lease_expires_at < terminal.last_heartbeat_at
    before_history = _exact_history(
        reservation_store,
        tenant_id=terminal.tenant_id,
        run_id=terminal.run_id,
        step_id=terminal.step_id,
    )
    before_budget = store.get_retry_budget(
        tenant_id=terminal.tenant_id,
        run_id=terminal.run_id,
        maximum=3,
    )
    before_receipts = _stored_receipt_records(reservation_store)

    transition, planned = _plan(identity_key=f"ownership-regressed-{terminal_status}")
    claimed = _claimed(transition, 1)
    observed = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(
        _observation(claimed, planned),
        heartbeat=_Heartbeat(),
    )
    if terminal_status == "failed":
        assert type(observed) is EffectExecutable
        applied = WorkflowTransitionOwnershipReservationExecutor(
            authority=store,
            clock=lambda: 1_002.0,
        ).execute(
            _attempt(claimed, _applying(planned, 1)),
            executable=observed,
            heartbeat=_Heartbeat(),
        )
        assert type(applied) is EffectApplied
        receipt = workflow_transition_ownership_reservation_receipt_from_result(applied.result_payload)
        assert receipt.prior_ownership == terminal
        assert receipt.acquired_revision == terminal.revision + 1
        assert receipt.acquired_fencing_token == terminal.fencing_token + 1
        return

    assert type(observed) is EffectQuarantine
    assert (
        store.get(
            tenant_id=terminal.tenant_id,
            run_id=terminal.run_id,
            step_id=terminal.step_id,
        )
        == terminal
    )
    assert (
        _exact_history(
            reservation_store,
            tenant_id=terminal.tenant_id,
            run_id=terminal.run_id,
            step_id=terminal.step_id,
        )
        == before_history
    )
    assert (
        store.get_retry_budget(
            tenant_id=terminal.tenant_id,
            run_id=terminal.run_id,
            maximum=3,
        )
        == before_budget
    )
    assert _stored_receipt_records(reservation_store) == before_receipts == ()


@pytest.mark.parametrize("projection", ("current", "history", "receipt"))
def test_normalized_storage_projection_tamper_fails_closed(
    reservation_store: _StoreCase,
    projection: str,
) -> None:
    claimed, applying, _executable, applied, intent = _initial_execution(reservation_store.store)
    persisted = _persisted_effect(applying, applied, generation=1)
    _tamper_projection(
        reservation_store,
        projection=projection,
        intent=intent,
    )

    with pytest.raises(WorkflowTransitionOwnershipReservationError):
        if projection == "current":
            assert_current_workflow_transition_ownership_reservation_validity(
                transition=claimed,
                effect=persisted,
                reads=reservation_store.store,
                clock=lambda: 1_004.0,
            )
        else:
            assert_durable_workflow_transition_ownership_reservation_proof(
                transition=claimed,
                effect=persisted,
                reads=reservation_store.store,
            )


def test_retry_consumption_projection_tamper_fails_closed(
    reservation_store: _StoreCase,
) -> None:
    store = reservation_store.store
    store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        owner_id="legacy-owner",
        lease_seconds=10.0,
        maximum_retries=3,
        now=900.0,
    )
    transition, planned = _plan()
    claimed = _claimed(transition, 1)
    executable = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(
        _observation(claimed, planned),
        heartbeat=_Heartbeat(),
    )
    assert type(executable) is EffectExecutable
    applying = _applying(planned, 1)
    applied = WorkflowTransitionOwnershipReservationExecutor(
        authority=store,
        clock=lambda: 1_002.0,
    ).execute(
        _attempt(claimed, applying),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied
    intent = _intent(claimed, applying)
    if reservation_store.name == "memory":
        store._retry_ids[(intent.tenant_id, intent.run_id, intent.retry_id)] = "runtime"
    elif reservation_store.name == "sqlite":
        store._connection.execute(
            """
            UPDATE workflow_retry_consumptions SET category = 'runtime'
            WHERE tenant_id = ? AND run_id = ? AND retry_id = ?
            """,
            (intent.tenant_id, intent.run_id, intent.retry_id),
        )
    else:
        assert reservation_store.engine is not None
        with Session(reservation_store.engine) as session, session.begin():
            session.execute(
                sa.update(WorkflowRetryConsumptionDB)
                .where(
                    WorkflowRetryConsumptionDB.tenant_id == intent.tenant_id,
                    WorkflowRetryConsumptionDB.run_id == intent.run_id,
                    WorkflowRetryConsumptionDB.retry_id == intent.retry_id,
                )
                .values(category="runtime")
            )
    with pytest.raises(WorkflowTransitionOwnershipReservationError):
        assert_durable_workflow_transition_ownership_reservation_proof(
            transition=claimed,
            effect=_persisted_effect(applying, applied, generation=1),
            reads=store,
        )


def test_receipt_adoption_is_historical_after_expiry_takeover_or_current_prune(
    reservation_store: _StoreCase,
) -> None:
    claimed, applying, _executable, applied, intent = _initial_execution(reservation_store.store)
    persisted = _persisted_effect(applying, applied, generation=1)

    expired = WorkflowTransitionOwnershipReservationObserver(
        reads=reservation_store.store,
        clock=lambda: 1_500.0,
    ).observe_or_adopt(
        _observation(_claimed(claimed, 2), applying),
        heartbeat=_Heartbeat(),
    )
    assert type(expired) is EffectAlreadyApplied
    with pytest.raises(WorkflowTransitionOwnershipReservationError, match="current"):
        assert_current_workflow_transition_ownership_reservation_validity(
            transition=claimed,
            effect=persisted,
            reads=reservation_store.store,
            clock=lambda: 1_500.0,
        )

    takeover = reservation_store.store.claim(
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        owner_id="legacy-owner",
        lease_seconds=100.0,
        maximum_retries=intent.maximum_retries,
        now=1_500.0,
    )
    assert takeover.acquired is True
    after_takeover = WorkflowTransitionOwnershipReservationObserver(
        reads=reservation_store.store,
        clock=lambda: 1_501.0,
    ).observe_or_adopt(
        _observation(_claimed(claimed, 3), applying),
        heartbeat=_Heartbeat(),
    )
    assert type(after_takeover) is EffectAlreadyApplied
    assert_durable_workflow_transition_ownership_reservation_proof(
        transition=claimed,
        effect=persisted,
        reads=reservation_store.store,
    )
    with pytest.raises(WorkflowTransitionOwnershipReservationError, match="current"):
        assert_current_workflow_transition_ownership_reservation_validity(
            transition=claimed,
            effect=persisted,
            reads=reservation_store.store,
            clock=lambda: 1_501.0,
        )

    _delete_current(reservation_store, intent)
    assert (
        reservation_store.store.get(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            step_id=intent.step_id,
        )
        is None
    )
    after_prune = WorkflowTransitionOwnershipReservationObserver(
        reads=reservation_store.store,
        clock=lambda: 1_502.0,
    ).observe_or_adopt(
        _observation(_claimed(claimed, 4), applying),
        heartbeat=_Heartbeat(),
    )
    assert type(after_prune) is EffectAlreadyApplied
    assert_durable_workflow_transition_ownership_reservation_proof(
        transition=claimed,
        effect=persisted,
        reads=reservation_store.store,
    )
    with pytest.raises(WorkflowTransitionOwnershipReservationError, match="current"):
        assert_current_workflow_transition_ownership_reservation_validity(
            transition=claimed,
            effect=persisted,
            reads=reservation_store.store,
            clock=lambda: 1_502.0,
        )


def test_live_legacy_owner_retries_then_expired_takeover_consumes_retry_once(
    reservation_store: _StoreCase,
) -> None:
    store = reservation_store.store
    legacy = store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        owner_id="legacy-owner",
        lease_seconds=10.0,
        maximum_retries=3,
        now=990.0,
    )
    assert legacy.acquired is True
    transition, planned = _plan()
    claimed = _claimed(transition, 1)
    live = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 995.0,
    ).observe_or_adopt(_observation(claimed, planned), heartbeat=_Heartbeat())
    assert type(live) is EffectRetry

    observer = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(_observation(claimed, planned), heartbeat=_Heartbeat())
    assert type(observer) is EffectExecutable
    applying = _applying(planned, 1)
    applied = WorkflowTransitionOwnershipReservationExecutor(
        authority=store,
        clock=lambda: 1_001.0,
    ).execute(
        _attempt(claimed, applying),
        executable=observer,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied
    receipt = workflow_transition_ownership_reservation_receipt_from_result(applied.result_payload)
    assert receipt.prior_ownership == legacy.ownership
    assert receipt.retry_consumption is not None
    assert receipt.retry_consumption.retry_id == receipt.operation_fence_id
    assert receipt.retry_budget_used_before == 0
    assert receipt.retry_budget_used_after == 1

    generation_two = _claimed(claimed, 2)
    rerun = WorkflowTransitionOwnershipReservationExecutor(
        authority=store,
        clock=lambda: 2_000.0,
    ).execute(
        _attempt(generation_two, _applying(applying, 2)),
        executable=observer,
        heartbeat=_Heartbeat(),
    )
    assert type(rerun) is EffectApplied
    budget = store.get_retry_budget(
        tenant_id="tenant-a",
        run_id="run-a",
        maximum=3,
    )
    assert budget.used == 1


@pytest.mark.parametrize(
    "scenario",
    ("exhausted", "maximum_mismatch", "consumption_without_receipt"),
)
def test_retry_budget_conflicts_quarantine_without_reservation_mutation(
    reservation_store: _StoreCase,
    scenario: str,
) -> None:
    store = reservation_store.store
    maximum = 0 if scenario == "exhausted" else 3
    transition, planned = _plan(maximum_retries=maximum)
    claimed = _claimed(transition, 1)
    intent = _intent(claimed, planned)
    if scenario == "exhausted":
        legacy = store.claim(
            tenant_id=intent.tenant_id,
            workflow_id=intent.workflow_id,
            run_id=intent.run_id,
            step_id=intent.step_id,
            owner_id="legacy-owner",
            lease_seconds=10.0,
            maximum_retries=0,
            now=900.0,
        )
        result = WorkflowTransitionOwnershipReservationObserver(
            reads=store,
            clock=lambda: 1_001.0,
        ).observe_or_adopt(
            _observation(claimed, planned),
            heartbeat=_Heartbeat(),
        )
        assert type(result) is EffectQuarantine
        assert result.reason_code == "ownership_reservation_retry_exhausted"
        assert (
            store.get(
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                step_id=intent.step_id,
            )
            == legacy.ownership
        )
    elif scenario == "maximum_mismatch":
        store.consume_retry(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            retry_id="unrelated-retry",
            category="hub_task",
            maximum=2,
        )
        result = WorkflowTransitionOwnershipReservationObserver(
            reads=store,
            clock=lambda: 1_001.0,
        ).observe_or_adopt(
            _observation(claimed, planned),
            heartbeat=_Heartbeat(),
        )
        assert type(result) is EffectQuarantine
        assert (
            store.get(
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                step_id=intent.step_id,
            )
            is None
        )
    else:
        store.consume_retry(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            retry_id=intent.retry_id,
            category="hub_task",
            maximum=intent.maximum_retries,
        )
        result = WorkflowTransitionOwnershipReservationObserver(
            reads=store,
            clock=lambda: 1_001.0,
        ).observe_or_adopt(
            _observation(claimed, planned),
            heartbeat=_Heartbeat(),
        )
        assert type(result) is EffectQuarantine
        assert (
            store.get(
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                step_id=intent.step_id,
            )
            is None
        )
    if scenario == "consumption_without_receipt":
        with pytest.raises(WorkflowTransitionOwnershipReservationConflict):
            store.read_transition_reservation_history(intent)
    else:
        assert store.read_transition_reservation_history(intent).receipt is None


@pytest.mark.parametrize("corruption", ("budget_wrong_id", "consumption_wrong_id"))
def test_sql_natural_alias_with_wrong_surrogate_id_is_proven_conflict_without_mutation(
    tmp_path: Path,
    corruption: str,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'retry-alias-{corruption}.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    store = SQLAlchemyExecutionOwnershipStore(engine)
    try:
        transition, planned = _plan(identity_key=f"ownership-{corruption}")
        claimed = _claimed(transition, 1)
        intent = _intent(claimed, planned)
        clean = store.observe_transition_reservation(intent, claim_generation=1)
        with Session(engine) as session, session.begin():
            if corruption == "budget_wrong_id":
                session.add(
                    WorkflowRetryBudgetDB(
                        id="wrong-budget-surrogate-id",
                        tenant_id=intent.tenant_id,
                        run_id=intent.run_id,
                        used=1,
                        maximum=intent.maximum_retries,
                        revision=1,
                        updated_at=900.0,
                    )
                )
            else:
                session.add(
                    WorkflowRetryConsumptionDB(
                        id="wrong-consumption-surrogate-id",
                        tenant_id=intent.tenant_id,
                        run_id=intent.run_id,
                        retry_id=intent.retry_id,
                        category="hub_task",
                        consumed_at=900.0,
                    )
                )
        before = _sql_reservation_state(engine)

        with pytest.raises(WorkflowTransitionOwnershipReservationConflict):
            store.observe_transition_reservation(intent, claim_generation=1)
        with pytest.raises(WorkflowTransitionOwnershipReservationConflict):
            store.reserve_transition_effect(
                intent,
                creator_claim_generation=1,
                expected_observation_digest=clean.observation_digest,
                reserved_at=1_002.0,
            )

        assert _sql_reservation_state(engine) == before
        assert (
            store.get(
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                step_id=intent.step_id,
            )
            is None
        )
    finally:
        engine.dispose()


def test_counter_exhaustion_quarantines_without_mutation(
    reservation_store: _StoreCase,
) -> None:
    store = reservation_store.store
    current = ExecutionOwnership(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        attempt_id="legacy-attempt",
        owner_id="legacy-owner",
        fencing_token=_MAX_OWNERSHIP_COUNTER,
        revision=_MAX_OWNERSHIP_COUNTER,
        status="active",
        lease_expires_at=910.0,
        last_heartbeat_at=900.0,
    )
    _seed_exact_current(reservation_store, current)
    before_history = _exact_history(
        reservation_store,
        tenant_id=current.tenant_id,
        run_id=current.run_id,
        step_id=current.step_id,
    )
    before_budget = store.get_retry_budget(
        tenant_id=current.tenant_id,
        run_id=current.run_id,
        maximum=3,
    )
    before_receipts = _stored_receipt_records(reservation_store)

    transition, effect = _plan()
    claimed = _claimed(transition, 1)
    result = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )

    assert type(result) is EffectQuarantine
    assert result.reason_code == "ownership_reservation_counter_exhausted"
    assert (
        store.get(
            tenant_id=current.tenant_id,
            run_id=current.run_id,
            step_id=current.step_id,
        )
        == current
    )
    assert (
        _exact_history(
            reservation_store,
            tenant_id=current.tenant_id,
            run_id=current.run_id,
            step_id=current.step_id,
        )
        == before_history
    )
    assert (
        store.get_retry_budget(
            tenant_id=current.tenant_id,
            run_id=current.run_id,
            maximum=3,
        )
        == before_budget
    )
    assert _stored_receipt_records(reservation_store) == before_receipts == ()


def test_near_counter_max_takeover_succeeds_then_distinct_intent_quarantines(
    reservation_store: _StoreCase,
) -> None:
    store = reservation_store.store
    current = ExecutionOwnership(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        attempt_id="legacy-near-max-attempt",
        owner_id="legacy-near-max-owner",
        fencing_token=_MAX_OWNERSHIP_COUNTER - 1,
        revision=_MAX_OWNERSHIP_COUNTER - 1,
        status="active",
        lease_expires_at=910.0,
        last_heartbeat_at=900.0,
    )
    _seed_exact_current(reservation_store, current)
    transition_a, planned_a = _plan(identity_key="ownership-counter-max-a")
    claimed_a = _claimed(transition_a, 1)
    executable_a = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(
        _observation(claimed_a, planned_a),
        heartbeat=_Heartbeat(),
    )
    assert type(executable_a) is EffectExecutable
    applied_a = WorkflowTransitionOwnershipReservationExecutor(
        authority=store,
        clock=lambda: 1_002.0,
    ).execute(
        _attempt(claimed_a, _applying(planned_a, 1)),
        executable=executable_a,
        heartbeat=_Heartbeat(),
    )
    assert type(applied_a) is EffectApplied
    receipt_a = workflow_transition_ownership_reservation_receipt_from_result(applied_a.result_payload)
    assert receipt_a.acquired_revision == _MAX_OWNERSHIP_COUNTER
    assert receipt_a.acquired_fencing_token == _MAX_OWNERSHIP_COUNTER

    after_a_current = store.get(
        tenant_id=current.tenant_id,
        run_id=current.run_id,
        step_id=current.step_id,
    )
    after_a_history = _exact_history(
        reservation_store,
        tenant_id=current.tenant_id,
        run_id=current.run_id,
        step_id=current.step_id,
    )
    after_a_budget = store.get_retry_budget(
        tenant_id=current.tenant_id,
        run_id=current.run_id,
        maximum=3,
    )
    after_a_receipts = _stored_receipt_records(reservation_store)
    assert after_a_current == receipt_a.acquired_ownership
    assert after_a_history[-1] == receipt_a.acquired_ownership
    assert after_a_budget.used == 1

    transition_b, planned_b = _plan(identity_key="ownership-counter-max-b")
    result_b = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 2_000.0,
    ).observe_or_adopt(
        _observation(_claimed(transition_b, 1), planned_b),
        heartbeat=_Heartbeat(),
    )
    assert type(result_b) is EffectQuarantine
    assert (
        store.get(
            tenant_id=current.tenant_id,
            run_id=current.run_id,
            step_id=current.step_id,
        )
        == after_a_current
    )
    assert (
        _exact_history(
            reservation_store,
            tenant_id=current.tenant_id,
            run_id=current.run_id,
            step_id=current.step_id,
        )
        == after_a_history
    )
    assert (
        store.get_retry_budget(
            tenant_id=current.tenant_id,
            run_id=current.run_id,
            maximum=3,
        )
        == after_a_budget
    )
    assert _stored_receipt_records(reservation_store) == after_a_receipts


def test_foreign_effect_cannot_take_over_receipt_bearing_current(
    reservation_store: _StoreCase,
) -> None:
    _initial_execution(reservation_store.store)
    transition, effect = _plan(identity_key="ownership-transition-b")
    result = WorkflowTransitionOwnershipReservationObserver(
        reads=reservation_store.store,
        clock=lambda: 1_003.0,
    ).observe_or_adopt(
        _observation(_claimed(transition, 1), effect),
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectQuarantine


@pytest.mark.parametrize("takeover_receipt", (False, True))
def test_retained_receipt_blocks_prospective_scope_counter_collision(
    reservation_store: _StoreCase,
    takeover_receipt: bool,
) -> None:
    store = reservation_store.store
    if takeover_receipt:
        legacy = store.claim(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            step_id="step-a",
            owner_id="legacy-owner",
            lease_seconds=10.0,
            maximum_retries=3,
            now=900.0,
        )
        assert legacy.acquired is True
    transition_a, effect_a = _plan(identity_key="ownership-transition-a")
    transition_b, effect_b = _plan(identity_key="ownership-transition-b")
    claimed_a = _claimed(transition_a, 1)
    claimed_b = _claimed(transition_b, 1)
    intent_a = _intent(claimed_a, effect_a)
    intent_b = _intent(claimed_b, effect_b)
    assert intent_a.receipt_id != intent_b.receipt_id
    assert (
        intent_a.tenant_id,
        intent_a.run_id,
        intent_a.step_id,
    ) == (
        intent_b.tenant_id,
        intent_b.run_id,
        intent_b.step_id,
    )
    initial_b = store.observe_transition_reservation(intent_b, claim_generation=1)
    initial_a = store.observe_transition_reservation(intent_a, claim_generation=1)
    receipt_a = store.reserve_transition_effect(
        intent_a,
        creator_claim_generation=1,
        expected_observation_digest=initial_a.observation_digest,
        reserved_at=1_002.0,
    )
    historical_a = store.read_transition_reservation_history(intent_a)
    assert historical_a.receipt == receipt_a
    assert receipt_a.acquired_revision == (2 if takeover_receipt else 1)
    assert receipt_a.acquired_fencing_token == (2 if takeover_receipt else 1)
    exact_receipt_a = (
        receipt_a.receipt_id,
        receipt_a.receipt_digest,
        canonical_json(receipt_a.to_dict()),
    )
    budget_after_a = store.get_retry_budget(
        tenant_id=intent_a.tenant_id,
        run_id=intent_a.run_id,
        maximum=intent_a.maximum_retries,
    )
    assert budget_after_a.used == (1 if takeover_receipt else 0)

    _delete_current_and_history(reservation_store, intent_a)
    assert (
        store.get(
            tenant_id=intent_a.tenant_id,
            run_id=intent_a.run_id,
            step_id=intent_a.step_id,
        )
        is None
    )
    assert (
        _exact_history(
            reservation_store,
            tenant_id=intent_a.tenant_id,
            run_id=intent_a.run_id,
            step_id=intent_a.step_id,
        )
        == ()
    )

    with pytest.raises(
        WorkflowTransitionOwnershipReservationConflict,
        match="prospective_receipt_conflict",
    ):
        store.observe_transition_reservation(intent_b, claim_generation=1)
    with pytest.raises(
        WorkflowTransitionOwnershipReservationConflict,
        match="prospective_receipt_conflict",
    ):
        store.reserve_transition_effect(
            intent_b,
            creator_claim_generation=1,
            expected_observation_digest=initial_b.observation_digest,
            reserved_at=1_003.0,
        )

    assert _stored_receipt_records(reservation_store) == (exact_receipt_a,)
    assert (
        store.get_retry_budget(
            tenant_id=intent_a.tenant_id,
            run_id=intent_a.run_id,
            maximum=intent_a.maximum_retries,
        )
        == budget_after_a
    )
    assert (
        store.get(
            tenant_id=intent_a.tenant_id,
            run_id=intent_a.run_id,
            step_id=intent_a.step_id,
        )
        is None
    )
    assert (
        _exact_history(
            reservation_store,
            tenant_id=intent_a.tenant_id,
            run_id=intent_a.run_id,
            step_id=intent_a.step_id,
        )
        == ()
    )
    with pytest.raises(
        WorkflowTransitionOwnershipReservationConflict,
        match="evidence_history_conflict",
    ):
        store.read_transition_reservation_history(intent_a)


def test_retained_high_water_receipt_blocks_current_present_aba_collision(
    reservation_store: _StoreCase,
) -> None:
    store = reservation_store.store
    maximum_retries = 5
    for revision, claimed_at in enumerate((700.0, 800.0, 900.0), start=1):
        legacy = store.claim(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            step_id="step-a",
            owner_id=f"legacy-owner-{revision}",
            lease_seconds=10.0,
            maximum_retries=maximum_retries,
            now=claimed_at,
        )
        assert legacy.acquired is True
        assert legacy.ownership.revision == revision
        assert legacy.ownership.fencing_token == revision

    transition_a, effect_a = _plan(
        identity_key="ownership-high-water-a",
        maximum_retries=maximum_retries,
    )
    transition_b, effect_b = _plan(
        identity_key="ownership-high-water-b",
        maximum_retries=maximum_retries,
    )
    claimed_a = _claimed(transition_a, 1)
    claimed_b = _claimed(transition_b, 1)
    intent_a = _intent(claimed_a, effect_a)
    intent_b = _intent(claimed_b, effect_b)
    initial_b = store.observe_transition_reservation(intent_b, claim_generation=1)
    initial_a = store.observe_transition_reservation(intent_a, claim_generation=1)
    receipt_a = store.reserve_transition_effect(
        intent_a,
        creator_claim_generation=1,
        expected_observation_digest=initial_a.observation_digest,
        reserved_at=1_002.0,
    )
    assert receipt_a.acquired_revision == 4
    assert receipt_a.acquired_fencing_token == 4
    exact_receipt_a = (
        receipt_a.receipt_id,
        receipt_a.receipt_digest,
        canonical_json(receipt_a.to_dict()),
    )

    _delete_current_and_history(reservation_store, intent_a)
    recreated = store.claim(
        tenant_id=intent_a.tenant_id,
        workflow_id=intent_a.workflow_id,
        run_id=intent_a.run_id,
        step_id=intent_a.step_id,
        owner_id="receiptless-recreated-owner",
        lease_seconds=10.0,
        maximum_retries=maximum_retries,
        now=2_000.0,
    )
    assert recreated.acquired is True
    assert recreated.ownership.revision == 1
    assert recreated.ownership.fencing_token == 1
    before_current = recreated.ownership
    before_history = _exact_history(
        reservation_store,
        tenant_id=intent_a.tenant_id,
        run_id=intent_a.run_id,
        step_id=intent_a.step_id,
    )
    before_budget = store.get_retry_budget(
        tenant_id=intent_a.tenant_id,
        run_id=intent_a.run_id,
        maximum=maximum_retries,
    )
    before_receipts = _stored_receipt_records(reservation_store)
    assert before_history == (before_current,)
    assert before_budget.used == 3
    assert before_receipts == (exact_receipt_a,)

    with pytest.raises(WorkflowTransitionOwnershipReservationConflict):
        store.observe_transition_reservation(intent_b, claim_generation=1)
    with pytest.raises(WorkflowTransitionOwnershipReservationConflict):
        store.reserve_transition_effect(
            intent_b,
            creator_claim_generation=1,
            expected_observation_digest=initial_b.observation_digest,
            reserved_at=2_100.0,
        )

    assert (
        store.get(
            tenant_id=intent_a.tenant_id,
            run_id=intent_a.run_id,
            step_id=intent_a.step_id,
        )
        == before_current
    )
    assert (
        _exact_history(
            reservation_store,
            tenant_id=intent_a.tenant_id,
            run_id=intent_a.run_id,
            step_id=intent_a.step_id,
        )
        == before_history
    )
    assert (
        store.get_retry_budget(
            tenant_id=intent_a.tenant_id,
            run_id=intent_a.run_id,
            maximum=maximum_retries,
        )
        == before_budget
    )
    assert _stored_receipt_records(reservation_store) == before_receipts


class _FailingReads:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def read_transition_reservation_history(self, intent: Any) -> Any:
        del intent
        raise self.error

    def observe_transition_reservation(
        self,
        intent: Any,
        *,
        claim_generation: int,
    ) -> Any:
        del intent, claim_generation
        raise self.error


class _ConflictThenHistoryUnavailable:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.history_reads = 0

    def read_transition_reservation_history(self, intent: Any) -> Any:
        self.history_reads += 1
        if self.history_reads > 1:
            raise WorkflowTransitionOwnershipReservationUnavailable("unavailable")
        return self.delegate.read_transition_reservation_history(intent)

    def observe_transition_reservation(self, intent: Any, *, claim_generation: int) -> Any:
        del intent, claim_generation
        raise WorkflowTransitionOwnershipReservationConflict("raced")


def test_current_conflict_with_unavailable_historical_recheck_retries() -> None:
    store = InMemoryExecutionOwnershipStore()
    transition, effect = _plan()
    result = WorkflowTransitionOwnershipReservationObserver(
        reads=_ConflictThenHistoryUnavailable(store),
        clock=lambda: 1_001.0,
    ).observe_or_adopt(
        _observation(_claimed(transition, 1), effect),
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectRetry


@pytest.mark.parametrize(
    ("error", "expected_type"),
    (
        (WorkflowTransitionOwnershipReservationUnavailable("unavailable"), EffectRetry),
        (RuntimeError("read failed"), EffectRetry),
        (WorkflowTransitionOwnershipReservationConflict("conflict"), EffectQuarantine),
    ),
)
def test_observer_distinguishes_read_failure_from_proven_conflict(
    error: Exception,
    expected_type: type[EffectRetry] | type[EffectQuarantine],
) -> None:
    transition, effect = _plan()
    result = WorkflowTransitionOwnershipReservationObserver(
        reads=_FailingReads(error),
        clock=lambda: 1_001.0,
    ).observe_or_adopt(
        _observation(_claimed(transition, 1), effect),
        heartbeat=_Heartbeat(),
    )
    assert type(result) is expected_type


class _LostResponseAuthority:
    def __init__(self, delegate: Any, *, fail_history_after_commit: bool = False) -> None:
        self.delegate = delegate
        self.fail_history_after_commit = fail_history_after_commit
        self.committed = False

    def observe_transition_reservation(self, intent: Any, *, claim_generation: int) -> Any:
        return self.delegate.observe_transition_reservation(
            intent,
            claim_generation=claim_generation,
        )

    def read_transition_reservation_history(self, intent: Any) -> Any:
        if self.committed and self.fail_history_after_commit:
            raise WorkflowTransitionOwnershipReservationUnavailable("read failed")
        return self.delegate.read_transition_reservation_history(intent)

    def reserve_transition_effect(self, intent: Any, **values: Any) -> Any:
        self.delegate.reserve_transition_effect(intent, **values)
        self.committed = True
        raise RuntimeError("lost response")


class _OCCBeforeCommitAuthority:
    def __init__(self, delegate: Any, *, mode: str) -> None:
        self.delegate = delegate
        self.mode = mode
        self.mutated = False

    def read_transition_reservation_history(self, intent: Any) -> Any:
        return self.delegate.read_transition_reservation_history(intent)

    def observe_transition_reservation(self, intent: Any, *, claim_generation: int) -> Any:
        return self.delegate.observe_transition_reservation(
            intent,
            claim_generation=claim_generation,
        )

    def reserve_transition_effect(self, intent: Any, **values: Any) -> Any:
        if not self.mutated:
            if self.mode == "heartbeat":
                current = self.delegate.get(
                    tenant_id=intent.tenant_id,
                    run_id=intent.run_id,
                    step_id=intent.step_id,
                )
                assert current is not None
                self.delegate.heartbeat(
                    tenant_id=intent.tenant_id,
                    run_id=intent.run_id,
                    step_id=intent.step_id,
                    attempt_id=current.attempt_id,
                    owner_id=current.owner_id,
                    fencing_token=current.fencing_token,
                    expected_revision=current.revision,
                    lease_seconds=100.0,
                    now=1_005.0,
                )
            else:
                self.delegate.consume_retry(
                    tenant_id=intent.tenant_id,
                    run_id=intent.run_id,
                    retry_id="unrelated-occ-retry",
                    category="hub_task",
                    maximum=intent.maximum_retries,
                )
            self.mutated = True
        return self.delegate.reserve_transition_effect(intent, **values)


class _CommitDuringCurrentReadAuthority:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.committed = False
        self.executor_commits = 0

    def read_transition_reservation_history(self, intent: Any) -> Any:
        return self.delegate.read_transition_reservation_history(intent)

    def observe_transition_reservation(self, intent: Any, *, claim_generation: int) -> Any:
        if not self.committed:
            observed = self.delegate.observe_transition_reservation(
                intent,
                claim_generation=claim_generation,
            )
            self.delegate.reserve_transition_effect(
                intent,
                creator_claim_generation=claim_generation,
                expected_observation_digest=observed.observation_digest,
                reserved_at=1_002.0,
            )
            self.committed = True
            raise WorkflowTransitionOwnershipReservationConflict("raced")
        return self.delegate.observe_transition_reservation(
            intent,
            claim_generation=claim_generation,
        )

    def reserve_transition_effect(self, intent: Any, **values: Any) -> Any:
        self.executor_commits += 1
        return self.delegate.reserve_transition_effect(intent, **values)


class _WinnerBetweenLostCommitHistoryAndCurrentRead:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.commit_failed = False
        self.winner_committed = False

    def read_transition_reservation_history(self, intent: Any) -> Any:
        evidence = self.delegate.read_transition_reservation_history(intent)
        if self.commit_failed and not self.winner_committed:
            observed = self.delegate.observe_transition_reservation(
                intent,
                claim_generation=1,
            )
            self.delegate.reserve_transition_effect(
                intent,
                creator_claim_generation=1,
                expected_observation_digest=observed.observation_digest,
                reserved_at=1_002.0,
            )
            self.winner_committed = True
        return evidence

    def observe_transition_reservation(self, intent: Any, *, claim_generation: int) -> Any:
        return self.delegate.observe_transition_reservation(
            intent,
            claim_generation=claim_generation,
        )

    def reserve_transition_effect(self, intent: Any, **values: Any) -> Any:
        del intent, values
        self.commit_failed = True
        raise RuntimeError("lost before commit")


def test_winner_between_lost_commit_history_and_current_read_is_adopted() -> None:
    store = InMemoryExecutionOwnershipStore()
    transition, planned = _plan()
    claimed = _claimed(transition, 1)
    executable = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(_observation(claimed, planned), heartbeat=_Heartbeat())
    assert type(executable) is EffectExecutable

    result = WorkflowTransitionOwnershipReservationExecutor(
        authority=_WinnerBetweenLostCommitHistoryAndCurrentRead(store),
        clock=lambda: 1_003.0,
    ).execute(
        _attempt(claimed, _applying(planned, 1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )

    assert type(result) is EffectApplied


def test_receipt_committed_between_history_and_current_read_is_adopted() -> None:
    store = InMemoryExecutionOwnershipStore()
    transition, planned = _plan()
    claimed = _claimed(transition, 1)
    executable = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(_observation(claimed, planned), heartbeat=_Heartbeat())
    assert type(executable) is EffectExecutable
    authority = _CommitDuringCurrentReadAuthority(store)

    result = WorkflowTransitionOwnershipReservationExecutor(
        authority=authority,
        clock=lambda: 1_003.0,
    ).execute(
        _attempt(claimed, _applying(planned, 1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )

    assert type(result) is EffectApplied
    assert authority.executor_commits == 0
    assert store.read_transition_reservation_history(_intent(claimed, planned)).receipt is not None


@pytest.mark.parametrize("post_commit_read_fails", (False, True))
def test_lost_response_adopts_exact_history_or_retries_without_second_commit(
    post_commit_read_fails: bool,
) -> None:
    store = InMemoryExecutionOwnershipStore()
    transition, planned = _plan()
    claimed = _claimed(transition, 1)
    executable = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 1_001.0,
    ).observe_or_adopt(_observation(claimed, planned), heartbeat=_Heartbeat())
    assert type(executable) is EffectExecutable
    authority = _LostResponseAuthority(
        store,
        fail_history_after_commit=post_commit_read_fails,
    )
    result = WorkflowTransitionOwnershipReservationExecutor(
        authority=authority,
        clock=lambda: 1_002.0,
    ).execute(
        _attempt(claimed, _applying(planned, 1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(result) is (EffectRetry if post_commit_read_fails else EffectApplied)
    intent = _intent(claimed, planned)
    history = store.read_transition_reservation_history(intent)
    assert history.receipt is not None
    assert history.receipt.retry_budget_used_after == 0

    adopted = WorkflowTransitionOwnershipReservationExecutor(
        authority=store,
        clock=lambda: 2_000.0,
    ).execute(
        _attempt(_claimed(claimed, 2), _applying(planned, 2)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectApplied
    assert (
        store.get_retry_budget(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            maximum=intent.maximum_retries,
        ).used
        == 0
    )


def test_direct_commit_port_reports_stale_observation_without_mutation() -> None:
    store = InMemoryExecutionOwnershipStore()
    transition, effect = _plan()
    claimed = _claimed(transition, 1)
    intent = _intent(claimed, effect)
    observed = store.observe_transition_reservation(intent, claim_generation=1)
    store.consume_retry(
        tenant_id=intent.tenant_id,
        run_id=intent.run_id,
        retry_id="unrelated-stale-retry",
        category="hub_task",
        maximum=intent.maximum_retries,
    )

    with pytest.raises(WorkflowTransitionOwnershipReservationStale):
        store.reserve_transition_effect(
            intent,
            creator_claim_generation=1,
            expected_observation_digest=observed.observation_digest,
            reserved_at=1_002.0,
        )
    assert (
        store.get(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            step_id=intent.step_id,
        )
        is None
    )
    assert store.read_transition_reservation_history(intent).receipt is None


@pytest.mark.parametrize("occ_mode", ("heartbeat", "budget"))
def test_executor_reproofs_heartbeat_or_budget_progress_as_retry(
    reservation_store: _StoreCase,
    occ_mode: str,
) -> None:
    store = reservation_store.store
    legacy_now = 1_000.0 if occ_mode == "heartbeat" else 900.0
    store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        owner_id="legacy-owner",
        lease_seconds=10.0,
        maximum_retries=3,
        now=legacy_now,
    )
    transition, planned = _plan()
    claimed = _claimed(transition, 1)
    decision_at = 1_011.0 if occ_mode == "heartbeat" else 1_001.0
    executable = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: decision_at,
    ).observe_or_adopt(
        _observation(claimed, planned),
        heartbeat=_Heartbeat(),
    )
    assert type(executable) is EffectExecutable
    result = WorkflowTransitionOwnershipReservationExecutor(
        authority=_OCCBeforeCommitAuthority(store, mode=occ_mode),
        clock=lambda: decision_at,
    ).execute(
        _attempt(claimed, _applying(planned, 1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectRetry
    intent = _intent(claimed, planned)
    assert store.read_transition_reservation_history(intent).receipt is None
    assert store.get_retry_budget(
        tenant_id=intent.tenant_id,
        run_id=intent.run_id,
        maximum=intent.maximum_retries,
    ).used == (1 if occ_mode == "budget" else 0)


def test_fault_before_receipt_publish_rolls_back_all_authority_state(
    reservation_store: _StoreCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transition, effect = _plan()
    claimed = _claimed(transition, 1)
    intent = _intent(claimed, effect)
    observation = reservation_store.store.observe_transition_reservation(
        intent,
        claim_generation=1,
    )

    def fail(stage: str, value: object) -> None:
        del value
        if stage == "after_history":
            raise RuntimeError("fault")

    monkeypatch.setattr(
        reservation_store.store,
        "_transition_reservation_fault",
        fail,
    )
    with pytest.raises(RuntimeError, match="fault"):
        reservation_store.store.reserve_transition_effect(
            intent,
            creator_claim_generation=1,
            expected_observation_digest=observation.observation_digest,
            reserved_at=1_002.0,
        )
    after = reservation_store.store.observe_transition_reservation(
        intent,
        claim_generation=1,
    )
    historical = reservation_store.store.read_transition_reservation_history(intent)
    assert after.current is None
    assert after.receipt is None
    assert after.retry_budget.used == 0
    assert historical.receipt is None


class _SimulatedCrash(BaseException):
    pass


@pytest.mark.parametrize(
    "stage",
    (
        "before_retry",
        "after_retry",
        "after_current",
        "after_history",
        "after_receipt",
        "before_commit",
    ),
)
def test_base_exception_rolls_back_current_history_retry_and_receipt_at_every_stage(
    reservation_store: _StoreCase,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    store = reservation_store.store
    legacy = store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        owner_id="legacy-owner",
        lease_seconds=10.0,
        maximum_retries=3,
        now=900.0,
    )
    transition, effect = _plan()
    claimed = _claimed(transition, 1)
    intent = _intent(claimed, effect)
    observed = store.observe_transition_reservation(intent, claim_generation=1)

    def crash(current_stage: str, value: object) -> None:
        del value
        if current_stage == stage:
            raise _SimulatedCrash(stage)

    monkeypatch.setattr(store, "_transition_reservation_fault", crash)
    with pytest.raises(_SimulatedCrash):
        store.reserve_transition_effect(
            intent,
            creator_claim_generation=1,
            expected_observation_digest=observed.observation_digest,
            reserved_at=1_002.0,
        )

    after = store.observe_transition_reservation(intent, claim_generation=1)
    historical = store.read_transition_reservation_history(intent)
    assert after.current == legacy.ownership
    assert after.current_history == legacy.ownership
    assert after.retry_consumption is None
    assert after.retry_budget.used == 0
    assert after.receipt is None
    assert historical.receipt is None


def test_after_commit_crash_is_adopted_from_history_without_second_retry(
    reservation_store: _StoreCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = reservation_store.store
    legacy = store.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        owner_id="legacy-owner",
        lease_seconds=10.0,
        maximum_retries=3,
        now=900.0,
    )
    transition, effect = _plan()
    claimed = _claimed(transition, 1)
    intent = _intent(claimed, effect)
    observed = store.observe_transition_reservation(intent, claim_generation=1)

    def crash(stage: str, value: object) -> None:
        del value
        if stage == "after_commit":
            raise _SimulatedCrash(stage)

    monkeypatch.setattr(store, "_transition_reservation_fault", crash)
    with pytest.raises(_SimulatedCrash):
        store.reserve_transition_effect(
            intent,
            creator_claim_generation=1,
            expected_observation_digest=observed.observation_digest,
            reserved_at=1_002.0,
        )
    history = store.read_transition_reservation_history(intent)
    assert history.receipt is not None
    assert history.receipt.prior_ownership == legacy.ownership
    assert history.receipt.retry_budget_used_after == 1

    adopted = WorkflowTransitionOwnershipReservationObserver(
        reads=store,
        clock=lambda: 2_000.0,
    ).observe_or_adopt(
        _observation(_claimed(claimed, 2), _applying(effect, 1)),
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectAlreadyApplied
    assert (
        store.get_retry_budget(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            maximum=intent.maximum_retries,
        ).used
        == 1
    )


def test_concurrent_initial_commit_has_one_exact_receipt(
    reservation_store: _StoreCase,
) -> None:
    transition, effect = _plan()
    claimed = _claimed(transition, 1)
    intent = _intent(claimed, effect)
    observed = reservation_store.store.observe_transition_reservation(
        intent,
        claim_generation=1,
    )

    def commit() -> Any:
        try:
            return reservation_store.store.reserve_transition_effect(
                intent,
                creator_claim_generation=1,
                expected_observation_digest=observed.observation_digest,
                reserved_at=1_002.0,
            )
        except WorkflowTransitionOwnershipReservationConflict:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = tuple(pool.map(lambda _: commit(), range(8)))
    receipts = tuple(value for value in outcomes if value is not None)
    assert receipts
    assert len({value.receipt_digest for value in receipts}) == 1
    history = reservation_store.store.read_transition_reservation_history(intent)
    assert history.receipt is not None
    assert history.acquired_history == history.receipt.acquired_ownership
    assert (
        reservation_store.store.get_retry_budget(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            maximum=intent.maximum_retries,
        ).used
        == 0
    )


@pytest.mark.parametrize("preexisting_current", (False, True))
@pytest.mark.parametrize("backend", ("sqlite", "sql"))
def test_cross_instance_reservation_race_converges_on_one_exact_receipt(
    tmp_path: Path,
    backend: str,
    preexisting_current: bool,
) -> None:
    resources: list[Any] = []
    if backend == "sqlite":
        path = tmp_path / "cross-instance-direct.sqlite"
        first_store = SQLiteExecutionOwnershipStore(path)
        second_store = SQLiteExecutionOwnershipStore(path)
        resources.extend((first_store, second_store))
    else:
        url = f"sqlite:///{tmp_path / 'cross-instance-sql.sqlite'}"
        first_engine = create_engine(url, connect_args={"check_same_thread": False})
        second_engine = create_engine(url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(first_engine, tables=_TABLES)
        first_store = SQLAlchemyExecutionOwnershipStore(first_engine)
        second_store = SQLAlchemyExecutionOwnershipStore(second_engine)
        resources.extend((first_engine, second_engine))
    try:
        if preexisting_current:
            first_store.claim(
                tenant_id="tenant-a",
                workflow_id="workflow-a",
                run_id="run-a",
                step_id="step-a",
                owner_id="legacy-owner",
                lease_seconds=10.0,
                maximum_retries=3,
                now=900.0,
            )
        transition, effect = _plan()
        claimed = _claimed(transition, 1)
        intent = _intent(claimed, effect)
        first_observation = first_store.observe_transition_reservation(
            intent,
            claim_generation=1,
        )
        second_observation = second_store.observe_transition_reservation(
            intent,
            claim_generation=1,
        )
        barrier = Barrier(2)

        def commit(store: Any, observed: Any) -> Any:
            barrier.wait()
            return store.reserve_transition_effect(
                intent,
                creator_claim_generation=1,
                expected_observation_digest=observed.observation_digest,
                reserved_at=1_002.0,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = tuple(
                pool.submit(commit, store, observed)
                for store, observed in (
                    (first_store, first_observation),
                    (second_store, second_observation),
                )
            )
            outcomes = tuple(future.result() for future in futures)
        history = first_store.read_transition_reservation_history(intent)
        assert history.receipt is not None
        assert outcomes == (history.receipt, history.receipt)
        assert len({receipt.receipt_digest for receipt in outcomes}) == 1
        assert history.acquired_history == history.receipt.acquired_ownership
        assert first_store.get_retry_budget(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            maximum=intent.maximum_retries,
        ).used == (1 if preexisting_current else 0)
    finally:
        for resource in resources:
            resource.close() if backend == "sqlite" else resource.dispose()


def test_durable_validator_rejects_tampered_persisted_result(
    reservation_store: _StoreCase,
) -> None:
    claimed, applying, _executable, applied, _intent_value = _initial_execution(reservation_store.store)
    envelope = thaw_json(
        workflow_transition_effect_result_envelope(
            mode="execute",
            result_payload=applied.result_payload,
            proof_payload=applied.proof_payload,
            stage_attempt_count=1,
        )
    )
    envelope["effect_result"]["receipt"]["receipt_digest"] = "f" * 64
    tampered = replace(
        applying,
        state=EFFECT_STATE_APPLIED,
        applied_generation=1,
        result_payload=envelope,
        result_digest=workflow_transition_effect_result_digest(envelope),
        revision=applying.revision + 2,
        updated_at=1_011.0,
    )
    with pytest.raises(WorkflowTransitionOwnershipReservationError):
        assert_durable_workflow_transition_ownership_reservation_proof(
            transition=claimed,
            effect=tampered,
            reads=reservation_store.store,
        )


def test_ownership_reservation_effect_is_imported_only_by_the_cutover_composition() -> None:
    repository = Path(__file__).resolve().parents[1]
    services = repository / "agent" / "services"
    # The Native cutover composition is the single sanctioned importer.  Any
    # other production import would reserve ownership outside the registry
    # seam, where no transition claim fences the reservation.
    allowed = {
        (services / "workflow_transition_ownership_reservation.py").resolve(),
        (services / "workflow_transition_native_composition.py").resolve(),
    }
    forbidden = (
        "from agent.services.workflow_transition_ownership_reservation import",
        "import agent.services.workflow_transition_ownership_reservation",
    )
    offenders = []
    for path in (repository / "agent").rglob("*.py"):
        if path.resolve() in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if any(value in source for value in forbidden):
            offenders.append(path.relative_to(repository).as_posix())
    assert offenders == []
