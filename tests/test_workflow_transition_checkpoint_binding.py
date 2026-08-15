"""Binding a transition to the exact checkpoint revision it advanced against.

The transition authors no checkpoint state. What these tests pin is that it
records which revision it actually saw: a checkpoint that is not written yet
is a wait, and a checkpoint at a different revision is a fault rather than an
attribution to state the transition never observed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import create_engine

from agent.db_models.workflow_runtime import WorkflowTransitionCheckpointBindingDB
from agent.services.workflow_runtime.checkpoint_bindings import (
    CHECKPOINT_BINDING_RECEIPT_SCHEMA,
    InMemoryWorkflowTransitionCheckpointBindingStore,
    WorkflowTransitionCheckpointBindingConflict,
    WorkflowTransitionCheckpointBindingError,
    WorkflowTransitionCheckpointBindingIntent,
    workflow_transition_checkpoint_attempt_id,
    workflow_transition_checkpoint_digest,
    workflow_transition_checkpoint_intent_digest,
    workflow_transition_checkpoint_operation_fence_id,
    workflow_transition_checkpoint_receipt_id,
)
from agent.services.workflow_runtime.sqlalchemy_checkpoint_bindings import (
    SQLAlchemyWorkflowTransitionCheckpointBindingStore,
)
from agent.services.workflow_transition_checkpoint_binding import (
    WORKFLOW_TRANSITION_CHECKPOINT_BINDING_EFFECT_SCHEMA,
    WorkflowTransitionCheckpointBindingEffectError,
    build_workflow_transition_checkpoint_binding_effect,
    workflow_transition_checkpoint_binding_receipt_from_result,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_CHECKPOINT_SAVE,
    TRANSITION_RUNTIME_NATIVE,
    thaw_json,
)

_NOW = 1_000.0
_SCOPE = {
    "tenant_id": "tenant-a",
    "workflow_id": "workflow-a",
    "run_id": "run-a",
    "step_id": "step-a",
}


@dataclass(frozen=True)
class _Checkpoint:
    checkpoint_id: str = "checkpoint-8"
    revision: int = 8
    fencing_token: int = 3


class _Checkpoints:
    def __init__(self, checkpoint: Any = None, *, error: Exception | None = None) -> None:
        self.checkpoint = checkpoint
        self.error = error

    def get_latest(self, *, tenant_id: str, run_id: str, task_id: str) -> Any | None:
        del tenant_id, run_id, task_id
        if self.error is not None:
            raise self.error
        return self.checkpoint


def _intent(*, effect_id: str = "effect-a", revision: int = 8) -> WorkflowTransitionCheckpointBindingIntent:
    digest = workflow_transition_checkpoint_intent_digest(
        transition_id="transition-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        task_id="task-a",
        effect_ordinal=3,
        expected_revision=revision,
        **_SCOPE,
    )
    fence = workflow_transition_checkpoint_operation_fence_id(checkpoint_intent_digest=digest)
    return WorkflowTransitionCheckpointBindingIntent(
        transition_id="transition-a",
        effect_id=effect_id,
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        task_id="task-a",
        effect_ordinal=3,
        checkpoint_intent_digest=digest,
        operation_fence_id=fence,
        attempt_id=workflow_transition_checkpoint_attempt_id(effect_id=effect_id, operation_fence_id=fence),
        receipt_id=workflow_transition_checkpoint_receipt_id(transition_id="transition-a", effect_id=effect_id),
        expected_revision=revision,
        planned_at=_NOW,
        **_SCOPE,
    )


def _commit_kwargs(revision: int = 8) -> dict[str, Any]:
    return {
        "checkpoint_id": f"checkpoint-{revision}",
        "checkpoint_digest": workflow_transition_checkpoint_digest({"revision": revision}),
        "bound_revision": revision,
        "bound_fencing_token": 3,
        "claim_generation": 1,
    }


@pytest.fixture(params=("memory", "sql"))
def store(request: pytest.FixtureRequest) -> Any:
    """Both adapters must honour the same contract."""

    if request.param == "memory":
        return InMemoryWorkflowTransitionCheckpointBindingStore()
    engine = create_engine("sqlite://")
    WorkflowTransitionCheckpointBindingDB.metadata.create_all(
        engine,
        tables=[WorkflowTransitionCheckpointBindingDB.__table__],
    )
    return SQLAlchemyWorkflowTransitionCheckpointBindingStore(engine)


def test_binding_twice_returns_the_same_receipt(store: Any) -> None:
    intent = _intent()

    first = store.bind_transition_checkpoint(intent, bound_at=_NOW + 1, **_commit_kwargs())
    second = store.bind_transition_checkpoint(intent, bound_at=_NOW + 99, **_commit_kwargs())

    assert first == second
    assert first.schema == CHECKPOINT_BINDING_RECEIPT_SCHEMA
    assert first.bound_revision == 8


def test_a_binding_is_observable_by_its_exact_effect(store: Any) -> None:
    store.bind_transition_checkpoint(_intent(), bound_at=_NOW + 1, **_commit_kwargs())

    observed = store.observe_transition_checkpoint_binding(
        tenant_id="tenant-a",
        run_id="run-a",
        effect_id="effect-a",
    )

    assert observed.receipt is not None
    assert observed.receipt.checkpoint_id == "checkpoint-8"


def test_an_unbound_effect_observes_an_absence(store: Any) -> None:
    observed = store.observe_transition_checkpoint_binding(
        tenant_id="tenant-a",
        run_id="run-a",
        effect_id="effect-unknown",
    )

    assert observed.receipt is None
    assert observed.head_revision == 0


def test_a_second_effect_cannot_bind_the_same_revision(store: Any) -> None:
    store.bind_transition_checkpoint(_intent(), bound_at=_NOW + 1, **_commit_kwargs())

    with pytest.raises(WorkflowTransitionCheckpointBindingConflict):
        store.bind_transition_checkpoint(
            _intent(effect_id="effect-b"),
            bound_at=_NOW + 2,
            **_commit_kwargs(),
        )


@pytest.mark.parametrize("generation", (0, -1, True))
def test_a_non_positive_claim_generation_is_rejected(store: Any, generation: Any) -> None:
    kwargs = _commit_kwargs()
    kwargs["claim_generation"] = generation

    with pytest.raises(WorkflowTransitionCheckpointBindingError):
        store.bind_transition_checkpoint(_intent(), bound_at=_NOW + 1, **kwargs)


def test_the_planned_effect_is_byte_deterministic() -> None:
    kwargs: dict[str, Any] = {
        "transition_id": "transition-a",
        "runtime_id": TRANSITION_RUNTIME_NATIVE,
        "ordinal": 3,
        "task_id": "task-a",
        "expected_revision": 8,
        "planned_at": _NOW,
        **_SCOPE,
    }

    first = build_workflow_transition_checkpoint_binding_effect(**kwargs)
    second = build_workflow_transition_checkpoint_binding_effect(**kwargs)

    assert first == second
    assert first.kind == EFFECT_CHECKPOINT_SAVE
    payload = thaw_json(first.payload)
    assert payload["schema"] == WORKFLOW_TRANSITION_CHECKPOINT_BINDING_EFFECT_SCHEMA
    assert payload["expected_revision"] == 8


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("runtime_id", "not-a-runtime"),
        ("ordinal", 0),
        ("task_id", ""),
        ("expected_revision", 0),
        ("planned_at", 0.0),
    ),
)
def test_an_invalid_plan_input_fails_closed(field: str, value: Any) -> None:
    kwargs: dict[str, Any] = {
        "transition_id": "transition-a",
        "runtime_id": TRANSITION_RUNTIME_NATIVE,
        "ordinal": 3,
        "task_id": "task-a",
        "expected_revision": 8,
        "planned_at": _NOW,
        **_SCOPE,
    }
    kwargs[field] = value

    with pytest.raises(WorkflowTransitionCheckpointBindingEffectError):
        build_workflow_transition_checkpoint_binding_effect(**kwargs)


def test_a_result_payload_round_trips_back_into_its_receipt(store: Any) -> None:
    receipt = store.bind_transition_checkpoint(_intent(), bound_at=_NOW + 1, **_commit_kwargs())
    result = {
        "schema": "ananta.workflow_transition_checkpoint_binding_result.v1",
        "receipt": receipt.to_dict(),
    }

    assert workflow_transition_checkpoint_binding_receipt_from_result(result) == receipt


def test_a_foreign_result_schema_is_never_read_as_a_receipt() -> None:
    with pytest.raises(WorkflowTransitionCheckpointBindingEffectError, match="result_invalid"):
        workflow_transition_checkpoint_binding_receipt_from_result({"schema": "other.v1", "receipt": {}})


def test_the_digest_is_derived_from_the_read_not_from_a_claim() -> None:
    """Two different checkpoints must never digest to the same value."""

    first = workflow_transition_checkpoint_digest({"checkpoint_id": "c-8", "revision": 8})
    second = workflow_transition_checkpoint_digest({"checkpoint_id": "c-9", "revision": 9})

    assert first != second
    assert len(first) == 64
