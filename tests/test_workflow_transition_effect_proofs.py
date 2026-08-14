from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from agent.services.workflow_transition_effect_proofs import (
    WORKFLOW_TRANSITION_EFFECT_PROOF_CONTEXT_SCHEMA,
    WORKFLOW_TRANSITION_EFFECT_RESOURCE_PROOF_SCHEMA,
    WorkflowTransitionEffectProofContext,
    WorkflowTransitionEffectProofError,
    WorkflowTransitionEffectResourceProof,
    assert_active_workflow_transition_effect_proof_binding,
    assert_durable_workflow_transition_effect_proof_binding,
    workflow_transition_effect_resource_digest,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_CHECKPOINT_SAVE,
    EFFECT_EVENT_APPEND,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_REJECTED,
    TRANSITION_KIND_ADVANCE,
    TRANSITION_KIND_START,
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    WorkflowTransition,
    WorkflowTransitionEffect,
    workflow_transition_effect_result_digest,
    workflow_transition_effect_result_envelope,
    workflow_transition_id,
    workflow_transition_request_fingerprint,
)

_CREATED_AT = 1_000.0


def _claimed_effect(
    *,
    generation: int = 3,
) -> tuple[WorkflowTransition, WorkflowTransitionEffect]:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_ADVANCE,
        identity_key="advance-a",
    )
    effects = (
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=1,
            kind=EFFECT_EVENT_APPEND,
            idempotency_key="event:run-a:advance-a",
            payload={"event_id": "event-a", "occurred_at": _CREATED_AT},
            created_at=_CREATED_AT,
        ),
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=2,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key="binding:workflow-a",
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
        kind=TRANSITION_KIND_ADVANCE,
        request_payload={"operation": "advance"},
        effects=effects,
        expected_revision=4,
        expected_checkpoint_ref="checkpoint-4",
        created_at=_CREATED_AT,
    )
    return (
        replace(
            transition,
            state=TRANSITION_STATE_APPLYING,
            claim_owner="runner-a",
            claim_generation=generation,
            claim_expires_at=1_100.0,
            last_heartbeat_at=1_001.0,
            attempt_count=generation,
            revision=transition.revision + 1,
            updated_at=1_001.0,
        ),
        effects[0],
    )


def _proof(
    *,
    generation: int = 3,
) -> tuple[
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionEffectResourceProof,
    dict[str, object],
]:
    transition, effect = _claimed_effect(generation=generation)
    resource = {
        "schema": "ananta.workflow_event.v1",
        "event_id": "event-a",
        "sequence": 7,
    }
    context = WorkflowTransitionEffectProofContext.from_active_claim(
        transition=transition,
        effect=effect,
        claim_generation=generation,
    )
    proof = WorkflowTransitionEffectResourceProof(
        context=context,
        resource_kind="workflow_event",
        resource_id="event-a",
        resource_revision=7,
        resource_digest=workflow_transition_effect_resource_digest(resource),
    )
    return transition, effect, proof, resource


def _applied_effect(
    effect: WorkflowTransitionEffect,
    proof: WorkflowTransitionEffectResourceProof,
    *,
    generation: int,
) -> WorkflowTransitionEffect:
    result = workflow_transition_effect_result_envelope(
        mode="execute",
        result_payload={"event_id": proof.resource_id},
        proof_payload=proof.to_dict(),
        stage_attempt_count=generation,
    )
    return replace(
        effect,
        state=EFFECT_STATE_APPLIED,
        applied_generation=generation,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
        revision=effect.revision + 1,
        updated_at=1_001.0,
    )


def test_proof_context_is_versioned_frozen_bounded_and_payload_derived() -> None:
    transition, effect, proof, _resource = _proof()

    assert proof.schema == WORKFLOW_TRANSITION_EFFECT_RESOURCE_PROOF_SCHEMA
    assert proof.context.schema == WORKFLOW_TRANSITION_EFFECT_PROOF_CONTEXT_SCHEMA
    assert proof.context.transition_id == transition.transition_id
    assert proof.context.effect_id == effect.effect_id
    assert proof.context.transition_kind == transition.kind
    assert proof.context.transition_request_fingerprint == transition.request_fingerprint
    assert proof.context.effect_ordinal == effect.ordinal
    assert proof.context.effect_payload_digest == effect.payload_digest
    assert proof.context.idempotency_key == effect.idempotency_key
    assert proof.context.claim_generation == transition.claim_generation
    with pytest.raises(FrozenInstanceError):
        proof.resource_revision = 8  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        proof.context.run_id = "run-other"  # type: ignore[misc]


def test_proof_round_trip_copies_nested_mappings_and_exact_binding_accepts() -> None:
    transition, effect, proof, resource = _proof()
    raw = proof.to_dict()
    restored = WorkflowTransitionEffectResourceProof.from_mapping(raw)

    raw["context"]["tenant_id"] = "tenant-mutated"
    raw["resource"]["id"] = "event-mutated"
    assert restored == proof
    assert (
        assert_active_workflow_transition_effect_proof_binding(
            restored,
            transition=transition,
            effect=effect,
            claim_generation=3,
            resource_kind="workflow_event",
            resource_id="event-a",
            resource_revision=7,
            resource_digest=workflow_transition_effect_resource_digest(resource),
        )
        is restored
    )


def test_durable_proof_uses_applied_generation_after_later_claims_and_completion() -> None:
    transition, effect, proof, resource = _proof(generation=1)
    applied = _applied_effect(effect, proof, generation=1)
    completed_later = replace(
        transition,
        state=TRANSITION_STATE_COMPLETED,
        result_status={"revision": 5, "checkpoint_ref": "checkpoint-5"},
        result_checkpoint_ref="checkpoint-5",
        outcome_fingerprint="f" * 64,
        claim_owner="",
        claim_generation=3,
        claim_expires_at=0.0,
        attempt_count=3,
        revision=transition.revision + 2,
        updated_at=1_010.0,
        completed_at=1_010.0,
    )

    assert (
        assert_durable_workflow_transition_effect_proof_binding(
            proof,
            transition=completed_later,
            effect=applied,
            resource_kind="workflow_event",
            resource_id="event-a",
            resource_revision=7,
            resource_digest=workflow_transition_effect_resource_digest(resource),
        )
        is proof
    )
    replay = replace(proof, context=replace(proof.context, claim_generation=3))
    with pytest.raises(
        WorkflowTransitionEffectProofError,
        match="durable_proof_binding_mismatch",
    ):
        assert_durable_workflow_transition_effect_proof_binding(
            replay,
            transition=completed_later,
            effect=applied,
            resource_kind="workflow_event",
            resource_id="event-a",
            resource_revision=7,
            resource_digest=workflow_transition_effect_resource_digest(resource),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("transition_id", "wft-other"),
        ("effect_id", "wfx-other"),
        ("effect_kind", EFFECT_CHECKPOINT_SAVE),
        ("runtime_id", TRANSITION_RUNTIME_LANGGRAPH),
        ("tenant_id", "tenant-other"),
        ("workflow_id", "workflow-other"),
        ("run_id", "run-other"),
        ("transition_kind", TRANSITION_KIND_START),
        ("transition_request_fingerprint", "a" * 64),
        ("effect_ordinal", 2),
        ("effect_payload_digest", "b" * 64),
        ("idempotency_key", "event:other"),
        ("claim_generation", 4),
    ),
)
def test_cross_context_replay_fails_closed(field_name: str, value: object) -> None:
    transition, effect, proof, resource = _proof()
    replay = replace(
        proof,
        context=replace(proof.context, **{field_name: value}),
    )

    with pytest.raises(WorkflowTransitionEffectProofError, match="proof_binding_mismatch"):
        assert_active_workflow_transition_effect_proof_binding(
            replay,
            transition=transition,
            effect=effect,
            claim_generation=3,
            resource_kind="workflow_event",
            resource_id="event-a",
            resource_revision=7,
            resource_digest=workflow_transition_effect_resource_digest(resource),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("resource_kind", "checkpoint"),
        ("resource_id", "event-other"),
        ("resource_revision", 8),
        ("resource_digest", "c" * 64),
    ),
)
def test_cross_resource_replay_fails_closed(field_name: str, value: object) -> None:
    transition, effect, proof, resource = _proof()
    replay = replace(proof, **{field_name: value})

    with pytest.raises(WorkflowTransitionEffectProofError, match="proof_resource_mismatch"):
        assert_active_workflow_transition_effect_proof_binding(
            replay,
            transition=transition,
            effect=effect,
            claim_generation=3,
            resource_kind="workflow_event",
            resource_id="event-a",
            resource_revision=7,
            resource_digest=workflow_transition_effect_resource_digest(resource),
        )


@pytest.mark.parametrize("generation", (0, True, None, "3", 3.0, 2, 4))
def test_context_rejects_nonpositive_boolean_or_stale_generation(
    generation: object,
) -> None:
    transition, effect = _claimed_effect()
    with pytest.raises(
        WorkflowTransitionEffectProofError,
        match="active_proof_context_invalid|claim_generation_invalid",
    ):
        WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=generation,  # type: ignore[arg-type]
        )


def test_context_rejects_final_effect_and_non_applying_transition() -> None:
    transition, _effect = _claimed_effect()
    final_effect = WorkflowTransitionEffect.build(
        transition_id=transition.transition_id,
        ordinal=2,
        kind=EFFECT_BINDING_FINALIZE,
        idempotency_key="binding:workflow-a",
        payload={"workflow_id": "workflow-a"},
        created_at=_CREATED_AT,
    )
    with pytest.raises(WorkflowTransitionEffectProofError, match="active_proof_context_invalid"):
        WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=final_effect,
            claim_generation=3,
        )
    with pytest.raises(WorkflowTransitionEffectProofError, match="active_proof_context_invalid"):
        WorkflowTransitionEffectProofContext.from_active_claim(
            transition=replace(
                transition,
                state="ready",
                claim_owner="",
                claim_expires_at=0.0,
            ),
            effect=_claimed_effect()[1],
            claim_generation=3,
        )


def test_active_context_accepts_only_planned_or_generation_bounded_applying() -> None:
    transition, effect, proof, _resource = _proof()
    older_applying = replace(
        effect,
        state=EFFECT_STATE_APPLYING,
        applied_generation=2,
        revision=effect.revision + 1,
        updated_at=1_001.0,
    )
    assert (
        WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=older_applying,
            claim_generation=3,
        ).claim_generation
        == 3
    )

    applied = _applied_effect(effect, proof, generation=3)
    rejected = replace(
        effect,
        state=EFFECT_STATE_REJECTED,
        revision=effect.revision + 1,
        updated_at=1_001.0,
    )
    future_applying = replace(older_applying, applied_generation=4)
    for invalid in (applied, rejected, future_applying):
        with pytest.raises(
            WorkflowTransitionEffectProofError,
            match="active_proof_context_invalid",
        ):
            WorkflowTransitionEffectProofContext.from_active_claim(
                transition=transition,
                effect=invalid,
                claim_generation=3,
            )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: {**value, "unknown": True},
        lambda value: {**value, "schema": "unsupported"},
        lambda value: {**value, "claim_generation": True},
        lambda value: {
            **value,
            "transition_request_fingerprint": "not-a-digest",
        },
        lambda value: {**value, "effect_payload_digest": "not-a-digest"},
        lambda value: {**value, "idempotency_key": "invalid-\ud800"},
    ),
)
def test_context_mapping_is_strict_and_does_not_coerce(mutation) -> None:
    _transition, _effect, proof, _resource = _proof()
    with pytest.raises(WorkflowTransitionEffectProofError):
        WorkflowTransitionEffectProofContext.from_mapping(mutation(proof.context.to_dict()))


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    tuple(
        (field_name, invalid)
        for field_name in ("runtime_id", "transition_kind", "effect_kind")
        for invalid in (True, [], {})
    ),
)
def test_context_mapping_rejects_unhashable_enum_values_with_stable_error(
    field_name: str,
    invalid: object,
) -> None:
    _transition, _effect, proof, _resource = _proof()
    raw = proof.context.to_dict()
    raw[field_name] = invalid

    with pytest.raises(WorkflowTransitionEffectProofError):
        WorkflowTransitionEffectProofContext.from_mapping(raw)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: {**value, "unknown": True},
        lambda value: {**value, "schema": "unsupported"},
        lambda value: {**value, "context": None},
        lambda value: {
            **value,
            "resource": {**value["resource"], "unknown": True},
        },
        lambda value: {
            **value,
            "resource": {**value["resource"], "revision": False},
        },
        lambda value: {
            **value,
            "resource": {**value["resource"], "id": "invalid-\ud800"},
        },
    ),
)
def test_resource_proof_mapping_is_strict_and_does_not_coerce(mutation) -> None:
    _transition, _effect, proof, _resource = _proof()
    with pytest.raises(WorkflowTransitionEffectProofError):
        WorkflowTransitionEffectResourceProof.from_mapping(mutation(proof.to_dict()))


def test_resource_digest_is_canonical_bounded_and_rejects_nonfinite_values() -> None:
    assert workflow_transition_effect_resource_digest(
        {"b": [2, 3], "a": {"value": 1.5}}
    ) == workflow_transition_effect_resource_digest({"a": {"value": 1.5}, "b": (2, 3)})
    for invalid in (
        {},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": object()},
        {"value": "x" * 524_289},
    ):
        with pytest.raises(WorkflowTransitionEffectProofError):
            workflow_transition_effect_resource_digest(invalid)


def test_resource_digest_enforces_one_aggregate_node_budget() -> None:
    aggregate_overflow = {f"branch-{branch}": list(range(100)) for branch in range(100)}

    with pytest.raises(
        WorkflowTransitionEffectProofError,
        match="resource_payload_invalid",
    ):
        workflow_transition_effect_resource_digest(aggregate_overflow)


def test_transition_request_and_effect_payload_have_distinct_replay_fences() -> None:
    transition, effect, proof, resource = _proof()
    changed_request = {"operation": "advance", "input": "different"}
    divergent_transition = replace(
        transition,
        request_payload=changed_request,
        request_fingerprint=workflow_transition_request_fingerprint(changed_request),
    )

    with pytest.raises(
        WorkflowTransitionEffectProofError,
        match="active_proof_binding_mismatch",
    ):
        assert_active_workflow_transition_effect_proof_binding(
            proof,
            transition=divergent_transition,
            effect=effect,
            claim_generation=3,
            resource_kind="workflow_event",
            resource_id="event-a",
            resource_revision=7,
            resource_digest=workflow_transition_effect_resource_digest(resource),
        )

    divergent_effect = WorkflowTransitionEffect.build(
        transition_id=transition.transition_id,
        ordinal=effect.ordinal,
        kind=effect.kind,
        idempotency_key=effect.idempotency_key,
        payload={"event_id": "event-a", "occurred_at": _CREATED_AT + 1},
        created_at=_CREATED_AT,
    )
    with pytest.raises(
        WorkflowTransitionEffectProofError,
        match="active_proof_binding_mismatch",
    ):
        assert_active_workflow_transition_effect_proof_binding(
            proof,
            transition=transition,
            effect=divergent_effect,
            claim_generation=3,
            resource_kind="workflow_event",
            resource_id="event-a",
            resource_revision=7,
            resource_digest=workflow_transition_effect_resource_digest(resource),
        )


def test_structural_binding_does_not_claim_resource_authority() -> None:
    transition, effect = _claimed_effect()
    arbitrary_digest = "d" * 64
    proof = WorkflowTransitionEffectResourceProof(
        context=WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=3,
        ),
        resource_kind="workflow_event",
        resource_id="event-unverified",
        resource_revision=99,
        resource_digest=arbitrary_digest,
    )

    # Exact equality alone is deliberately accepted.  A future adapter must
    # first read this resource and independently establish its semantics.
    assert (
        assert_active_workflow_transition_effect_proof_binding(
            proof,
            transition=transition,
            effect=effect,
            claim_generation=3,
            resource_kind="workflow_event",
            resource_id="event-unverified",
            resource_revision=99,
            resource_digest=arbitrary_digest,
        )
        is proof
    )
