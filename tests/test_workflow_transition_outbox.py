from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_QUEUE_RESERVE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_REJECTED,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_REJECTED,
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionError,
    WorkflowTransitionSnapshot,
    thaw_json,
    validate_transition_plan,
    workflow_transition_effect_fingerprint,
    workflow_transition_effect_result_digest,
    workflow_transition_id,
    workflow_transition_outcome_fingerprint,
    workflow_transition_request_fingerprint,
)

_CREATED_AT = 1_000.0


def _plan(
    *,
    request_payload: dict[str, object] | None = None,
    queue_payload: dict[str, object] | None = None,
) -> tuple[WorkflowTransition, tuple[WorkflowTransitionEffect, ...]]:
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
            payload=queue_payload or {"task_id": "task-a", "labels": ["hub"]},
            created_at=_CREATED_AT,
        ),
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=2,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key="workflow-a",
            payload={"workflow_id": "workflow-a"},
            created_at=_CREATED_AT,
        ),
    )
    transition = WorkflowTransition.build(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_COMMAND,
        command_id="command-a",
        receipt_id="command-a",
        admitted_command={"command_id": "command-a", "kind": "advance"},
        request_payload=request_payload or {"command": "advance", "metadata": {"labels": ["hub"]}},
        effects=effects,
        expected_revision=7,
        expected_checkpoint_ref="checkpoint-7",
        created_at=_CREATED_AT,
    )
    return transition, effects


def test_transition_contract_is_deeply_immutable_and_deterministic() -> None:
    request = {"command": "advance", "metadata": {"labels": ["hub"]}}
    effect_payload = {"task_id": "task-a", "labels": ["hub"]}
    transition, effects = _plan(
        request_payload=request,
        queue_payload=effect_payload,
    )

    request["metadata"]["labels"].append("mutated")  # type: ignore[index,union-attr]
    effect_payload["labels"].append("mutated")  # type: ignore[union-attr]

    assert thaw_json(transition.request_payload) == {
        "command": "advance",
        "metadata": {"labels": ["hub"]},
    }
    assert thaw_json(effects[0].payload) == {
        "task_id": "task-a",
        "labels": ["hub"],
    }
    with pytest.raises(TypeError):
        transition.request_payload["command"] = "pause"  # type: ignore[index]
    with pytest.raises(TypeError):
        effects[0].payload["task_id"] = "other"  # type: ignore[index]

    reordered_request = {
        "metadata": {"labels": ["hub"]},
        "command": "advance",
    }
    assert transition.request_fingerprint == workflow_transition_request_fingerprint(reordered_request)
    assert transition.effect_fingerprint == workflow_transition_effect_fingerprint(effects)
    assert transition.transition_id == workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_COMMAND,
        identity_key="command-a",
    )
    assert validate_transition_plan(transition, effects) == effects


def test_transition_contract_rejects_unbounded_open_or_inconsistent_values() -> None:
    transition, effects = _plan()

    with pytest.raises(WorkflowTransitionError, match="request_payload_invalid"):
        _plan(request_payload={"value": float("nan")})
    with pytest.raises(WorkflowTransitionError, match="request_payload_too_large"):
        _plan(request_payload={"value": "x" * 524_289})
    with pytest.raises(WorkflowTransitionError, match="effect_kind_invalid"):
        WorkflowTransitionEffect.build(
            transition_id=transition.transition_id,
            ordinal=1,
            kind="worker_orchestrate",
            idempotency_key="forbidden",
            payload={"task_id": "task-a"},
            created_at=_CREATED_AT,
        )
    with pytest.raises(WorkflowTransitionError, match="request_fingerprint_mismatch"):
        replace(transition, request_fingerprint="f" * 64)

    with pytest.raises(WorkflowTransitionError, match="effect_order_invalid"):
        validate_transition_plan(transition, tuple(reversed(effects)))

    no_finalize_transition = replace(
        transition,
        effect_fingerprint=workflow_transition_effect_fingerprint((effects[0],)),
    )
    with pytest.raises(
        WorkflowTransitionError,
        match="binding_finalize_effect_invalid",
    ):
        validate_transition_plan(no_finalize_transition, (effects[0],))


def test_outcome_fingerprint_binds_effect_results_status_and_checkpoint() -> None:
    transition, effects = _plan()
    result = {"task_id": "task-a", "queue_state": "reserved"}
    applied = replace(
        effects[0],
        state=EFFECT_STATE_APPLIED,
        applied_generation=3,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
        revision=2,
        updated_at=1_001.0,
    )
    status = {
        "status": "running",
        "revision": 8,
        "checkpoint_ref": "checkpoint-8",
    }
    receipt_status = {**status, "checkpoint_ref": "local:workflow-a:8"}
    first = workflow_transition_outcome_fingerprint(
        transition,
        (applied, effects[1]),
        binding_status=status,
        checkpoint_ref="checkpoint-8",
        receipt_result=receipt_status,
    )
    second = workflow_transition_outcome_fingerprint(
        transition,
        (applied, effects[1]),
        binding_status={**status, "status": "paused"},
        checkpoint_ref="checkpoint-8",
        receipt_result=receipt_status,
    )
    third = workflow_transition_outcome_fingerprint(
        transition,
        (applied, effects[1]),
        binding_status={**status, "checkpoint_ref": "checkpoint-9"},
        checkpoint_ref="checkpoint-9",
        receipt_result=receipt_status,
    )
    fourth = workflow_transition_outcome_fingerprint(
        transition,
        (applied, effects[1]),
        binding_status=status,
        checkpoint_ref="checkpoint-8",
        receipt_result={**receipt_status, "status": "paused"},
    )

    assert len(first) == 64
    assert len({first, second, third, fourth}) == 4
    with pytest.raises(WorkflowTransitionError, match="effects_incomplete"):
        workflow_transition_outcome_fingerprint(
            transition,
            effects,
            binding_status=status,
            checkpoint_ref="checkpoint-8",
            receipt_result=receipt_status,
        )


def test_rejected_snapshot_requires_every_effect_to_be_rejected() -> None:
    transition, effects = _plan()
    rejected_transition = replace(
        transition,
        state=TRANSITION_STATE_REJECTED,
        last_error="policy_rejected",
        revision=2,
        updated_at=1_001.0,
    )
    rejected_effects = tuple(
        replace(
            effect,
            state=EFFECT_STATE_REJECTED,
            revision=2,
            updated_at=1_001.0,
        )
        for effect in effects
    )

    assert WorkflowTransitionSnapshot(rejected_transition, rejected_effects).effects == rejected_effects

    result = {"task_id": "task-a"}
    ambiguously_applied = replace(
        effects[0],
        state=EFFECT_STATE_APPLIED,
        applied_generation=1,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
        revision=2,
        updated_at=1_001.0,
    )
    with pytest.raises(
        WorkflowTransitionError,
        match="rejection_effect_proof_missing",
    ):
        WorkflowTransitionSnapshot(
            rejected_transition,
            (ambiguously_applied, rejected_effects[1]),
        )
