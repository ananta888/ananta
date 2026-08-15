from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.workflow_runtime import (
    WorkflowRuntimeEventDB,
    WorkflowRuntimeOutboxDB,
)
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.events import (
    WORKFLOW_EVENT_COMMIT_INLINE,
    WORKFLOW_EVENT_COMMIT_OUTBOX,
    WORKFLOW_EVENT_TOPIC,
    CanonicalWorkflowEvent,
    InMemoryEventStore,
    WorkflowEventCommitProof,
    workflow_event_delivery_dedupe_key,
    workflow_event_outbox_id,
)
from agent.services.workflow_runtime.persistence import SQLiteEventStore
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyEventStore
from agent.services.workflow_runtime.telemetry import TelemetryEventStore
from agent.services.workflow_transition_effect_execution import (
    EffectAlreadyApplied,
    EffectApplied,
    EffectExecutable,
    EffectQuarantine,
    EffectRetry,
    WorkflowTransitionEffectAttempt,
    WorkflowTransitionEffectObservation,
)
from agent.services.workflow_transition_effect_proofs import WorkflowTransitionEffectProofError
from agent.services.workflow_transition_event_effect import (
    WORKFLOW_TRANSITION_EVENT_ACTOR,
    WorkflowTransitionEventEffectError,
    WorkflowTransitionEventEffectExecutor,
    WorkflowTransitionEventEffectObserver,
    assert_active_workflow_transition_event_proof,
    assert_durable_workflow_transition_event_proof,
    build_workflow_transition_event_effect,
    workflow_transition_event_effect_idempotency_key,
    workflow_transition_event_id,
    workflow_transition_event_observation_digest,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_EVENT_APPEND,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    TRANSITION_KIND_ADVANCE,
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_APPLYING,
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_id,
    workflow_transition_effect_result_digest,
    workflow_transition_effect_result_envelope,
    workflow_transition_id,
    workflow_transition_request_fingerprint,
)

_TABLES = [
    WorkflowRuntimeEventDB.__table__,
    WorkflowRuntimeOutboxDB.__table__,
]
_KNOWN_EVENT_EFFECT_BYTES = (
    '{"applied_generation":0,"created_at":1000.0,"effect_id":"wfx-deba5721350e94a5fb5823fcd8312dd5314'
    '1d6fb5bae082e44e1398c017bb490","idempotency_key":"wftei-10c6a8c2d413530e9ca4e5a5247f4d0bb72aa2ab'
    'f9078e83fdc3288b50c3ad72","kind":"event_append","ordinal":1,"payload":{"event":{"actor":"ananta-'
    'hub","attempt":0,"causation_id":"wfx-deba5721350e94a5fb5823fcd8312dd53141d6fb5bae082e44e1398c017'
    'bb490","correlation_id":"wft-0deac93d7d0f7454d0c6f1ff39833fa1902071530aa5a9e874745fdde392ed9a","'
    'dedupe_key":"wftei-10c6a8c2d413530e9ca4e5a5247f4d0bb72aa2abf9078e83fdc3288b50c3ad72","event_id":'
    '"wfte-0427a9ec36c89f3ad4eac3ddcaa2b3b80c0361d79f0b78dc7fbe30b56764d0e8","event_type":"workflow.s'
    'tep.completed","occurred_at":1000.0,"payload":{"artifact_ref":"artifact://result-a","nested":{"o'
    'k":true}},"run_id":"run-a","schema":"ananta.workflow_event.v1","sequence":0,"step_id":"step-a","'
    'tenant_id":"tenant-a","workflow_id":"workflow-a"},"event_content_digest":"8b8ae43690197ad2752863'
    '293b82057865f588d67b52b8c11cb6147df09b76bf","event_payload_digest":"ffb789187e2eca297d4104379eae'
    '98a4536015c2d46e200e9b69af654f320773","expected_sequence":0,"schema":"ananta.workflow_transition'
    '_event_effect.v1"},"payload_digest":"f57ffcff54d8ea148ffbf74e6b71c062916c804f3568a25d385f95ced73'
    '4d930","result_digest":"","result_payload":{},"revision":1,"schema":"ananta.workflow-transition-'
    'effect.v1","state":"planned","transition_id":"wft-0deac93d7d0f7454d0c6f1ff39833fa1902071530aa5a9'
    'e874745fdde392ed9a","updated_at":1000.0}'
)


@dataclass
class _StoreCase:
    name: str
    store: Any
    engine: Any | None = None


@pytest.fixture(params=("memory", "sqlite", "sql"))
def event_authority(request: pytest.FixtureRequest, tmp_path) -> _StoreCase:
    if request.param == "memory":
        return _StoreCase("memory", InMemoryEventStore())
    if request.param == "sqlite":
        store = SQLiteEventStore(tmp_path / "transition-events.sqlite")
        request.addfinalizer(store.close)
        return _StoreCase("sqlite", store)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    request.addfinalizer(engine.dispose)
    return _StoreCase("sql", SQLAlchemyEventStore(engine), engine)


class _Heartbeat:
    def __init__(self) -> None:
        self.calls = 0

    def heartbeat(self) -> None:
        self.calls += 1


def _plan(
    *,
    expected_sequence: int = 0,
    event_type: str = "workflow.step.completed",
    identity_key: str = "event-transition-a",
) -> tuple[WorkflowTransition, WorkflowTransitionEffect]:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_ADVANCE,
        identity_key=identity_key,
    )
    effect = build_workflow_transition_event_effect(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        ordinal=1,
        event_type=event_type,
        step_id="step-a",
        payload={"artifact_ref": "artifact://result-a", "nested": {"ok": True}},
        expected_sequence=expected_sequence,
        planned_at=1_000.0,
    )
    transition = WorkflowTransition.build(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_ADVANCE,
        request_payload={"request_id": identity_key},
        effects=(effect,),
        expected_revision=0,
        expected_checkpoint_ref="checkpoint-0",
        created_at=1_000.0,
    )
    return transition, effect


def _claimed(transition: WorkflowTransition, *, generation: int) -> WorkflowTransition:
    return replace(
        transition,
        state=TRANSITION_STATE_APPLYING,
        claim_owner=f"owner-{generation}",
        claim_generation=generation,
        attempt_count=generation,
        claim_expires_at=1_100.0 + generation,
        last_heartbeat_at=1_000.0 + generation,
        revision=transition.revision + generation,
        updated_at=1_000.0 + generation,
    )


def _applying(effect: WorkflowTransitionEffect, *, generation: int) -> WorkflowTransitionEffect:
    return replace(
        effect,
        state=EFFECT_STATE_APPLYING,
        applied_generation=generation,
        revision=effect.revision + 1,
        updated_at=effect.updated_at + generation,
    )


def _candidate(effect: WorkflowTransitionEffect) -> CanonicalWorkflowEvent:
    raw = thaw_json(effect.payload)["event"]
    return CanonicalWorkflowEvent.from_mapping(raw, validate=False)


def _unrelated_event(
    *,
    dedupe: str,
    event_id: str,
    run_id: str = "run-a",
    workflow_id: str = "workflow-a",
    occurred_at: float = 999.0,
) -> CanonicalWorkflowEvent:
    return CanonicalWorkflowEvent.build(
        tenant_id="tenant-a",
        workflow_id=workflow_id,
        run_id=run_id,
        event_type="workflow.step.started",
        correlation_id="correlation-other",
        causation_id="causation-other",
        dedupe_key=dedupe,
        step_id="step-other",
        actor="ananta-hub",
        payload={"other": True},
        occurred_at=occurred_at,
        event_id=event_id,
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


def test_event_effect_planning_is_acyclic_byte_deterministic_and_uses_no_clock_or_uuid() -> None:
    from agent.services.workflow_runtime import events as events_module

    with (
        patch.object(events_module.time, "time", side_effect=AssertionError("clock used")),
        patch.object(events_module.uuid, "uuid4", side_effect=AssertionError("uuid used")),
    ):
        transition, first = _plan()
        _transition_again, second = _plan()
    event = _candidate(first)

    expected_idempotency = workflow_transition_event_effect_idempotency_key(
        transition_id=transition.transition_id,
        ordinal=first.ordinal,
        event_type=event.event_type,
    )
    expected_effect_id = workflow_transition_effect_id(
        transition_id=transition.transition_id,
        ordinal=first.ordinal,
        kind=EFFECT_EVENT_APPEND,
        idempotency_key=expected_idempotency,
    )

    assert first.to_dict() == second.to_dict()
    assert transition.transition_id == "wft-0deac93d7d0f7454d0c6f1ff39833fa1902071530aa5a9e874745fdde392ed9a"
    assert expected_idempotency == "wftei-10c6a8c2d413530e9ca4e5a5247f4d0bb72aa2abf9078e83fdc3288b50c3ad72"
    assert expected_effect_id == "wfx-deba5721350e94a5fb5823fcd8312dd53141d6fb5bae082e44e1398c017bb490"
    assert first.idempotency_key == event.dedupe_key == expected_idempotency
    assert first.effect_id == expected_effect_id
    assert event.event_id == workflow_transition_event_id(
        transition_id=transition.transition_id,
        effect_id=expected_effect_id,
    )
    assert event.occurred_at == transition.created_at == first.created_at
    assert event.correlation_id == transition.transition_id
    assert event.causation_id == first.effect_id
    assert event.actor == WORKFLOW_TRANSITION_EVENT_ACTOR
    assert event.attempt == event.sequence == 0
    assert thaw_json(first.payload)["expected_sequence"] == 0
    assert event.event_id == "wfte-0427a9ec36c89f3ad4eac3ddcaa2b3b80c0361d79f0b78dc7fbe30b56764d0e8"
    sequenced = event.with_sequence(1)
    assert workflow_event_delivery_dedupe_key(sequenced) == (
        "run-a:wftei-10c6a8c2d413530e9ca4e5a5247f4d0bb72aa2abf9078e83fdc3288b50c3ad72"
    )
    assert workflow_event_outbox_id(sequenced) == (
        "wfro-4e314dedddb4a5e9395c5a65885bf709486a98d0378745bb5cc82cf0f00ad885"
    )
    assert canonical_json(first.to_dict()) == _KNOWN_EVENT_EFFECT_BYTES


@pytest.mark.parametrize(
    "payload",
    [
        {"secret": "do-not-stage"},
        {"value": float("nan")},
        {"value": "\ud800"},
        {"": "invalid-key"},
    ],
    ids=("sensitive", "nonfinite", "surrogate", "invalid-key"),
)
def test_event_effect_builder_rejects_unsafe_json_with_stable_error(payload: dict[str, Any]) -> None:
    transition, _effect = _plan()
    with pytest.raises(WorkflowTransitionEventEffectError):
        build_workflow_transition_event_effect(
            transition_id=transition.transition_id,
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            ordinal=1,
            event_type="workflow.step.completed",
            step_id="step-a",
            payload=payload,
            expected_sequence=0,
            planned_at=transition.created_at,
        )


def test_event_effect_builder_bounds_depth_items_and_cycles() -> None:
    transition, _effect = _plan()
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    deep: dict[str, Any] = {"leaf": True}
    for _ in range(40):
        deep = {"nested": deep}
    wide = {f"key-{index}": index for index in range(10_001)}

    for payload in (cyclic, deep, wide):
        with pytest.raises(WorkflowTransitionEventEffectError):
            build_workflow_transition_event_effect(
                transition_id=transition.transition_id,
                tenant_id=transition.tenant_id,
                workflow_id=transition.workflow_id,
                run_id=transition.run_id,
                ordinal=1,
                event_type="workflow.step.completed",
                step_id="step-a",
                payload=payload,
                expected_sequence=0,
                planned_at=transition.created_at,
            )


def test_event_effect_depth_boundary_is_identical_at_build_and_observe() -> None:
    transition, _effect = _plan()

    def nested(levels: int) -> dict[str, Any]:
        value: dict[str, Any] = {"leaf": True}
        for _ in range(levels):
            value = {"nested": value}
        return value

    accepted = build_workflow_transition_event_effect(
        transition_id=transition.transition_id,
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        ordinal=1,
        event_type="workflow.step.completed",
        step_id="step-a",
        payload=nested(29),
        expected_sequence=0,
        planned_at=transition.created_at,
    )
    accepted_transition = WorkflowTransition.build(
        transition_id=transition.transition_id,
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        runtime_id=transition.runtime_id,
        kind=transition.kind,
        request_payload={"request_id": "depth-boundary"},
        effects=(accepted,),
        expected_revision=transition.expected_revision,
        expected_checkpoint_ref=transition.expected_checkpoint_ref,
        created_at=transition.created_at,
    )
    result = WorkflowTransitionEventEffectObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=InMemoryEventStore(),
    ).observe_or_adopt(
        _observation(_claimed(accepted_transition, generation=1), accepted),
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectExecutable

    with pytest.raises(WorkflowTransitionEventEffectError):
        build_workflow_transition_event_effect(
            transition_id=transition.transition_id,
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            ordinal=1,
            event_type="workflow.step.completed",
            step_id="step-a",
            payload=nested(30),
            expected_sequence=0,
            planned_at=transition.created_at,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw["event"].update(sequence=1),
        lambda raw: raw["event"].update(sequence=True),
        lambda raw: raw["event"].update(attempt=1),
        lambda raw: raw["event"].update(attempt=True),
        lambda raw: raw["event"].update(occurred_at=1_001.0),
        lambda raw: raw["event"].update(occurred_at="1000"),
        lambda raw: raw.update(expected_sequence=True),
        lambda raw: raw["event"].update(actor="worker"),
        lambda raw: raw["event"].update(tenant_id="tenant-other"),
        lambda raw: raw["event"].update(correlation_id="other-transition"),
        lambda raw: raw["event"].update(causation_id="other-effect"),
        lambda raw: raw["event"].update(dedupe_key="stale-dedupe"),
        lambda raw: raw["event"].update(event_id="stale-event"),
        lambda raw: raw["event"].update(event_type="workflow.step.failed"),
        lambda raw: raw.update(extra=True),
        lambda raw: raw["event"].update(extra=True),
        lambda raw: raw["event"]["payload"].update(secret="unsafe"),
        lambda raw: raw.update(event_payload_digest="0" * 64),
        lambda raw: raw.update(event_content_digest="0" * 64),
    ],
    ids=(
        "sequence",
        "sequence-bool",
        "attempt",
        "attempt-bool",
        "occurred-at",
        "occurred-at-string",
        "expected-sequence-bool",
        "actor",
        "tenant",
        "correlation",
        "causation",
        "dedupe",
        "event-id",
        "event-type",
        "wrapper-extra",
        "event-extra",
        "redaction",
        "payload-digest",
        "content-digest",
    ),
)
def test_observer_rejects_noncanonical_staged_event_payload(mutation) -> None:
    transition, effect = _plan()
    raw = thaw_json(effect.payload)
    mutation(raw)
    mutated = WorkflowTransitionEffect.build(
        transition_id=effect.transition_id,
        ordinal=effect.ordinal,
        kind=effect.kind,
        idempotency_key=effect.idempotency_key,
        payload=raw,
        created_at=effect.created_at,
    )
    claimed = _claimed(transition, generation=1)
    result = WorkflowTransitionEventEffectObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=InMemoryEventStore(),
    ).observe_or_adopt(_observation(claimed, mutated), heartbeat=_Heartbeat())
    assert type(result) is EffectQuarantine


def test_atomic_event_observation_has_memory_sqlite_sql_identity_head_and_clone_parity(
    event_authority: _StoreCase,
) -> None:
    store = event_authority.store
    transition, effect = _plan()
    candidate = _candidate(effect)
    empty = store.observe_transition_event(
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        dedupe_key=candidate.dedupe_key,
        event_id=candidate.event_id,
    )
    assert empty.dedupe_event is empty.event_id_event is empty.head_event is None
    assert empty.dedupe_commit is empty.event_id_commit is None
    assert empty.head_sequence == 0
    assert workflow_transition_event_observation_digest(empty) != "0" * 64

    stored = store.append_transition_event(candidate, expected_sequence=0)
    observed = store.observe_transition_event(
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        dedupe_key=candidate.dedupe_key,
        event_id=candidate.event_id,
    )
    assert observed.dedupe_event == observed.event_id_event == observed.head_event == stored
    assert observed.dedupe_commit == observed.event_id_commit
    assert observed.dedupe_commit == WorkflowEventCommitProof.for_event(
        stored,
        delivery_mode=observed.delivery_mode,
    )
    expected_mode = WORKFLOW_EVENT_COMMIT_OUTBOX if event_authority.name == "sql" else WORKFLOW_EVENT_COMMIT_INLINE
    assert observed.delivery_mode == expected_mode

    assert observed.dedupe_event is not None
    observed.dedupe_event.payload["nested"]["ok"] = False
    reread = store.observe_transition_event(
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        dedupe_key=candidate.dedupe_key,
        event_id=candidate.event_id,
    )
    assert reread.dedupe_event is not None
    assert reread.dedupe_event.payload["nested"]["ok"] is True

    second = store.append(
        _unrelated_event(dedupe="other-dedupe", event_id="other-event", occurred_at=1_001.0),
        expected_sequence=1,
    )
    crossed = store.observe_transition_event(
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        dedupe_key=candidate.dedupe_key,
        event_id=second.event_id,
    )
    assert crossed.dedupe_event == stored
    assert crossed.event_id_event == crossed.head_event == second


def test_atomic_observation_rejects_a_mixed_workflow_stream(event_authority: _StoreCase) -> None:
    store = event_authority.store
    store.append(
        _unrelated_event(
            dedupe="foreign-dedupe",
            event_id="foreign-event",
            workflow_id="workflow-foreign",
        ),
        expected_sequence=0,
    )
    transition, effect = _plan()
    candidate = _candidate(effect)
    with pytest.raises(OptimisticConcurrencyError, match="observation_binding_conflict"):
        store.observe_transition_event(
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            dedupe_key=candidate.dedupe_key,
            event_id=candidate.event_id,
        )


def test_concurrent_identical_transition_append_converges_to_one_event_and_commit(
    event_authority: _StoreCase,
) -> None:
    _transition, effect = _plan()
    candidate = _candidate(effect)

    def append() -> CanonicalWorkflowEvent:
        return event_authority.store.append_transition_event(
            candidate,
            expected_sequence=0,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: append(), range(2)))

    assert results[0] == results[1]
    assert len(event_authority.store.list_events(tenant_id="tenant-a", run_id="run-a")) == 1
    if event_authority.name == "sql":
        assert len(event_authority.store.outbox.list_messages(tenant_id="tenant-a")) == 1


def test_observer_and_executor_apply_then_later_generation_adopts_exactly_once(
    event_authority: _StoreCase,
) -> None:
    transition, effect = _plan()
    claimed = _claimed(transition, generation=1)
    heartbeat = _Heartbeat()
    observer = WorkflowTransitionEventEffectObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=event_authority.store,
    )
    executable = observer.observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=heartbeat,
    )
    assert type(executable) is EffectExecutable
    assert heartbeat.calls == 0

    applying = _applying(effect, generation=1)
    executor = WorkflowTransitionEventEffectExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=event_authority.store,
    )
    applied = executor.execute(
        _attempt(claimed, applying),
        executable=executable,
        heartbeat=heartbeat,
    )
    assert type(applied) is EffectApplied
    assert heartbeat.calls == 0
    events = event_authority.store.list_events(tenant_id="tenant-a", run_id="run-a")
    assert len(events) == 1
    committed = event_authority.store.observe_transition_event(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        dedupe_key=events[0].dedupe_key,
        event_id=events[0].event_id,
    )
    assert committed.dedupe_commit is not None
    assert_active_workflow_transition_event_proof(
        applied.proof_payload,
        transition=claimed,
        effect=applying,
        claim_generation=1,
        event=events[0],
        commit=committed.dedupe_commit,
    )

    takeover = _claimed(transition, generation=2)
    adopted = observer.observe_or_adopt(
        _observation(takeover, applying),
        heartbeat=heartbeat,
    )
    assert type(adopted) is EffectAlreadyApplied
    assert len(event_authority.store.list_events(tenant_id="tenant-a", run_id="run-a")) == 1

    envelope = workflow_transition_effect_result_envelope(
        mode="execute",
        result_payload=applied.result_payload,
        proof_payload=applied.proof_payload,
        stage_attempt_count=1,
    )
    persisted_effect = replace(
        applying,
        state=EFFECT_STATE_APPLIED,
        result_payload=envelope,
        result_digest=workflow_transition_effect_result_digest(envelope),
        revision=applying.revision + 1,
        updated_at=1_002.0,
    )
    later = _claimed(transition, generation=3)
    assert_durable_workflow_transition_event_proof(
        applied.proof_payload,
        transition=later,
        effect=persisted_effect,
        event=events[0],
        commit=committed.dedupe_commit,
    )


def test_event_semantic_proof_rejects_resource_commit_and_context_replays() -> None:
    store = InMemoryEventStore()
    transition, effect = _plan()
    claimed = _claimed(transition, generation=1)
    executable = WorkflowTransitionEventEffectObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=store,
    ).observe_or_adopt(_observation(claimed, effect), heartbeat=_Heartbeat())
    assert type(executable) is EffectExecutable
    applying = _applying(effect, generation=1)
    applied = WorkflowTransitionEventEffectExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=store,
    ).execute(
        _attempt(claimed, applying),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied
    snapshot = store.observe_transition_event(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        dedupe_key=_candidate(applying).dedupe_key,
        event_id=_candidate(applying).event_id,
    )
    assert snapshot.dedupe_event is not None and snapshot.dedupe_commit is not None
    event = snapshot.dedupe_event
    commit = snapshot.dedupe_commit
    envelope = workflow_transition_effect_result_envelope(
        mode="execute",
        result_payload=applied.result_payload,
        proof_payload=applied.proof_payload,
        stage_attempt_count=1,
    )
    persisted = replace(
        applying,
        state=EFFECT_STATE_APPLIED,
        result_payload=envelope,
        result_digest=workflow_transition_effect_result_digest(envelope),
        revision=applying.revision + 1,
        updated_at=1_002.0,
    )
    later = _claimed(transition, generation=3)

    event_replays = (
        replace(event, sequence=event.sequence + 1),
        replace(event, event_id="different-event-id"),
        replace(event, payload={**event.payload, "artifact_ref": "artifact://other"}),
    )
    commit_replays = [
        replace(commit, commit_id="different-commit-id"),
        replace(commit, dedupe_key="run-a:different-dedupe"),
        replace(commit, created_at=commit.created_at + 1),
        replace(commit, payload_digest="0" * 64),
    ]
    wrong_topic = replace(commit)
    object.__setattr__(wrong_topic, "topic", "workflow.runtime.wrong")
    commit_replays.append(wrong_topic)

    for replayed_event in event_replays:
        with pytest.raises(Exception):
            assert_active_workflow_transition_event_proof(
                applied.proof_payload,
                transition=claimed,
                effect=applying,
                claim_generation=1,
                event=replayed_event,
                commit=commit,
            )
        with pytest.raises(Exception):
            assert_durable_workflow_transition_event_proof(
                applied.proof_payload,
                transition=later,
                effect=persisted,
                event=replayed_event,
                commit=commit,
            )
    for replayed_commit in commit_replays:
        with pytest.raises(Exception):
            assert_active_workflow_transition_event_proof(
                applied.proof_payload,
                transition=claimed,
                effect=applying,
                claim_generation=1,
                event=event,
                commit=replayed_commit,
            )
        with pytest.raises(Exception):
            assert_durable_workflow_transition_event_proof(
                applied.proof_payload,
                transition=later,
                effect=persisted,
                event=event,
                commit=replayed_commit,
            )

    divergent_request = {"request_id": "different-request"}
    context_replays = (
        replace(claimed, transition_id=f"wft-{'1' * 64}"),
        replace(claimed, runtime_id=TRANSITION_RUNTIME_LANGGRAPH),
        replace(
            claimed,
            request_payload=divergent_request,
            request_fingerprint=workflow_transition_request_fingerprint(divergent_request),
        ),
        _claimed(transition, generation=2),
    )
    for replayed_transition in context_replays:
        with pytest.raises(Exception):
            assert_active_workflow_transition_event_proof(
                applied.proof_payload,
                transition=replayed_transition,
                effect=applying,
                claim_generation=replayed_transition.claim_generation,
                event=event,
                commit=commit,
            )

    durable_context_replays = (
        replace(later, transition_id=f"wft-{'1' * 64}"),
        replace(later, runtime_id=TRANSITION_RUNTIME_LANGGRAPH),
        replace(
            later,
            request_payload=divergent_request,
            request_fingerprint=workflow_transition_request_fingerprint(divergent_request),
        ),
    )
    for replayed_transition in durable_context_replays:
        with pytest.raises(Exception):
            assert_durable_workflow_transition_event_proof(
                applied.proof_payload,
                transition=replayed_transition,
                effect=persisted,
                event=event,
                commit=commit,
            )

    wrong_effect = build_workflow_transition_event_effect(
        transition_id=transition.transition_id,
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        ordinal=2,
        event_type="workflow.step.completed",
        step_id="step-a",
        payload={"artifact_ref": "artifact://result-a"},
        expected_sequence=0,
        planned_at=transition.created_at,
    )
    wrong_applying = _applying(wrong_effect, generation=1)
    with pytest.raises(Exception):
        assert_active_workflow_transition_event_proof(
            applied.proof_payload,
            transition=claimed,
            effect=wrong_applying,
            claim_generation=1,
            event=event,
            commit=commit,
        )
    wrong_envelope = workflow_transition_effect_result_envelope(
        mode="execute",
        result_payload=applied.result_payload,
        proof_payload=applied.proof_payload,
        stage_attempt_count=1,
    )
    wrong_persisted = replace(
        wrong_applying,
        state=EFFECT_STATE_APPLIED,
        result_payload=wrong_envelope,
        result_digest=workflow_transition_effect_result_digest(wrong_envelope),
        revision=wrong_applying.revision + 1,
        updated_at=1_002.0,
    )
    with pytest.raises(Exception):
        assert_durable_workflow_transition_event_proof(
            applied.proof_payload,
            transition=later,
            effect=wrong_persisted,
            event=event,
            commit=commit,
        )


def test_observer_quarantines_permanent_head_or_identity_conflicts(event_authority: _StoreCase) -> None:
    transition, effect = _plan()
    claimed = _claimed(transition, generation=1)
    candidate = _candidate(effect)
    store = event_authority.store
    store.append(
        _unrelated_event(dedupe="head-winner", event_id="head-winner"),
        expected_sequence=0,
    )
    observer = WorkflowTransitionEventEffectObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=store,
    )
    head_result = observer.observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(head_result) is EffectQuarantine

    other_transition, other_effect = _plan(identity_key="identity-conflict")
    other_claimed = _claimed(other_transition, generation=1)
    other_candidate = _candidate(other_effect)
    conflicting = replace(
        other_candidate,
        event_id="event-id-with-same-dedupe",
    )
    store.append(conflicting, expected_sequence=1)
    identity_result = WorkflowTransitionEventEffectObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=store,
    ).observe_or_adopt(
        _observation(other_claimed, other_effect),
        heartbeat=_Heartbeat(),
    )
    assert type(identity_result) is EffectQuarantine
    assert candidate.dedupe_key != other_candidate.dedupe_key


@pytest.mark.parametrize("collision", ("dedupe", "event_id"))
def test_observer_quarantines_both_event_identity_collision_directions(
    event_authority: _StoreCase,
    collision: str,
) -> None:
    transition, effect = _plan()
    candidate = _candidate(effect)
    conflicting = (
        replace(candidate, event_id="same-dedupe-other-event")
        if collision == "dedupe"
        else replace(candidate, dedupe_key="same-event-other-dedupe")
    )
    event_authority.store.append(conflicting, expected_sequence=0)

    result = WorkflowTransitionEventEffectObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=event_authority.store,
    ).observe_or_adopt(
        _observation(_claimed(transition, generation=1), effect),
        heartbeat=_Heartbeat(),
    )

    assert type(result) is EffectQuarantine
    assert len(event_authority.store.list_events(tenant_id="tenant-a", run_id="run-a")) == 1


class _AuthorityWrapper:
    def __init__(self, store: Any, append) -> None:
        self.store = store
        self._append = append
        self.append_calls = 0

    def observe_transition_event(self, **kwargs: Any):
        return self.store.observe_transition_event(**kwargs)

    def append_transition_event(self, event: CanonicalWorkflowEvent, *, expected_sequence: int):
        self.append_calls += 1
        return self._append(event, expected_sequence)


def _executable_for(store: Any):
    transition, effect = _plan()
    claimed = _claimed(transition, generation=1)
    executable = WorkflowTransitionEventEffectObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=store,
    ).observe_or_adopt(_observation(claimed, effect), heartbeat=_Heartbeat())
    assert type(executable) is EffectExecutable
    applying = _applying(effect, generation=1)
    return claimed, applying, executable


def test_append_exception_is_retry_only_for_the_exact_unchanged_absence(
    event_authority: _StoreCase,
) -> None:
    claimed, applying, executable = _executable_for(event_authority.store)

    def fail_without_mutation(_event: CanonicalWorkflowEvent, _expected: int):
        raise RuntimeError("transport unavailable")

    executor = WorkflowTransitionEventEffectExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=_AuthorityWrapper(event_authority.store, fail_without_mutation),
    )
    result = executor.execute(
        _attempt(claimed, applying),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectRetry
    assert event_authority.store.list_events(tenant_id="tenant-a", run_id="run-a") == ()


def test_executor_rejects_tampered_active_absence_proof_without_append() -> None:
    store = InMemoryEventStore()
    claimed, applying, executable = _executable_for(store)
    tampered = thaw_json(executable.proof_payload)
    tampered["head"]["digest"] = "0" * 64

    result = WorkflowTransitionEventEffectExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=store,
    ).execute(
        _attempt(claimed, applying),
        executable=EffectExecutable(tampered),
        heartbeat=_Heartbeat(),
    )

    assert type(result) is EffectQuarantine
    assert store.list_events(tenant_id="tenant-a", run_id="run-a") == ()


def test_append_lost_response_is_adopted_but_success_without_commit_quarantines(
    event_authority: _StoreCase,
) -> None:
    claimed, applying, executable = _executable_for(event_authority.store)

    def commit_then_fail(event: CanonicalWorkflowEvent, expected: int):
        event_authority.store.append_transition_event(event, expected_sequence=expected)
        raise RuntimeError("response lost")

    adopted = WorkflowTransitionEventEffectExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=_AuthorityWrapper(event_authority.store, commit_then_fail),
    ).execute(
        _attempt(claimed, applying),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectApplied
    assert len(event_authority.store.list_events(tenant_id="tenant-a", run_id="run-a")) == 1

    second_case = InMemoryEventStore()
    claimed, applying, executable = _executable_for(second_case)

    def false_success(event: CanonicalWorkflowEvent, expected: int):
        return event.with_sequence(expected + 1)

    missing = WorkflowTransitionEventEffectExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=_AuthorityWrapper(second_case, false_success),
    ).execute(
        _attempt(claimed, applying),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(missing) is EffectQuarantine
    assert second_case.list_events(tenant_id="tenant-a", run_id="run-a") == ()


def test_committed_keyboard_interrupt_escapes_and_next_generation_adopts(
    event_authority: _StoreCase,
) -> None:
    claimed, applying, executable = _executable_for(event_authority.store)

    def commit_then_crash(event: CanonicalWorkflowEvent, expected: int):
        event_authority.store.append_transition_event(event, expected_sequence=expected)
        raise KeyboardInterrupt("hard crash")

    crash_authority = _AuthorityWrapper(event_authority.store, commit_then_crash)
    executor = WorkflowTransitionEventEffectExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=crash_authority,
    )
    with pytest.raises(KeyboardInterrupt, match="hard crash"):
        executor.execute(
            _attempt(claimed, applying),
            executable=executable,
            heartbeat=_Heartbeat(),
        )
    assert crash_authority.append_calls == 1

    transition, _effect = _plan()
    takeover = _claimed(transition, generation=2)
    adopted = WorkflowTransitionEventEffectObserver(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        reads=event_authority.store,
    ).observe_or_adopt(
        _observation(takeover, applying),
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectAlreadyApplied
    assert len(event_authority.store.list_events(tenant_id="tenant-a", run_id="run-a")) == 1
    if event_authority.name == "sql":
        assert len(event_authority.store.outbox.list_messages(tenant_id="tenant-a")) == 1

    adopted_applying = _applying(effect=applying, generation=2)
    adopted_envelope = workflow_transition_effect_result_envelope(
        mode="adopt",
        result_payload=adopted.result_payload,
        proof_payload=adopted.proof_payload,
        stage_attempt_count=2,
    )
    persisted = replace(
        adopted_applying,
        state=EFFECT_STATE_APPLIED,
        result_payload=adopted_envelope,
        result_digest=workflow_transition_effect_result_digest(adopted_envelope),
        revision=adopted_applying.revision + 1,
        updated_at=1_003.0,
    )
    snapshot = event_authority.store.observe_transition_event(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        dedupe_key=_candidate(applying).dedupe_key,
        event_id=_candidate(applying).event_id,
    )
    assert snapshot.dedupe_event is not None and snapshot.dedupe_commit is not None
    assert_durable_workflow_transition_event_proof(
        adopted.proof_payload,
        transition=takeover,
        effect=persisted,
        event=snapshot.dedupe_event,
        commit=snapshot.dedupe_commit,
    )
    stale = thaw_json(adopted.proof_payload)
    stale["context"]["claim_generation"] = 1
    with pytest.raises(WorkflowTransitionEffectProofError, match="binding_mismatch"):
        assert_durable_workflow_transition_event_proof(
            stale,
            transition=takeover,
            effect=persisted,
            event=snapshot.dedupe_event,
            commit=snapshot.dedupe_commit,
        )


def test_concurrent_different_head_after_append_failure_is_permanent_quarantine(
    event_authority: _StoreCase,
) -> None:
    claimed, applying, executable = _executable_for(event_authority.store)

    def drift_then_fail(_event: CanonicalWorkflowEvent, expected: int):
        event_authority.store.append(
            _unrelated_event(dedupe="racing-head", event_id="racing-head"),
            expected_sequence=expected,
        )
        raise OptimisticConcurrencyError("event_sequence_conflict")

    result = WorkflowTransitionEventEffectExecutor(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        authority=_AuthorityWrapper(event_authority.store, drift_then_fail),
    ).execute(
        _attempt(claimed, applying),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectQuarantine
    assert [event.dedupe_key for event in event_authority.store.list_events(tenant_id="tenant-a", run_id="run-a")] == [
        "racing-head"
    ]


@pytest.mark.parametrize("malformation", ("missing", "zero"))
def test_sqlite_and_sql_exact_observation_never_defaults_or_calls_clock(
    event_authority: _StoreCase,
    malformation: str,
) -> None:
    if event_authority.name == "memory":
        pytest.skip("persisted JSON hydration applies to SQLite and SQL")
    transition, effect = _plan()
    candidate = _candidate(effect)
    stored = event_authority.store.append_transition_event(candidate, expected_sequence=0)
    malformed = stored.to_dict()
    if malformation == "missing":
        malformed.pop("occurred_at")
    else:
        malformed["occurred_at"] = 0

    if event_authority.name == "sqlite":
        event_authority.store._connection.execute(  # noqa: SLF001 - corruption seam
            "UPDATE workflow_runtime_events SET event_json = ?",
            (canonical_json(malformed),),
        )
    else:
        with Session(event_authority.engine) as session:
            row = session.execute(sa.select(WorkflowRuntimeEventDB)).scalar_one()
            row.canonical_event = malformed
            session.commit()

    from agent.services.workflow_runtime import events as events_module

    with patch.object(events_module.time, "time", side_effect=AssertionError("clock used")):
        with pytest.raises(OptimisticConcurrencyError, match="record_invalid"):
            event_authority.store.observe_transition_event(
                tenant_id=transition.tenant_id,
                workflow_id=transition.workflow_id,
                run_id=transition.run_id,
                dedupe_key=candidate.dedupe_key,
                event_id=candidate.event_id,
            )


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("sequence", 99),
        ("content_hash", "0" * 64),
        ("event_type", "workflow.step.failed"),
        ("occurred_at", 2_000.0),
    ),
)
def test_sqlite_and_sql_exact_reads_bind_canonical_json_to_immutable_row_projection(
    event_authority: _StoreCase,
    column: str,
    value: Any,
) -> None:
    if event_authority.name == "memory":
        pytest.skip("Memory has no denormalized event row projection")
    if event_authority.name == "sqlite" and column == "event_type":
        pytest.skip("SQLite stores event_type only in canonical JSON")
    transition, effect = _plan()
    candidate = _candidate(effect)
    event_authority.store.append_transition_event(candidate, expected_sequence=0)
    if event_authority.name == "sqlite":
        event_authority.store._connection.execute(  # noqa: SLF001 - corruption seam
            f"UPDATE workflow_runtime_events SET {column} = ?",  # noqa: S608 - fixed test parametrization
            (value,),
        )
    else:
        with Session(event_authority.engine) as session:
            row = session.execute(sa.select(WorkflowRuntimeEventDB)).scalar_one()
            setattr(row, column, value)
            session.commit()

    with pytest.raises(OptimisticConcurrencyError, match="record_projection_conflict"):
        event_authority.store.get_by_dedupe(
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            dedupe_key=candidate.dedupe_key,
        )
    with pytest.raises(OptimisticConcurrencyError, match="record_projection_conflict"):
        event_authority.store.observe_transition_event(
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            dedupe_key=candidate.dedupe_key,
            event_id=candidate.event_id,
        )


def test_sql_commit_proof_survives_publisher_release_and_ack() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    try:
        store = SQLAlchemyEventStore(engine)
        transition, effect = _plan()
        candidate = _candidate(effect)
        store.append_transition_event(candidate, expected_sequence=0)
        before = store.observe_transition_event(
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            dedupe_key=candidate.dedupe_key,
            event_id=candidate.event_id,
        )
        claimed = store.outbox.claim_batch(
            tenant_id="tenant-a",
            consumer_id="publisher-a",
            now=1_000.0,
            lease_seconds=10,
        )[0]
        released = store.outbox.release(
            tenant_id="tenant-a",
            message_id=claimed.id,
            consumer_id="publisher-a",
            expected_revision=claimed.revision,
            retry_after_seconds=5,
            now=1_001.0,
        )
        assert released.available_at == 1_006.0
        after_release = store.observe_transition_event(
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            dedupe_key=candidate.dedupe_key,
            event_id=candidate.event_id,
        )
        assert after_release.dedupe_commit == before.dedupe_commit

        reclaimed = store.outbox.claim_batch(
            tenant_id="tenant-a",
            consumer_id="publisher-a",
            now=1_006.0,
            lease_seconds=10,
        )[0]
        store.outbox.acknowledge(
            tenant_id="tenant-a",
            message_id=reclaimed.id,
            consumer_id="publisher-a",
            expected_revision=reclaimed.revision,
            now=1_007.0,
        )
        after_ack = store.observe_transition_event(
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            dedupe_key=candidate.dedupe_key,
            event_id=candidate.event_id,
        )
        assert after_ack.dedupe_commit == before.dedupe_commit
    finally:
        engine.dispose()


def test_sql_observation_ignores_same_event_id_in_a_different_run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    try:
        store = SQLAlchemyEventStore(engine)
        first = _unrelated_event(dedupe="run-a-dedupe", event_id="shared-event-id")
        second = _unrelated_event(
            dedupe="run-b-dedupe",
            event_id="shared-event-id",
            run_id="run-b",
        )
        stored_first = store.append_transition_event(first, expected_sequence=0)
        store.append_transition_event(second, expected_sequence=0)
        observed = store.observe_transition_event(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            dedupe_key=first.dedupe_key,
            event_id=first.event_id,
        )
        assert observed.dedupe_event == observed.event_id_event == stored_first
        assert observed.dedupe_commit == observed.event_id_commit
    finally:
        engine.dispose()


def test_sql_missing_or_stray_canonical_outbox_quarantines_without_event_write() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    try:
        store = SQLAlchemyEventStore(engine)
        transition, effect = _plan()
        candidate = _candidate(effect)
        sequenced = candidate.with_sequence(1)
        with Session(engine) as session:
            session.add(
                WorkflowRuntimeOutboxDB(
                    id="stray-outbox",
                    tenant_id="tenant-a",
                    aggregate_id="run-wrong",
                    topic=WORKFLOW_EVENT_TOPIC,
                    dedupe_key=f"run-a:{candidate.dedupe_key}",
                    status="pending",
                    revision=1,
                    attempts=0,
                    available_at=1_000.0,
                    claimed_by="",
                    claim_expires_at=None,
                    created_at=1_000.0,
                    published_at=None,
                    payload=sequenced.to_dict(),
                )
            )
            session.commit()
        result = WorkflowTransitionEventEffectObserver(
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            reads=store,
        ).observe_or_adopt(
            _observation(_claimed(transition, generation=1), effect),
            heartbeat=_Heartbeat(),
        )
        assert type(result) is EffectQuarantine
        assert store.list_events(tenant_id="tenant-a", run_id="run-a") == ()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "corruption",
    ("missing", "id", "topic", "dedupe", "aggregate", "created_at", "payload"),
)
def test_sql_existing_event_requires_exact_immutable_canonical_outbox(corruption: str) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    try:
        store = SQLAlchemyEventStore(engine)
        transition, effect = _plan()
        candidate = _candidate(effect)
        store.append_transition_event(candidate, expected_sequence=0)
        with Session(engine) as session:
            row = session.execute(sa.select(WorkflowRuntimeOutboxDB)).scalar_one()
            if corruption == "missing":
                session.delete(row)
            elif corruption == "id":
                row.id = "wrong-commit-id"
            elif corruption == "topic":
                row.topic = "workflow.runtime.wrong"
            elif corruption == "dedupe":
                row.dedupe_key = "run-a:wrong-dedupe"
            elif corruption == "aggregate":
                row.aggregate_id = "run-wrong"
            elif corruption == "created_at":
                row.created_at += 1
            else:
                row.payload = {**dict(row.payload), "event_id": "wrong-event-id"}
            session.commit()

        result = WorkflowTransitionEventEffectObserver(
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            reads=store,
        ).observe_or_adopt(
            _observation(_claimed(transition, generation=1), effect),
            heartbeat=_Heartbeat(),
        )
        assert type(result) is EffectQuarantine
        assert len(store.list_events(tenant_id="tenant-a", run_id="run-a")) == 1
        assert len(store.outbox.list_messages(tenant_id="tenant-a")) in {0, 1}
    finally:
        engine.dispose()


def test_sql_unrelated_outbox_topic_does_not_conflict_with_canonical_commit() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    try:
        store = SQLAlchemyEventStore(engine)
        transition, effect = _plan()
        candidate = _candidate(effect)
        stored = store.append_transition_event(candidate, expected_sequence=0)
        with Session(engine) as session:
            session.add(
                WorkflowRuntimeOutboxDB(
                    id="unrelated-topic-row",
                    tenant_id="tenant-a",
                    aggregate_id="run-a",
                    topic="workflow.runtime.unrelated",
                    dedupe_key=f"run-a:{candidate.dedupe_key}",
                    status="pending",
                    revision=1,
                    attempts=0,
                    available_at=1_000.0,
                    claimed_by="",
                    claim_expires_at=None,
                    created_at=1_000.0,
                    published_at=None,
                    payload=stored.to_dict(),
                )
            )
            session.commit()
        result = WorkflowTransitionEventEffectObserver(
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            reads=store,
        ).observe_or_adopt(
            _observation(_claimed(transition, generation=1), effect),
            heartbeat=_Heartbeat(),
        )
        assert type(result) is EffectAlreadyApplied
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "store_kwargs",
    (
        {"publish_to_outbox": False},
        {"outbox_topic": "workflow.runtime.custom"},
    ),
)
def test_sql_transition_event_authority_rejects_noncanonical_outbox_configuration(store_kwargs) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    try:
        store = SQLAlchemyEventStore(engine, **store_kwargs)
        _transition, effect = _plan()
        candidate = _candidate(effect)
        with pytest.raises(OptimisticConcurrencyError, match="outbox_required"):
            store.append_transition_event(candidate, expected_sequence=0)
        with pytest.raises(OptimisticConcurrencyError, match="outbox_required"):
            store.observe_transition_event(
                tenant_id=candidate.tenant_id,
                workflow_id=candidate.workflow_id,
                run_id=candidate.run_id,
                dedupe_key=candidate.dedupe_key,
                event_id=candidate.event_id,
            )
        assert store.list_events(tenant_id="tenant-a", run_id="run-a") == ()
        assert store.outbox.list_messages(tenant_id="tenant-a") == ()
    finally:
        engine.dispose()


def test_telemetry_decorator_is_structurally_ineligible_and_exports_nothing() -> None:
    class _Exporter:
        def __init__(self) -> None:
            self.calls = 0

        def export(self, _event: Any, _context: Any) -> None:
            self.calls += 1

    exporter = _Exporter()
    decorated = TelemetryEventStore(
        InMemoryEventStore(),
        exporter,
        trace_context_factory=lambda _event: object(),
    )

    with pytest.raises(WorkflowTransitionEventEffectError, match="executor_invalid"):
        WorkflowTransitionEventEffectExecutor(
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            authority=decorated,  # type: ignore[arg-type]
        )
    with pytest.raises(WorkflowTransitionEventEffectError, match="observer_invalid"):
        WorkflowTransitionEventEffectObserver(
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            reads=decorated,  # type: ignore[arg-type]
        )
    assert exporter.calls == 0


def test_event_effect_is_reachable_only_through_the_cutover_composition() -> None:
    root = Path(__file__).resolve().parents[1]
    # The Native cutover composition is the single sanctioned consumer.  Any
    # other production reference would bypass the registry seam and the
    # planner, so the adapter would no longer be exactly attributable.
    allowed = {
        "workflow_transition_event_effect.py",
        "workflow_transition_native_composition.py",
    }
    offenders: list[str] = []
    for path in (root / "agent").rglob("*.py"):
        if path.name in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if (
            "workflow_transition_event_effect" in source
            or "WorkflowTransitionEventEffectObserver" in source
            or "WorkflowTransitionEventEffectExecutor" in source
        ):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
