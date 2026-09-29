"""Transition-owned reservation of exactly one task-queue slot.

The property that matters: an interrupted or retried transition converges on
one task. The reservation is derived from the transition, so a replan names
the same slot, and the authority's own constraints reject a second one.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from agent.db_models.workflow_runtime import WorkflowTransitionQueueReservationDB
from agent.services.workflow_runtime.queue_reservations import (
    QUEUE_RESERVATION_RECEIPT_SCHEMA,
    InMemoryWorkflowTransitionQueueReservationStore,
    WorkflowTransitionQueueReservationConflict,
    WorkflowTransitionQueueReservationError,
    WorkflowTransitionQueueReservationIntent,
    workflow_transition_queue_attempt_id,
    workflow_transition_queue_intent_digest,
    workflow_transition_queue_operation_fence_id,
    workflow_transition_queue_receipt_id,
)
from agent.services.workflow_runtime.sqlalchemy_queue_reservations import (
    SQLAlchemyWorkflowTransitionQueueReservationStore,
)
from agent.services.workflow_transition_native_composition import (
    workflow_transition_task_id,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_QUEUE_RESERVE,
    TRANSITION_RUNTIME_NATIVE,
    thaw_json,
)
from agent.services.workflow_transition_queue_reservation import (
    WORKFLOW_TRANSITION_QUEUE_RESERVATION_EFFECT_SCHEMA,
    WorkflowTransitionQueueReservationEffectError,
    build_workflow_transition_queue_reservation_effect,
    workflow_transition_queue_reservation_receipt_from_result,
)

_NOW = 1_000.0
_SCOPE = {
    "tenant_id": "tenant-a",
    "workflow_id": "workflow-a",
    "run_id": "run-a",
    "step_id": "step-a",
}


def _intent(*, effect_id: str = "effect-a", task_id: str = "task-a", ordinal: int = 2) -> Any:
    digest = workflow_transition_queue_intent_digest(
        transition_id="transition-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        task_id=task_id,
        effect_ordinal=ordinal,
        maximum_retries=3,
        **_SCOPE,
    )
    fence = workflow_transition_queue_operation_fence_id(queue_intent_digest=digest)
    return WorkflowTransitionQueueReservationIntent(
        transition_id="transition-a",
        effect_id=effect_id,
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        task_id=task_id,
        effect_ordinal=ordinal,
        queue_intent_digest=digest,
        operation_fence_id=fence,
        attempt_id=workflow_transition_queue_attempt_id(effect_id=effect_id, operation_fence_id=fence),
        receipt_id=workflow_transition_queue_receipt_id(transition_id="transition-a", effect_id=effect_id),
        maximum_retries=3,
        planned_at=_NOW,
        **_SCOPE,
    )


def _sql_store() -> SQLAlchemyWorkflowTransitionQueueReservationStore:
    engine = create_engine("sqlite://")
    WorkflowTransitionQueueReservationDB.metadata.create_all(
        engine,
        tables=[WorkflowTransitionQueueReservationDB.__table__],
    )
    return SQLAlchemyWorkflowTransitionQueueReservationStore(engine)


@pytest.fixture(params=("memory", "sql"))
def store(request: pytest.FixtureRequest) -> Any:
    """Both adapters must honour the same contract byte for byte."""

    if request.param == "memory":
        return InMemoryWorkflowTransitionQueueReservationStore()
    return _sql_store()


def test_reserving_twice_returns_the_same_receipt(store: Any) -> None:
    intent = _intent()

    first = store.reserve_transition_queue_slot(intent, claim_generation=1, reserved_at=_NOW + 1)
    second = store.reserve_transition_queue_slot(intent, claim_generation=1, reserved_at=_NOW + 99)

    assert first == second
    assert first.schema == QUEUE_RESERVATION_RECEIPT_SCHEMA
    assert len(first.receipt_digest) == 64


def test_a_reservation_is_observable_by_its_exact_effect(store: Any) -> None:
    intent = _intent()
    store.reserve_transition_queue_slot(intent, claim_generation=1, reserved_at=_NOW + 1)

    observed = store.observe_transition_queue_reservation(
        tenant_id="tenant-a",
        run_id="run-a",
        effect_id="effect-a",
    )

    assert observed.receipt is not None
    assert observed.receipt.task_id == "task-a"
    assert observed.head_revision >= 1


def test_an_unreserved_effect_observes_an_absence(store: Any) -> None:
    observed = store.observe_transition_queue_reservation(
        tenant_id="tenant-a",
        run_id="run-a",
        effect_id="effect-unknown",
    )

    assert observed.receipt is None
    assert observed.head_revision == 0


def test_a_second_effect_cannot_take_the_same_task(store: Any) -> None:
    """Two effects racing one task is the exact duplicate this prevents."""

    store.reserve_transition_queue_slot(_intent(), claim_generation=1, reserved_at=_NOW + 1)

    with pytest.raises(WorkflowTransitionQueueReservationConflict, match="task_conflict|conflict"):
        store.reserve_transition_queue_slot(
            _intent(effect_id="effect-b", ordinal=3),
            claim_generation=1,
            reserved_at=_NOW + 2,
        )


def test_a_replanned_effect_with_a_different_fence_is_rejected(store: Any) -> None:
    store.reserve_transition_queue_slot(_intent(), claim_generation=1, reserved_at=_NOW + 1)

    with pytest.raises(WorkflowTransitionQueueReservationConflict, match="fence_conflict"):
        store.reserve_transition_queue_slot(
            _intent(task_id="task-b"),
            claim_generation=1,
            reserved_at=_NOW + 2,
        )


def test_the_sql_table_rejects_a_duplicate_fence_at_the_database_level() -> None:
    """The constraint, not the application, is what a race has to get past."""

    engine = create_engine("sqlite://")
    WorkflowTransitionQueueReservationDB.metadata.create_all(
        engine,
        tables=[WorkflowTransitionQueueReservationDB.__table__],
    )
    store = SQLAlchemyWorkflowTransitionQueueReservationStore(engine)
    receipt = store.reserve_transition_queue_slot(_intent(), claim_generation=1, reserved_at=_NOW + 1)

    with engine.begin() as connection, pytest.raises(Exception):
        connection.execute(
            sa.insert(WorkflowTransitionQueueReservationDB).values(
                receipt_id="other-receipt",
                transition_id=receipt.transition_id,
                effect_id="effect-other",
                operation_fence_id=receipt.operation_fence_id,
                attempt_id="attempt-other",
                task_id="task-other",
                tenant_id=receipt.tenant_id,
                workflow_id=receipt.workflow_id,
                run_id=receipt.run_id,
                runtime_id=receipt.runtime_id,
                step_id=receipt.step_id,
                queue_intent_digest=receipt.queue_intent_digest,
                reservation_record_digest=receipt.reservation_record_digest,
                receipt_digest=receipt.receipt_digest,
                creator_claim_generation=1,
                reserved_revision=2,
                maximum_retries=3,
                retry_consumed=False,
                planned_at=_NOW,
                reserved_at=_NOW + 2,
                receipt={},
            )
        )


def test_the_planned_effect_is_byte_deterministic() -> None:
    first = build_workflow_transition_queue_reservation_effect(
        transition_id="transition-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        ordinal=2,
        task_id="task-a",
        maximum_retries=3,
        planned_at=_NOW,
        **_SCOPE,
    )
    second = build_workflow_transition_queue_reservation_effect(
        transition_id="transition-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        ordinal=2,
        task_id="task-a",
        maximum_retries=3,
        planned_at=_NOW,
        **_SCOPE,
    )

    assert first == second
    assert first.kind == EFFECT_QUEUE_RESERVE
    payload = thaw_json(first.payload)
    assert payload["schema"] == WORKFLOW_TRANSITION_QUEUE_RESERVATION_EFFECT_SCHEMA
    assert payload["task_id"] == "task-a"


def test_the_task_a_transition_may_reserve_is_derived_not_allocated() -> None:
    """A retry must name the same task, so the id comes from the transition."""

    assert workflow_transition_task_id(transition_id="t-1") == workflow_transition_task_id(transition_id="t-1")
    assert workflow_transition_task_id(transition_id="t-1") != workflow_transition_task_id(transition_id="t-2")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("runtime_id", "not-a-runtime"),
        ("ordinal", 0),
        ("task_id", ""),
        ("maximum_retries", -1),
        ("planned_at", 0.0),
    ),
)
def test_an_invalid_plan_input_fails_closed(field: str, value: Any) -> None:
    kwargs: dict[str, Any] = {
        "transition_id": "transition-a",
        "runtime_id": TRANSITION_RUNTIME_NATIVE,
        "ordinal": 2,
        "task_id": "task-a",
        "maximum_retries": 3,
        "planned_at": _NOW,
        **_SCOPE,
    }
    kwargs[field] = value

    with pytest.raises(WorkflowTransitionQueueReservationEffectError):
        build_workflow_transition_queue_reservation_effect(**kwargs)


def test_a_result_payload_round_trips_back_into_its_receipt(store: Any) -> None:
    receipt = store.reserve_transition_queue_slot(_intent(), claim_generation=1, reserved_at=_NOW + 1)
    result = {
        "schema": "ananta.workflow_transition_queue_reservation_result.v1",
        "receipt": receipt.to_dict(),
    }

    assert workflow_transition_queue_reservation_receipt_from_result(result) == receipt


def test_a_foreign_result_schema_is_never_read_as_a_receipt() -> None:
    with pytest.raises(WorkflowTransitionQueueReservationEffectError, match="result_invalid"):
        workflow_transition_queue_reservation_receipt_from_result({"schema": "something.else.v1", "receipt": {}})


@pytest.mark.parametrize("generation", (0, -1, True))
def test_a_non_positive_claim_generation_is_rejected(store: Any, generation: Any) -> None:
    with pytest.raises(WorkflowTransitionQueueReservationError):
        store.reserve_transition_queue_slot(_intent(), claim_generation=generation, reserved_at=_NOW + 1)
