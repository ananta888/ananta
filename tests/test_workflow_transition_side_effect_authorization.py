from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlmodel import SQLModel, create_engine

from agent.db_models.workflow_runtime import (
    WorkflowSideEffectLedgerDB,
    WorkflowTransitionSideEffectAuthorizationDB,
)
from agent.services import workflow_transition_side_effect_authorization as authorization
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.side_effects import (
    InMemorySideEffectLedger,
    SQLiteSideEffectLedger,
    WorkflowTransitionSideEffectAuthorizationIntent,
    WorkflowTransitionSideEffectAuthorizationReceipt,
    workflow_transition_side_effect_authorization_receipt_id,
    workflow_transition_side_effect_operation_fence_id,
    workflow_transition_side_effect_operation_intent_digest,
)
from agent.services.workflow_runtime.sqlalchemy_side_effects import (
    SQLAlchemySideEffectLedger,
)
from agent.services.workflow_transition_effect_execution import (
    EffectAlreadyApplied,
    EffectApplied,
    EffectExecutable,
    EffectQuarantine,
    WorkflowTransitionEffectAttempt,
    WorkflowTransitionEffectObservation,
)
from agent.services.workflow_transition_effect_proofs import (
    WorkflowTransitionEffectProofError,
    WorkflowTransitionEffectResourceProof,
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
    workflow_transition_effect_result_digest,
    workflow_transition_effect_result_envelope,
    workflow_transition_id,
)
from agent.services.workflow_transition_side_effect_authorization import (
    WorkflowTransitionSideEffectAuthorizationError,
    WorkflowTransitionSideEffectAuthorizationExecutor,
    WorkflowTransitionSideEffectAuthorizationObserver,
    assert_active_workflow_transition_side_effect_authorization_proof,
    assert_durable_workflow_transition_side_effect_authorization_proof,
    build_workflow_transition_side_effect_authorization_effect,
    workflow_transition_side_effect_authorization_receipt_from_result,
)
from ananta_contracts.workflow_operation import operation_id_for

_TABLES = [
    WorkflowSideEffectLedgerDB.__table__,
    WorkflowTransitionSideEffectAuthorizationDB.__table__,
]
_KNOWN_TRANSITION_ID = "wft-42e6c9548ca683d1c61fed0d1d2f654bf4014845cf1ec53241a008ac484295a0"
_KNOWN_OPERATION_ID = "op-2780b2443a72b69b92533690e60b00a3"
_KNOWN_OPERATION_INTENT_DIGEST = "004d98142bf99f0466d306e15cac05a020373700b334fd702cdaa367c5573387"
_KNOWN_OPERATION_FENCE_ID = "wftsf-3477f461da7adfccbb185c2eac7d9482b8089ff6404868d0b4f7593fed1bdc32"
_KNOWN_EFFECT_ID = "wfx-e5f50aec2a30639f0f7e2e54d5d6875d0f5ddf7f09a338199a20d24c6527b9de"
_KNOWN_RECEIPT_ID = "wftsar-aacc338e15eb6545776fb1c5742f8980c4f623e061d598424e58a9150a14b55a"
_KNOWN_EFFECT_BYTES = (
    '{"applied_generation":0,"created_at":1000.0,"effect_id":"wfx-e5f50aec2a30639f0f7e2e54d5d6875d0f5'
    'ddf7f09a338199a20d24c6527b9de","idempotency_key":"wftsf-3477f461da7adfccbb185c2eac7d9482b8089f'
    'f6404868d0b4f7593fed1bdc32","kind":"side_effect_authorize","ordinal":1,"payload":{"authorizatio'
    'n_envelope_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","authorizati'
    'on_envelope_id":"envelope-a","declared_operation":"provider.write:artifact-a","effect_id":"wfx-e5f50aec'
    '2a30639f0f7e2e54d5d6875d0f5ddf7f09a338199a20d24c6527b9de","effect_ordinal":1,"operation_fence'
    '_id":"wftsf-3477f461da7adfccbb185c2eac7d9482b8089ff6404868d0b4f7593fed1bdc32","operation_id":"op-'
    '2780b2443a72b69b92533690e60b00a3","operation_intent_digest":"004d98142bf99f0466d306e15cac05a0'
    '20373700b334fd702cdaa367c5573387","operation_payload_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    'aaaaaaaaaaaaaaaaaaaaaaaaaa","ownership_attempt_id":"ownership-attempt-a","ownership_fencing_token":11'
    ',"receipt_id":"wftsar-aacc338e15eb6545776fb1c5742f8980c4f623e061d598424e58a9150a14b55a","run_i'
    'd":"run-a","runtime_id":"ananta-native","schema":"ananta.workflow_transition_side_effect_authorization_eff'
    'ect.v1","side_effect_class":"idempotent_write","step_id":"step-a","tenant_id":"tenant-a","transitio'
    'n_id":"wft-42e6c9548ca683d1c61fed0d1d2f654bf4014845cf1ec53241a008ac484295a0","workflow_id":"workfl'
    'ow-a"},"payload_digest":"3b24dcfcba5ca47caac656fec1501a32338a062edae62fdedd180c181f35d976","result_di'
    'gest":"","result_payload":{},"revision":1,"schema":"ananta.workflow-transition-effect.v1","state":"plan'
    'ned","transition_id":"wft-42e6c9548ca683d1c61fed0d1d2f654bf4014845cf1ec53241a008ac484295a0","upd'
    'ated_at":1000.0}'
)


@dataclass
class _StoreCase:
    name: str
    store: Any
    engine: sa.Engine | None = None
    path: Path | None = None


@pytest.fixture(params=("memory", "sqlite", "sql"))
def authorization_store(request: pytest.FixtureRequest, tmp_path: Path) -> _StoreCase:
    if request.param == "memory":
        return _StoreCase("memory", InMemorySideEffectLedger())
    if request.param == "sqlite":
        path = tmp_path / "side-effect-authorization.sqlite"
        store = SQLiteSideEffectLedger(path)
        request.addfinalizer(store.close)
        return _StoreCase("sqlite", store, path=path)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'side-effect-authorization-sql.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    request.addfinalizer(engine.dispose)
    return _StoreCase("sql", SQLAlchemySideEffectLedger(engine), engine)


class _Heartbeat:
    def __init__(self) -> None:
        self.calls = 0

    def heartbeat(self) -> None:
        self.calls += 1


def _plan(
    *,
    identity_key: str = "side-effect-transition-a",
    runtime_id: str = TRANSITION_RUNTIME_NATIVE,
    ownership_attempt_id: str = "ownership-attempt-a",
    ownership_fencing_token: int = 11,
    authorization_envelope_id: str = "envelope-a",
    authorization_envelope_digest: str = "b" * 64,
    operation_payload_digest: str = "a" * 64,
    step_id: str = "step-a",
    declared_operation: str = "provider.write:artifact-a",
) -> tuple[WorkflowTransition, WorkflowTransitionEffect]:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        kind=TRANSITION_KIND_ADVANCE,
        identity_key=identity_key,
    )
    effect = build_workflow_transition_side_effect_authorization_effect(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        ordinal=1,
        step_id=step_id,
        declared_operation=declared_operation,
        side_effect_class="idempotent_write",
        operation_payload_digest=operation_payload_digest,
        authorization_envelope_id=authorization_envelope_id,
        authorization_envelope_digest=authorization_envelope_digest,
        ownership_attempt_id=ownership_attempt_id,
        ownership_fencing_token=ownership_fencing_token,
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


def _claimed(
    transition: WorkflowTransition,
    *,
    generation: int,
) -> WorkflowTransition:
    return replace(
        transition,
        state=TRANSITION_STATE_APPLYING,
        claim_owner=f"runner-{generation}",
        claim_generation=generation,
        claim_expires_at=1_100.0 + generation,
        last_heartbeat_at=1_000.0 + generation,
        attempt_count=generation,
        revision=transition.revision + generation,
        updated_at=1_000.0 + generation,
    )


def _applying(
    effect: WorkflowTransitionEffect,
    *,
    generation: int,
) -> WorkflowTransitionEffect:
    return replace(
        effect,
        state=EFFECT_STATE_APPLYING,
        applied_generation=generation,
        revision=effect.revision + 1,
        updated_at=1_000.0 + generation,
    )


def _intent(
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
) -> WorkflowTransitionSideEffectAuthorizationIntent:
    return authorization._authorization_intent(
        transition=transition,
        effect=effect,
        claim_generation=transition.claim_generation,
    )


def _observe(
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


def _applied_effect(
    effect: WorkflowTransitionEffect,
    result: EffectApplied | EffectAlreadyApplied,
    *,
    generation: int,
    mode: str,
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


def _receipt_count(case: _StoreCase) -> int:
    if case.name == "memory":
        return len(case.store._transition_authorization_receipts)
    if case.name == "sqlite":
        return int(
            case.store._connection.execute(
                "SELECT COUNT(*) FROM workflow_transition_side_effect_authorizations"
            ).fetchone()[0]
        )
    assert case.engine is not None
    with Session(case.engine) as session:
        return int(
            session.scalar(sa.select(sa.func.count()).select_from(WorkflowTransitionSideEffectAuthorizationDB)) or 0
        )


def _delete_ledger_record(case: _StoreCase, operation_id: str) -> None:
    if case.name == "memory":
        case.store._records.pop(operation_id)
    elif case.name == "sqlite":
        case.store._connection.execute(
            "DELETE FROM workflow_side_effect_ledger WHERE operation_id = ?",
            (operation_id,),
        )
    else:
        assert case.engine is not None
        with Session(case.engine) as session, session.begin():
            session.execute(
                sa.delete(WorkflowSideEffectLedgerDB).where(WorkflowSideEffectLedgerDB.operation_id == operation_id)
            )


def test_authorization_effect_planning_is_deterministic_and_external_operation_stable() -> None:
    from agent.services.workflow_runtime import side_effects as side_effects_module

    with patch.object(
        side_effects_module.time,
        "time",
        side_effect=AssertionError("clock used"),
    ):
        transition, first = _plan()
        _other_transition, second = _plan()
    raw = dict(first.payload)
    expected_operation = operation_id_for(
        tenant_id="tenant-a",
        run_id="run-a",
        step_id="step-a",
        declared_operation="provider.write:artifact-a",
    )
    assert first == second
    assert raw["operation_id"] == expected_operation
    assert transition.transition_id == _KNOWN_TRANSITION_ID
    assert raw["operation_id"] == _KNOWN_OPERATION_ID
    assert raw["operation_intent_digest"] == _KNOWN_OPERATION_INTENT_DIGEST
    assert raw["operation_intent_digest"] == workflow_transition_side_effect_operation_intent_digest(
        operation_id=expected_operation,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        declared_operation="provider.write:artifact-a",
        side_effect_class="idempotent_write",
        operation_payload_digest="a" * 64,
    )
    assert raw["operation_fence_id"] == _KNOWN_OPERATION_FENCE_ID
    assert raw["operation_fence_id"] == workflow_transition_side_effect_operation_fence_id(
        operation_id=expected_operation,
        operation_intent_digest=raw["operation_intent_digest"],
        ownership_attempt_id="ownership-attempt-a",
        ownership_fencing_token=11,
        authorization_envelope_id="envelope-a",
        authorization_envelope_digest="b" * 64,
    )
    assert first.effect_id == _KNOWN_EFFECT_ID
    assert raw["receipt_id"] == _KNOWN_RECEIPT_ID
    assert raw["receipt_id"] == workflow_transition_side_effect_authorization_receipt_id(
        transition_id=transition.transition_id,
        effect_id=first.effect_id,
    )
    assert raw["operation_id"] == operation_id_for(
        tenant_id="tenant-a",
        run_id="run-a",
        step_id="step-a",
        declared_operation="provider.write:artifact-a",
    )
    assert first.created_at == transition.created_at == 1_000.0
    assert canonical_json(first.to_dict()) == _KNOWN_EFFECT_BYTES


@pytest.mark.parametrize("runtime_id", (TRANSITION_RUNTIME_NATIVE, TRANSITION_RUNTIME_LANGGRAPH))
def test_observe_execute_and_active_durable_proofs_are_exact_and_read_only(
    authorization_store: _StoreCase,
    runtime_id: str,
) -> None:
    from agent.services.workflow_runtime import side_effects as side_effects_module

    transition, effect = _plan(runtime_id=runtime_id)
    claimed = _claimed(transition, generation=1)
    heartbeat = _Heartbeat()
    observer = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=runtime_id,
        reads=authorization_store.store,
    )
    with (
        patch.object(
            side_effects_module.time,
            "time",
            side_effect=AssertionError("clock used"),
        ),
        patch("uuid.uuid4", side_effect=AssertionError("uuid used")),
    ):
        executable = observer.observe_or_adopt(
            _observe(claimed, effect),
            heartbeat=heartbeat,
        )
        assert _receipt_count(authorization_store) == 0
        intent = _intent(claimed, effect)
        assert (
            authorization_store.store.get(
                tenant_id=intent.tenant_id,
                operation_id=intent.operation_id,
            )
            is None
        )
        applying = _applying(effect, generation=1)
        executor = WorkflowTransitionSideEffectAuthorizationExecutor(
            runtime_id=runtime_id,
            authority=authorization_store.store,
        )
        applied = executor.execute(
            _attempt(claimed, applying),
            executable=executable,
            heartbeat=heartbeat,
        )
    assert type(executable) is EffectExecutable
    assert heartbeat.calls == 0
    assert type(applied) is EffectApplied
    assert heartbeat.calls == 0
    receipt = workflow_transition_side_effect_authorization_receipt_from_result(applied.result_payload)
    assert receipt.prior_status == "absent"
    assert receipt.prior_revision == 0
    assert receipt.authorized_ledger_revision == 2
    assert receipt.planned_at == receipt.authorized_at == effect.created_at
    assert receipt.authorized_record.updated_at == effect.created_at
    assert receipt.authorized_record.status == "authorized"
    assert receipt.authorized_record.attempt_id == ""
    assert receipt.creator_claim_generation == 1
    assert _receipt_count(authorization_store) == 1
    assert (
        assert_active_workflow_transition_side_effect_authorization_proof(
            applied.proof_payload,
            transition=claimed,
            effect=applying,
            claim_generation=1,
            reads=authorization_store.store,
        ).resource_digest
        == receipt.receipt_digest
    )

    durable_effect = _applied_effect(
        applying,
        applied,
        generation=1,
        mode="execute",
    )
    assert (
        assert_durable_workflow_transition_side_effect_authorization_proof(
            applied.proof_payload,
            transition=claimed,
            effect=durable_effect,
            reads=authorization_store.store,
        ).resource_id
        == receipt.receipt_id
    )
    detached = receipt.to_dict()
    detached["authorized_record"]["status"] = "failed"
    reread = authorization_store.store.observe_transition_authorization(_intent(claimed, effect)).receipt
    assert reread == receipt
    assert reread.authorized_record.status == "authorized"


def test_persisted_receipt_rehydrates_after_store_restart(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="restart-a")
    claimed = _claimed(transition, generation=1)
    intent = _intent(claimed, effect)
    before = authorization_store.store.observe_transition_authorization(intent)
    receipt = authorization_store.store.authorize_transition_effect(
        intent,
        expected_observation_digest=before.observation_digest,
    )
    if authorization_store.name == "memory":
        restarted = InMemorySideEffectLedger()
        restarted._records = dict(authorization_store.store._records)
        restarted._transition_authorization_receipts = dict(
            authorization_store.store._transition_authorization_receipts
        )
    elif authorization_store.name == "sqlite":
        assert authorization_store.path is not None
        restarted = SQLiteSideEffectLedger(authorization_store.path)
    else:
        assert authorization_store.engine is not None
        restarted = SQLAlchemySideEffectLedger(authorization_store.engine)
    try:
        observed = restarted.observe_transition_authorization(intent)
        assert observed.receipt == receipt
        assert observed.receipt.authorized_record == receipt.authorized_record
    finally:
        if authorization_store.name == "sqlite":
            restarted.close()


def test_authorize_adopts_exact_direct_retry_and_rejects_invalid_digest_lsp(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan()
    claimed = _claimed(transition, generation=1)
    intent = _intent(claimed, effect)
    before = authorization_store.store.observe_transition_authorization(intent)
    first = authorization_store.store.authorize_transition_effect(
        intent,
        expected_observation_digest=before.observation_digest,
    )
    assert (
        authorization_store.store.authorize_transition_effect(
            intent,
            expected_observation_digest="0" * 64,
        )
        == first
    )

    other_transition, other_effect = _plan(
        identity_key="invalid-digest-a",
        step_id="step-invalid-digest",
        declared_operation="provider.write:invalid-digest",
    )
    other_claimed = _claimed(other_transition, generation=1)
    other_intent = _intent(other_claimed, other_effect)
    snapshot = authorization_store.store.observe_transition_authorization(other_intent)
    for invalid in (None, True, 7, "A" * 64, "a" * 63, "g" * 64):
        with pytest.raises(ValueError, match="expected_observation_digest_invalid"):
            authorization_store.store.authorize_transition_effect(
                other_intent,
                expected_observation_digest=invalid,  # type: ignore[arg-type]
            )
        assert authorization_store.store.observe_transition_authorization(other_intent) == snapshot


def test_concurrent_identical_direct_committers_converge_on_one_receipt(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="concurrent-a")
    intent = _intent(_claimed(transition, generation=1), effect)
    before = authorization_store.store.observe_transition_authorization(intent)

    def commit() -> WorkflowTransitionSideEffectAuthorizationReceipt:
        return authorization_store.store.authorize_transition_effect(
            intent,
            expected_observation_digest=before.observation_digest,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = tuple(pool.map(lambda _value: commit(), range(2)))
    assert receipts[0] == receipts[1]
    assert _receipt_count(authorization_store) == 1


def test_concurrent_identical_committers_converge_from_preplanned_ledger(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="concurrent-planned-a")
    intent = _intent(_claimed(transition, generation=1), effect)
    planned = authorization_store.store.plan(
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        declared_operation=intent.declared_operation,
        side_effect_class=intent.side_effect_class,
    )
    assert planned.status == "planned" and planned.revision == 1
    before = authorization_store.store.observe_transition_authorization(intent)

    def commit() -> WorkflowTransitionSideEffectAuthorizationReceipt:
        return authorization_store.store.authorize_transition_effect(
            intent,
            expected_observation_digest=before.observation_digest,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = tuple(pool.map(lambda _value: commit(), range(2)))
    assert receipts[0] == receipts[1]
    assert receipts[0].prior_status == "planned"
    assert receipts[0].authorized_ledger_revision == 2
    assert _receipt_count(authorization_store) == 1


@pytest.mark.parametrize("stage", ("after_plan", "after_authorize", "after_publish"))
def test_memory_baseexception_fault_rolls_back_both_ledger_and_receipt(stage: str) -> None:
    class _FaultStore(InMemorySideEffectLedger):
        def _transition_authorization_fault(self, current: str, value: object) -> None:
            del value
            if current == stage:
                raise KeyboardInterrupt("crash")

    store = _FaultStore()
    transition, effect = _plan(identity_key=f"memory-crash-{stage}")
    intent = _intent(_claimed(transition, generation=1), effect)
    before = store.observe_transition_authorization(intent)
    with pytest.raises(KeyboardInterrupt, match="crash"):
        store.authorize_transition_effect(
            intent,
            expected_observation_digest=before.observation_digest,
        )
    assert store.observe_transition_authorization(intent) == before
    assert store.get(tenant_id=intent.tenant_id, operation_id=intent.operation_id) is None


@pytest.mark.parametrize("stage", ("after_plan", "after_authorize", "before_commit"))
def test_sqlite_baseexception_fault_rolls_back_both_ledger_and_receipt(
    tmp_path: Path,
    stage: str,
) -> None:
    class _FaultStore(SQLiteSideEffectLedger):
        def _transition_authorization_fault(self, current: str, value: object) -> None:
            del value
            if current == stage:
                raise KeyboardInterrupt("crash")

    store = _FaultStore(tmp_path / f"sqlite-crash-{stage}.sqlite")
    try:
        transition, effect = _plan(identity_key=f"sqlite-crash-{stage}")
        intent = _intent(_claimed(transition, generation=1), effect)
        before = store.observe_transition_authorization(intent)
        with pytest.raises(KeyboardInterrupt, match="crash"):
            store.authorize_transition_effect(
                intent,
                expected_observation_digest=before.observation_digest,
            )
        assert store.observe_transition_authorization(intent) == before
        assert store.get(tenant_id=intent.tenant_id, operation_id=intent.operation_id) is None
    finally:
        store.close()


@pytest.mark.parametrize("preplanned", (False, True))
def test_sql_baseexception_during_receipt_insert_rolls_back_whole_uow(
    authorization_store: _StoreCase,
    preplanned: bool,
) -> None:
    if authorization_store.name != "sql":
        pytest.skip("SQLAlchemy-specific transaction fault")
    assert authorization_store.engine is not None
    transition, effect = _plan(identity_key=f"sql-baseexception-{preplanned}")
    intent = _intent(_claimed(transition, generation=1), effect)
    if preplanned:
        baseline = authorization_store.store.plan(
            tenant_id=intent.tenant_id,
            workflow_id=intent.workflow_id,
            run_id=intent.run_id,
            step_id=intent.step_id,
            declared_operation=intent.declared_operation,
            side_effect_class=intent.side_effect_class,
        )
    else:
        baseline = None
    before = authorization_store.store.observe_transition_authorization(intent)

    def crash(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        if statement.lstrip().upper().startswith("INSERT") and (
            "workflow_transition_side_effect_authorizations" in statement
        ):
            raise KeyboardInterrupt("receipt insert crash")

    event.listen(authorization_store.engine, "before_cursor_execute", crash)
    try:
        with pytest.raises(KeyboardInterrupt, match="receipt insert crash"):
            authorization_store.store.authorize_transition_effect(
                intent,
                expected_observation_digest=before.observation_digest,
            )
    finally:
        event.remove(authorization_store.engine, "before_cursor_execute", crash)
    assert authorization_store.store.observe_transition_authorization(intent) == before
    assert (
        authorization_store.store.get(
            tenant_id=intent.tenant_id,
            operation_id=intent.operation_id,
        )
        == baseline
    )
    assert _receipt_count(authorization_store) == 0


def test_lost_response_is_adopted_next_generation_without_second_commit(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="lost-response-a")
    first_claim = _claimed(transition, generation=1)
    observer = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=authorization_store.store,
    )
    executable = observer.observe_or_adopt(
        _observe(first_claim, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(executable) is EffectExecutable

    class _CrashAfterCommit:
        def __init__(self, delegate: Any) -> None:
            self.delegate = delegate
            self.commit_calls = 0

        def observe_transition_authorization(self, intent: Any) -> Any:
            return self.delegate.observe_transition_authorization(intent)

        def authorize_transition_effect(
            self,
            intent: Any,
            *,
            expected_observation_digest: str,
        ) -> Any:
            self.commit_calls += 1
            self.delegate.authorize_transition_effect(
                intent,
                expected_observation_digest=expected_observation_digest,
            )
            raise KeyboardInterrupt("response lost")

    authority = _CrashAfterCommit(authorization_store.store)
    executor = WorkflowTransitionSideEffectAuthorizationExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=authority,
    )
    first_applying = _applying(effect, generation=1)
    with pytest.raises(KeyboardInterrupt, match="response lost"):
        executor.execute(
            _attempt(first_claim, first_applying),
            executable=executable,
            heartbeat=_Heartbeat(),
        )
    assert authority.commit_calls == 1
    assert _receipt_count(authorization_store) == 1

    second_claim = _claimed(transition, generation=2)
    adopted = observer.observe_or_adopt(
        _observe(second_claim, first_applying),
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectAlreadyApplied
    assert authority.commit_calls == 1
    second_applying = _applying(effect, generation=2)
    durable_effect = _applied_effect(
        second_applying,
        adopted,
        generation=2,
        mode="adopt",
    )
    proof = assert_durable_workflow_transition_side_effect_authorization_proof(
        adopted.proof_payload,
        transition=second_claim,
        effect=durable_effect,
        reads=authorization_store.store,
    )
    assert proof.context.claim_generation == durable_effect.applied_generation == 2
    stale = proof.to_dict()
    stale["context"]["claim_generation"] = 1
    with pytest.raises(WorkflowTransitionEffectProofError):
        assert_durable_workflow_transition_side_effect_authorization_proof(
            stale,
            transition=second_claim,
            effect=durable_effect,
            reads=authorization_store.store,
        )


def test_normal_commit_response_loss_is_resolved_by_exact_reread(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="normal-response-loss-a")
    claimed = _claimed(transition, generation=1)
    executable = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=authorization_store.store,
    ).observe_or_adopt(
        _observe(claimed, effect),
        heartbeat=_Heartbeat(),
    )

    class _LostResponse:
        def __init__(self, delegate: Any) -> None:
            self.delegate = delegate
            self.calls = 0

        def observe_transition_authorization(self, intent: Any) -> Any:
            return self.delegate.observe_transition_authorization(intent)

        def authorize_transition_effect(
            self,
            intent: Any,
            *,
            expected_observation_digest: str,
        ) -> Any:
            self.calls += 1
            self.delegate.authorize_transition_effect(
                intent,
                expected_observation_digest=expected_observation_digest,
            )
            raise RuntimeError("response lost")

    authority = _LostResponse(authorization_store.store)
    result = WorkflowTransitionSideEffectAuthorizationExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=authority,
    ).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectApplied
    assert authority.calls == 1
    assert _receipt_count(authorization_store) == 1


def test_failed_operation_reauthorizes_with_distinct_attempt_domains_and_higher_fence(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="reauthorize-first", ownership_fencing_token=11)
    first_claim = _claimed(transition, generation=1)
    first_intent = _intent(first_claim, effect)
    first_observation = authorization_store.store.observe_transition_authorization(first_intent)
    first_receipt = authorization_store.store.authorize_transition_effect(
        first_intent,
        expected_observation_digest=first_observation.observation_digest,
    )
    first_adoption = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=authorization_store.store,
    ).observe_or_adopt(
        _observe(first_claim, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(first_adoption) is EffectAlreadyApplied
    provider_attempt = "provider-attempt-distinct-from-ownership"
    claimed = authorization_store.store.claim(
        first_intent.operation_id,
        expected_revision=2,
        fencing_token=11,
        attempt_id=provider_attempt,
    )
    assert claimed.record.attempt_id == provider_attempt
    assert provider_attempt != first_receipt.ownership_attempt_id
    failed = authorization_store.store.fail(
        first_intent.operation_id,
        expected_revision=3,
        fencing_token=11,
        attempt_id=provider_attempt,
        failure_code="provider_denied",
    )
    assert failed.status == "failed" and failed.revision == 4

    next_transition, next_effect = _plan(
        identity_key="reauthorize-second",
        ownership_attempt_id="ownership-attempt-b",
        ownership_fencing_token=12,
        authorization_envelope_id="envelope-b",
        authorization_envelope_digest="c" * 64,
    )
    next_intent = _intent(_claimed(next_transition, generation=1), next_effect)
    assert next_intent.operation_id == first_intent.operation_id
    assert next_intent.operation_intent_digest == first_intent.operation_intent_digest
    observation = authorization_store.store.observe_transition_authorization(next_intent)
    second_receipt = authorization_store.store.authorize_transition_effect(
        next_intent,
        expected_observation_digest=observation.observation_digest,
    )
    assert second_receipt.prior_status == "failed"
    assert second_receipt.prior_revision == 4
    assert second_receipt.authorized_ledger_revision == 5
    assert second_receipt.ownership_fencing_token == 12
    assert second_receipt.authorized_record.attempt_id == ""
    assert second_receipt.authorized_record.failure_code == ""
    assert _receipt_count(authorization_store) == 2
    first_applied = _applied_effect(
        _applying(effect, generation=1),
        first_adoption,
        generation=1,
        mode="adopt",
    )
    assert (
        assert_durable_workflow_transition_side_effect_authorization_proof(
            first_adoption.proof_payload,
            transition=first_claim,
            effect=first_applied,
            reads=authorization_store.store,
        ).resource_digest
        == first_receipt.receipt_digest
    )

    stale_transition, stale_effect = _plan(
        identity_key="reauthorize-stale",
        ownership_attempt_id="ownership-attempt-stale",
        ownership_fencing_token=11,
        authorization_envelope_id="envelope-stale",
        authorization_envelope_digest="d" * 64,
    )
    with pytest.raises(OptimisticConcurrencyError):
        authorization_store.store.observe_transition_authorization(
            _intent(_claimed(stale_transition, generation=1), stale_effect)
        )


@pytest.mark.parametrize(
    "terminal_status",
    ("started", "completed", "failed", "uncertain", "compensated"),
)
def test_historical_proof_survives_every_later_mutable_ledger_progression(
    authorization_store: _StoreCase,
    terminal_status: str,
) -> None:
    transition, effect = _plan(identity_key=f"progress-{terminal_status}")
    claimed_transition = _claimed(transition, generation=1)
    observer = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=authorization_store.store,
    )
    executable = observer.observe_or_adopt(
        _observe(claimed_transition, effect),
        heartbeat=_Heartbeat(),
    )
    applying = _applying(effect, generation=1)
    result = WorkflowTransitionSideEffectAuthorizationExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=authorization_store.store,
    ).execute(
        _attempt(claimed_transition, applying),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectApplied
    receipt = workflow_transition_side_effect_authorization_receipt_from_result(result.result_payload)
    applied_effect = _applied_effect(
        applying,
        result,
        generation=1,
        mode="execute",
    )
    attempt_id = f"provider-{terminal_status}"
    authorization_store.store.claim(
        receipt.operation_id,
        expected_revision=2,
        fencing_token=receipt.ownership_fencing_token,
        attempt_id=attempt_id,
    )
    if terminal_status == "completed":
        authorization_store.store.complete(
            receipt.operation_id,
            expected_revision=3,
            fencing_token=receipt.ownership_fencing_token,
            attempt_id=attempt_id,
            result_ref="artifact://result-a",
        )
    elif terminal_status == "failed":
        authorization_store.store.fail(
            receipt.operation_id,
            expected_revision=3,
            fencing_token=receipt.ownership_fencing_token,
            attempt_id=attempt_id,
            failure_code="provider_failed",
        )
    elif terminal_status == "uncertain":
        authorization_store.store.mark_uncertain(
            receipt.operation_id,
            expected_revision=3,
            fencing_token=receipt.ownership_fencing_token,
            attempt_id=attempt_id,
        )
    elif terminal_status == "compensated":
        authorization_store.store.complete(
            receipt.operation_id,
            expected_revision=3,
            fencing_token=receipt.ownership_fencing_token,
            attempt_id=attempt_id,
            result_ref="artifact://result-a",
        )
        authorization_store.store.compensate(
            receipt.operation_id,
            expected_revision=4,
            fencing_token=receipt.ownership_fencing_token,
            result_ref="artifact://compensation-a",
        )
    observed = authorization_store.store.observe_transition_authorization(_intent(claimed_transition, effect))
    assert observed.receipt == receipt
    assert observed.receipt.receipt_digest == receipt.receipt_digest
    assert observed.ledger_record.status == terminal_status
    assert (
        assert_durable_workflow_transition_side_effect_authorization_proof(
            result.proof_payload,
            transition=claimed_transition,
            effect=applied_effect,
            reads=authorization_store.store,
        ).resource_digest
        == receipt.receipt_digest
    )


def test_historical_receipt_survives_mutable_progression_and_ledger_cleanup(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="history-a")
    claimed_transition = _claimed(transition, generation=1)
    intent = _intent(claimed_transition, effect)
    before = authorization_store.store.observe_transition_authorization(intent)
    receipt = authorization_store.store.authorize_transition_effect(
        intent,
        expected_observation_digest=before.observation_digest,
    )
    provider_attempt = "provider-attempt-a"
    authorization_store.store.claim(
        intent.operation_id,
        expected_revision=2,
        fencing_token=intent.ownership_fencing_token,
        attempt_id=provider_attempt,
    )
    authorization_store.store.complete(
        intent.operation_id,
        expected_revision=3,
        fencing_token=intent.ownership_fencing_token,
        attempt_id=provider_attempt,
        result_ref="artifact://result-a",
    )
    progressed = authorization_store.store.observe_transition_authorization(intent)
    assert progressed.receipt == receipt
    assert progressed.ledger_record.status == "completed"
    assert progressed.ledger_record.revision == 4

    _delete_ledger_record(authorization_store, intent.operation_id)
    cleaned = authorization_store.store.observe_transition_authorization(intent)
    assert cleaned.receipt == receipt
    assert cleaned.ledger_record is None


def test_current_ledger_revision_regression_below_historical_receipt_fails_closed(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="ledger-regression-a")
    intent = _intent(_claimed(transition, generation=1), effect)
    before = authorization_store.store.observe_transition_authorization(intent)
    receipt = authorization_store.store.authorize_transition_effect(
        intent,
        expected_observation_digest=before.observation_digest,
    )
    regressed = replace(receipt.authorized_record, revision=1)
    if authorization_store.name == "memory":
        authorization_store.store._records[intent.operation_id] = regressed
    elif authorization_store.name == "sqlite":
        authorization_store.store._connection.execute(
            "UPDATE workflow_side_effect_ledger SET revision = ?, record_json = ? WHERE operation_id = ?",
            (1, canonical_json(regressed.to_dict()), intent.operation_id),
        )
    else:
        assert authorization_store.engine is not None
        with Session(authorization_store.engine) as session, session.begin():
            row = session.get(WorkflowSideEffectLedgerDB, intent.operation_id)
            row.revision = 1
            row.record = regressed.to_dict()
    with pytest.raises(OptimisticConcurrencyError, match="ledger_revision_regressed"):
        authorization_store.store.observe_transition_authorization(intent)


def test_legacy_authorized_without_receipt_quarantines(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="poison-a")
    claimed = _claimed(transition, generation=1)
    intent = _intent(claimed, effect)
    planned = authorization_store.store.plan(
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        declared_operation=intent.declared_operation,
        side_effect_class=intent.side_effect_class,
    )
    authorization_store.store.authorize(
        intent.operation_id,
        expected_revision=planned.revision,
        fencing_token=intent.ownership_fencing_token,
        authorization_envelope_id=intent.authorization_envelope_id,
    )
    observer = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=authorization_store.store,
    )
    assert (
        type(
            observer.observe_or_adopt(
                _observe(claimed, effect),
                heartbeat=_Heartbeat(),
            )
        )
        is EffectQuarantine
    )


@pytest.mark.parametrize("same_fence", (True, False))
def test_cross_transition_operation_replay_quarantines_without_mutation(
    authorization_store: _StoreCase,
    same_fence: bool,
) -> None:
    transition, effect = _plan(identity_key="cross-replay-source")
    claimed = _claimed(transition, generation=1)
    source_intent = _intent(claimed, effect)
    source_observation = authorization_store.store.observe_transition_authorization(source_intent)
    receipt = authorization_store.store.authorize_transition_effect(
        source_intent,
        expected_observation_digest=source_observation.observation_digest,
    )
    stored_before = authorization_store.store.get(
        tenant_id=source_intent.tenant_id,
        operation_id=source_intent.operation_id,
    )
    replay_transition, replay_effect = _plan(
        identity_key=f"cross-replay-target-{same_fence}",
        ownership_attempt_id=("ownership-attempt-a" if same_fence else "ownership-attempt-other"),
        ownership_fencing_token=(11 if same_fence else 12),
        authorization_envelope_id=("envelope-a" if same_fence else "envelope-other"),
        authorization_envelope_digest=("b" * 64 if same_fence else "c" * 64),
    )
    replay_claim = _claimed(replay_transition, generation=1)
    replay_intent = _intent(replay_claim, replay_effect)
    assert replay_intent.operation_id == source_intent.operation_id
    if same_fence:
        assert replay_intent.operation_fence_id == source_intent.operation_fence_id
        assert replay_intent.effect_id != source_intent.effect_id
    else:
        assert replay_intent.operation_fence_id != source_intent.operation_fence_id
    result = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=authorization_store.store,
    ).observe_or_adopt(
        _observe(replay_claim, replay_effect),
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectQuarantine
    assert _receipt_count(authorization_store) == 1
    assert authorization_store.store.observe_transition_authorization(source_intent).receipt == receipt
    assert (
        authorization_store.store.get(
            tenant_id=source_intent.tenant_id,
            operation_id=source_intent.operation_id,
        )
        == stored_before
    )


def test_active_proof_rejects_context_and_resource_replay(
    authorization_store: _StoreCase,
) -> None:
    transition, effect = _plan(identity_key="proof-replay-a")
    claimed = _claimed(transition, generation=1)
    observer = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=authorization_store.store,
    )
    executable = observer.observe_or_adopt(
        _observe(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    applied = WorkflowTransitionSideEffectAuthorizationExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=authorization_store.store,
    ).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied
    valid = WorkflowTransitionEffectResourceProof.from_mapping(applied.proof_payload)
    durable_effect = _applied_effect(
        _applying(effect, generation=1),
        applied,
        generation=1,
        mode="execute",
    )
    mutations = (
        ("transition_id", "transition-other"),
        ("effect_id", "effect-other"),
        ("runtime_id", TRANSITION_RUNTIME_LANGGRAPH),
        ("claim_generation", 2),
        ("transition_request_fingerprint", "f" * 64),
        ("effect_payload_digest", "e" * 64),
    )
    for name, value in mutations:
        raw = valid.to_dict()
        raw["context"][name] = value
        with pytest.raises(WorkflowTransitionEffectProofError):
            assert_active_workflow_transition_side_effect_authorization_proof(
                raw,
                transition=claimed,
                effect=_applying(effect, generation=1),
                claim_generation=1,
                reads=authorization_store.store,
            )
        with pytest.raises(WorkflowTransitionEffectProofError):
            assert_durable_workflow_transition_side_effect_authorization_proof(
                raw,
                transition=claimed,
                effect=durable_effect,
                reads=authorization_store.store,
            )
    for name, value in (("id", "receipt-other"), ("digest", "d" * 64)):
        raw = valid.to_dict()
        raw["resource"][name] = value
        with pytest.raises(WorkflowTransitionEffectProofError):
            assert_active_workflow_transition_side_effect_authorization_proof(
                raw,
                transition=claimed,
                effect=_applying(effect, generation=1),
                claim_generation=1,
                reads=authorization_store.store,
            )
        with pytest.raises(WorkflowTransitionEffectProofError):
            assert_durable_workflow_transition_side_effect_authorization_proof(
                raw,
                transition=claimed,
                effect=durable_effect,
                reads=authorization_store.store,
            )


@pytest.mark.parametrize(
    "field,value",
    (
        ("transition_id", "transition-other"),
        ("effect_id", "effect-other"),
        ("runtime_id", TRANSITION_RUNTIME_LANGGRAPH),
        ("operation_id", "operation-other"),
        ("operation_payload_digest", "d" * 64),
        ("authorization_envelope_digest", "e" * 64),
        ("ownership_attempt_id", "ownership-other"),
        ("ownership_fencing_token", 12),
        ("operation_fence_id", "fence-other"),
        ("receipt_id", "receipt-other"),
    ),
)
def test_staged_authorization_binding_tamper_quarantines_before_any_write(
    authorization_store: _StoreCase,
    field: str,
    value: object,
) -> None:
    transition, effect = _plan(identity_key=f"staged-tamper-{field}")
    raw = dict(effect.payload)
    raw[field] = value
    tampered = WorkflowTransitionEffect.build(
        transition_id=effect.transition_id,
        ordinal=effect.ordinal,
        kind=effect.kind,
        idempotency_key=effect.idempotency_key,
        payload=raw,
        created_at=effect.created_at,
    )
    claimed = _claimed(transition, generation=1)
    result = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=authorization_store.store,
    ).observe_or_adopt(
        _observe(claimed, tampered),
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectQuarantine
    assert _receipt_count(authorization_store) == 0


@pytest.mark.parametrize(
    "section,field,value",
    (
        ("context", "claim_generation", 2),
        ("context", "transition_request_fingerprint", "f" * 64),
        ("context", "effect_payload_digest", "e" * 64),
        ("resource", "id", "receipt-other"),
        ("head", "digest", "d" * 64),
        ("head", "revision", 7),
    ),
)
def test_executable_absence_proof_replay_quarantines_without_commit(
    authorization_store: _StoreCase,
    section: str,
    field: str,
    value: object,
) -> None:
    transition, effect = _plan(identity_key=f"absence-tamper-{section}-{field}")
    claimed = _claimed(transition, generation=1)
    executable = WorkflowTransitionSideEffectAuthorizationObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=authorization_store.store,
    ).observe_or_adopt(
        _observe(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(executable) is EffectExecutable
    raw = {key: dict(value) if hasattr(value, "items") else value for key, value in executable.proof_payload.items()}
    raw[section][field] = value
    result = WorkflowTransitionSideEffectAuthorizationExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=authorization_store.store,
    ).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=EffectExecutable(raw),
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectQuarantine
    assert _receipt_count(authorization_store) == 0


def test_memory_history_limit_filters_unrelated_receipts_before_bound() -> None:
    store = InMemorySideEffectLedger()
    transition, effect = _plan(identity_key="history-limit-source")
    intent = _intent(_claimed(transition, generation=1), effect)
    before = store.observe_transition_authorization(intent)
    receipt = store.authorize_transition_effect(
        intent,
        expected_observation_digest=before.observation_digest,
    )
    other_transition, other_effect = _plan(
        identity_key="history-limit-other",
        operation_payload_digest="d" * 64,
        step_id="step-b",
    )
    other_intent = _intent(_claimed(other_transition, generation=1), other_effect)
    assert other_intent.operation_id != intent.operation_id
    store._transition_authorization_receipts = {f"corrupt-unrelated-{index}": receipt for index in range(1_001)}
    assert store.observe_transition_authorization(other_intent).receipt is None
    store._transition_authorization_receipts = {f"corrupt-relevant-{index}": receipt for index in range(1_001)}
    with pytest.raises(OptimisticConcurrencyError, match="history_limit"):
        store.observe_transition_authorization(intent)


def test_sql_observation_is_one_statement_without_nullable_outer_join_lock(
    authorization_store: _StoreCase,
) -> None:
    if authorization_store.name != "sql":
        pytest.skip("SQLAlchemy-specific statement proof")
    assert authorization_store.engine is not None
    transition, effect = _plan(identity_key="sql-snapshot-a")
    intent = _intent(_claimed(transition, generation=1), effect)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(authorization_store.engine, "before_cursor_execute", capture)
    try:
        authorization_store.store.observe_transition_authorization(intent)
    finally:
        event.remove(authorization_store.engine, "before_cursor_execute", capture)
    relevant = [statement for statement in statements if "workflow_transition_side_effect_authorizations" in statement]
    assert len(relevant) == 1
    assert "LEFT OUTER JOIN" in relevant[0].upper()
    assert "FOR UPDATE" not in relevant[0].upper()


@pytest.mark.parametrize("tamper", ("ledger_json_projection", "receipt_json_projection"))
def test_persisted_json_projection_corruption_fails_closed_lsp(
    authorization_store: _StoreCase,
    tamper: str,
) -> None:
    if authorization_store.name == "memory":
        pytest.skip("raw persisted-row corruption")
    transition, effect = _plan(identity_key=f"projection-{tamper}")
    intent = _intent(_claimed(transition, generation=1), effect)
    before = authorization_store.store.observe_transition_authorization(intent)
    authorization_store.store.authorize_transition_effect(
        intent,
        expected_observation_digest=before.observation_digest,
    )
    if authorization_store.name == "sqlite":
        if tamper == "ledger_json_projection":
            row = authorization_store.store._connection.execute(
                "SELECT record_json FROM workflow_side_effect_ledger WHERE operation_id = ?",
                (intent.operation_id,),
            ).fetchone()
            raw = json.loads(row[0])
            raw["status"] = "failed"
            authorization_store.store._connection.execute(
                "UPDATE workflow_side_effect_ledger SET record_json = ? WHERE operation_id = ?",
                (canonical_json(raw), intent.operation_id),
            )
        else:
            authorization_store.store._connection.execute(
                "UPDATE workflow_transition_side_effect_authorizations "
                "SET runtime_id = 'langgraph' WHERE receipt_id = ?",
                (intent.receipt_id,),
            )
    else:
        assert authorization_store.engine is not None
        with Session(authorization_store.engine) as session, session.begin():
            if tamper == "ledger_json_projection":
                row = session.get(WorkflowSideEffectLedgerDB, intent.operation_id)
                raw = dict(row.record)
                raw["status"] = "failed"
                row.record = raw
            else:
                row = session.get(
                    WorkflowTransitionSideEffectAuthorizationDB,
                    intent.receipt_id,
                )
                row.runtime_id = TRANSITION_RUNTIME_LANGGRAPH
    with pytest.raises(
        OptimisticConcurrencyError,
        match=("projection_conflict" if tamper.endswith("projection") else "invalid"),
    ):
        authorization_store.store.observe_transition_authorization(intent)


def test_direct_sqlite_receipt_ddl_has_exact_unique_check_index_and_no_fk_contract(
    tmp_path: Path,
) -> None:
    path = tmp_path / "direct-ddl.sqlite"
    store = SQLiteSideEffectLedger(path)
    store.close()
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        inspector = sa.inspect(engine)
        table = "workflow_transition_side_effect_authorizations"
        assert {value["name"] for value in inspector.get_columns(table)} == {
            "receipt_id",
            "transition_id",
            "effect_id",
            "operation_id",
            "operation_fence_id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "runtime_id",
            "step_id",
            "operation_intent_digest",
            "authorization_envelope_id",
            "authorization_envelope_digest",
            "ownership_attempt_id",
            "ownership_fencing_token",
            "creator_claim_generation",
            "authorized_ledger_revision",
            "planned_at",
            "authorized_at",
            "receipt_digest",
            "receipt_json",
        }
        assert {tuple(value["column_names"]) for value in inspector.get_unique_constraints(table)} == {
            ("effect_id",),
            ("operation_fence_id",),
            ("operation_id", "authorized_ledger_revision"),
        }
        assert {value["name"]: tuple(value["column_names"]) for value in inspector.get_indexes(table)} == {
            "ix_transition_side_effect_auth_operation": ("operation_id",),
            "ix_transition_side_effect_auth_tenant_run": ("tenant_id", "run_id"),
            "ix_transition_side_effect_auth_transition": ("transition_id",),
        }
        checks = " ".join(str(value["sqltext"]) for value in inspector.get_check_constraints(table))
        assert "ownership_fencing_token > 0" in checks
        assert "creator_claim_generation > 0" in checks
        assert "authorized_ledger_revision > 1" in checks
        assert inspector.get_foreign_keys(table) == []
    finally:
        engine.dispose()


def test_effect_framework_remains_unwired_in_production() -> None:
    root = Path(__file__).resolve().parents[1]
    framework_files = {
        "db_models/workflow_runtime.py",
        "services/workflow_runtime/side_effects.py",
        "services/workflow_runtime/sqlalchemy_side_effects.py",
        "services/workflow_transition_side_effect_authorization.py",
    }
    offenders: list[str] = []
    for path in (root / "agent").rglob("*.py"):
        relative = str(path.relative_to(root / "agent"))
        if relative in framework_files:
            continue
        source = path.read_text(encoding="utf-8")
        if (
            "WorkflowTransitionSideEffectAuthorizationObserver" in source
            or "WorkflowTransitionSideEffectAuthorizationExecutor" in source
            or "workflow_transition_side_effect_authorization import" in source
        ):
            offenders.append(relative)
    assert offenders == []


def test_receipt_authorized_record_is_detached_from_caller_mapping() -> None:
    transition, effect = _plan(identity_key="receipt-alias-a")
    intent = _intent(_claimed(transition, generation=1), effect)
    store = InMemorySideEffectLedger()
    before = store.observe_transition_authorization(intent)
    receipt = store.authorize_transition_effect(
        intent,
        expected_observation_digest=before.observation_digest,
    )
    raw = receipt.to_dict()
    restored = WorkflowTransitionSideEffectAuthorizationReceipt.from_mapping(raw)
    raw_record = raw["authorized_record"]
    assert isinstance(raw_record, dict)
    raw_record["status"] = "failed"
    assert restored.authorized_record.status == "authorized"
    assert restored.receipt_digest == receipt.receipt_digest


@pytest.mark.parametrize(
    "field,value",
    (
        ("runtime_id", "r" * 65),
        ("side_effect_class", []),
        ("ownership_fencing_token", True),
        ("operation_payload_digest", "A" * 64),
        ("planned_at", float("nan")),
    ),
)
def test_builder_and_staged_parser_fail_closed_without_coercion(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "transition_id": "transition-a",
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "runtime_id": TRANSITION_RUNTIME_NATIVE,
        "ordinal": 1,
        "step_id": "step-a",
        "declared_operation": "provider.write:artifact-a",
        "side_effect_class": "idempotent_write",
        "operation_payload_digest": "a" * 64,
        "authorization_envelope_id": "envelope-a",
        "authorization_envelope_digest": "b" * 64,
        "ownership_attempt_id": "ownership-a",
        "ownership_fencing_token": 1,
        "planned_at": 1_000.0,
    }
    values[field] = value
    with pytest.raises(WorkflowTransitionSideEffectAuthorizationError):
        build_workflow_transition_side_effect_authorization_effect(**values)  # type: ignore[arg-type]


def test_external_operation_id_is_not_transition_scoped() -> None:
    _first_transition, first = _plan(identity_key="external-operation-a")
    _second_transition, second = _plan(
        identity_key="external-operation-b",
        ownership_attempt_id="ownership-b",
        ownership_fencing_token=12,
        authorization_envelope_id="envelope-b",
        authorization_envelope_digest="c" * 64,
    )
    assert first.payload["operation_id"] == second.payload["operation_id"]
    assert first.payload["operation_intent_digest"] == second.payload["operation_intent_digest"]
    assert first.payload["operation_fence_id"] != second.payload["operation_fence_id"]
    assert first.effect_id != second.effect_id
    assert canonical_json(first.to_dict()) != canonical_json(second.to_dict())
