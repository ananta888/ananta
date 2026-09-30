"""Fencing, compare-and-set preconditions and input rules of transition persistence.

Both store adapters share these pure checks, so the in-memory and SQL
adapters fail closed with identical reason codes.  Nothing here opens a
session, takes a lock or mutates state.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from agent.db_models.workflow_runtime import (
    WorkflowControlBindingDB,
    WorkflowControlCommandReceiptDB,
    WorkflowTransitionOutboxDB,
)
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_PLANNED,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_QUARANTINED,
    TRANSITION_STATE_READY,
    TRANSITION_STATE_REJECTED,
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionError,
    WorkflowTransitionPublicProjectionPort,
    WorkflowTransitionSnapshot,
    thaw_json,
    workflow_transition_effect_result_digest,
    workflow_transition_effect_stage_attempt_count,
    workflow_transition_finalization_result_digest,
    workflow_transition_finalization_stage_attempt_count,
    workflow_transition_outcome_fingerprint,
    workflow_transition_request_fingerprint,
)

_RECEIPT_ACTIVE_STATES = frozenset({"pending", "dispatching"})
_ACTIVE_MARKER_TRANSITION_STATES = frozenset(
    {TRANSITION_STATE_READY, TRANSITION_STATE_APPLYING, TRANSITION_STATE_QUARANTINED}
)
_MAX_LEASE_SECONDS = 300.0
_MAX_RESULT_BYTES = 524_288


class WorkflowTransitionPersistenceError(WorkflowTransitionError):
    """Stable persistence or compare-and-set failure."""


def _same_snapshot_or_raise(
    existing: WorkflowTransitionSnapshot,
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
) -> WorkflowTransitionSnapshot:
    existing_plan = existing.transition.to_dict()
    requested_plan = transition.to_dict()
    mutable_fields = {
        "state",
        "result_status",
        "result_checkpoint_ref",
        "outcome_fingerprint",
        "claim_owner",
        "claim_generation",
        "claim_expires_at",
        "last_heartbeat_at",
        "attempt_count",
        "available_at",
        "last_error",
        "revision",
        "created_at",
        "updated_at",
        "completed_at",
    }
    for field_name in mutable_fields:
        existing_plan.pop(field_name, None)
        requested_plan.pop(field_name, None)
    existing_effects = [
        {
            key: value
            for key, value in effect.to_dict().items()
            if key
            not in {
                "state",
                "applied_generation",
                "result_payload",
                "result_digest",
                "revision",
                "created_at",
                "updated_at",
            }
        }
        for effect in existing.effects
    ]
    requested_effects = [
        {
            key: value
            for key, value in effect.to_dict().items()
            if key
            not in {
                "state",
                "applied_generation",
                "result_payload",
                "result_digest",
                "revision",
                "created_at",
                "updated_at",
            }
        }
        for effect in effects
    ]
    if canonical_json(existing_plan) != canonical_json(requested_plan) or canonical_json(
        existing_effects
    ) != canonical_json(requested_effects):
        raise WorkflowTransitionPersistenceError("workflow_transition_stage_conflict")
    return existing


def _linked_receipt(transition: WorkflowTransition, receipt_id: str) -> str:
    explicit = str(receipt_id or "")
    if explicit and explicit != transition.receipt_id:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_binding_invalid")
    return transition.receipt_id


def _assert_memory_binding_for_stage(
    binding: Mapping[str, Any] | None,
    *,
    transition: WorkflowTransition,
    receipt_id: str,
    now: float,
) -> None:
    if binding is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_not_found")
    if (
        binding.get("tenant_id") != transition.tenant_id
        or binding.get("workflow_id") != transition.workflow_id
        or binding.get("run_id") != transition.run_id
        or not _runtime_matches(str(binding.get("runtime_id") or ""), transition.runtime_id)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_mismatch")
    if (
        int(binding.get("runtime_revision") or 0) != transition.expected_revision
        or str(binding.get("runtime_checkpoint_ref") or "") != transition.expected_checkpoint_ref
        or str(binding.get("active_transition_id") or "")
        or str(binding.get("dispatch_intent_id") or "")
        or str(binding.get("command_claim") or "")
        or bool(binding.get("command_observation_pending"))
        or str(binding.get("command_receipt_id") or "") != receipt_id
        or (str(binding.get("scheduler_owner") or "") and float(binding.get("scheduler_lease_expires_at") or 0.0) > now)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_stage_cas_conflict")


def _assert_sql_binding_for_stage(
    binding: WorkflowControlBindingDB | None,
    *,
    transition: WorkflowTransition,
    receipt_id: str,
    now: float,
) -> None:
    if binding is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_not_found")
    if (
        binding.tenant_id != transition.tenant_id
        or binding.workflow_id != transition.workflow_id
        or binding.run_id != transition.run_id
        or not _runtime_matches(str(binding.runtime_id), transition.runtime_id)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_mismatch")
    if (
        int(binding.runtime_revision) != transition.expected_revision
        or str(binding.runtime_checkpoint_ref) != transition.expected_checkpoint_ref
        or str(binding.active_transition_id or "")
        or str(binding.dispatch_intent_id or "")
        or str(binding.command_claim or "")
        or bool(binding.command_observation_pending)
        or str(binding.command_receipt_id or "") != receipt_id
        or (str(binding.scheduler_owner or "") and float(binding.scheduler_lease_expires_at) > now)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_stage_cas_conflict")


def _assert_memory_receipt_for_stage(
    receipt: Mapping[str, Any] | None,
    *,
    transition: WorkflowTransition,
    now: float,
) -> None:
    if not transition.receipt_id:
        return
    if receipt is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_not_found")
    if (
        receipt.get("id") != transition.receipt_id
        or receipt.get("tenant_id") != transition.tenant_id
        or receipt.get("workflow_id") != transition.workflow_id
        or receipt.get("run_id") != transition.run_id
        or int(receipt.get("expected_revision") or 0) != transition.expected_revision
        or receipt.get("checkpoint_ref") != transition.expected_checkpoint_ref
        or workflow_transition_request_fingerprint(receipt.get("request_payload") or {})
        != transition.request_fingerprint
        or str(receipt.get("request_fingerprint") or "") not in {"", transition.request_fingerprint}
        or receipt.get("state") != "pending"
        or str(receipt.get("transition_id") or "")
        or str(receipt.get("effect_fingerprint") or "")
        or str(receipt.get("outcome_fingerprint") or "")
        or str(receipt.get("dispatch_owner") or "")
        or float(receipt.get("dispatch_lease_expires_at") or 0.0) != 0.0
        or int(receipt.get("dispatch_generation") or 0) != 0
        or float(receipt.get("last_heartbeat_at") or 0.0) != 0.0
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_stage_conflict")


def _assert_sql_receipt_for_stage(
    receipt: WorkflowControlCommandReceiptDB | None,
    *,
    transition: WorkflowTransition,
    now: float,
) -> None:
    if not transition.receipt_id:
        return
    if receipt is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_not_found")
    if (
        receipt.id != transition.receipt_id
        or receipt.tenant_id != transition.tenant_id
        or receipt.workflow_id != transition.workflow_id
        or receipt.run_id != transition.run_id
        or int(receipt.expected_revision) != transition.expected_revision
        or receipt.checkpoint_ref != transition.expected_checkpoint_ref
        or workflow_transition_request_fingerprint(receipt.request_payload or {}) != transition.request_fingerprint
        or str(receipt.request_fingerprint or "") not in {"", transition.request_fingerprint}
        or receipt.state != "pending"
        or str(receipt.transition_id or "")
        or str(receipt.effect_fingerprint or "")
        or str(receipt.outcome_fingerprint or "")
        or str(receipt.dispatch_owner or "")
        or float(receipt.dispatch_lease_expires_at) != 0.0
        or int(receipt.dispatch_generation) != 0
        or float(receipt.last_heartbeat_at) != 0.0
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_stage_conflict")


def _assert_owned(
    transition: WorkflowTransition,
    *,
    owner_id: str,
    claim_generation: int,
    now: float,
) -> None:
    if (
        transition.state != TRANSITION_STATE_APPLYING
        or transition.claim_owner != _owner_id(owner_id)
        or transition.claim_generation != _generation(claim_generation)
        or transition.claim_expires_at <= now
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")


def _assert_sql_owned(
    row: WorkflowTransitionOutboxDB | None,
    *,
    owner_id: str,
    claim_generation: int,
    now: float,
) -> None:
    if row is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
    if (
        row.state != TRANSITION_STATE_APPLYING
        or row.claim_owner != _owner_id(owner_id)
        or int(row.claim_generation) != _generation(claim_generation)
        or float(row.claim_expires_at) <= now
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")


def _assert_binding_finalize_state(
    binding: Mapping[str, Any] | WorkflowControlBindingDB | None,
    transition: WorkflowTransition,
) -> None:
    _assert_binding_execution_state(binding, transition)


def _assert_binding_execution_state(
    binding: Mapping[str, Any] | WorkflowControlBindingDB | None,
    transition: WorkflowTransition,
) -> None:
    """Fence authority before send and before any retry/progress requeue."""

    _assert_binding_quarantine_state(binding, transition)
    if binding is None:  # pragma: no cover - guarded above
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_not_found")

    def get(name: str) -> Any:
        return binding.get(name) if isinstance(binding, Mapping) else getattr(binding, name)

    if (
        int(get("runtime_revision") or 0) != transition.expected_revision
        or str(get("runtime_checkpoint_ref") or "") != transition.expected_checkpoint_ref
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")


def _assert_binding_quarantine_state(
    binding: Mapping[str, Any] | WorkflowControlBindingDB | None,
    transition: WorkflowTransition,
) -> None:
    """Validate aggregate identity without requiring the possibly-drifted revision."""

    if binding is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_not_found")

    def get(name: str) -> Any:
        return binding.get(name) if isinstance(binding, Mapping) else getattr(binding, name)

    if (
        str(get("active_transition_id") or "") != transition.transition_id
        or str(get("tenant_id") or "") != transition.tenant_id
        or str(get("workflow_id") or "") != transition.workflow_id
        or str(get("run_id") or "") != transition.run_id
        or not _runtime_matches(str(get("runtime_id") or ""), transition.runtime_id)
        or str(get("command_receipt_id") or "") != transition.receipt_id
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")


def _assert_receipt_finalize_state(
    receipt: Mapping[str, Any] | WorkflowControlCommandReceiptDB | None,
    transition: WorkflowTransition,
) -> None:
    if transition.state != TRANSITION_STATE_APPLYING:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
    _assert_receipt_lease_mirror(receipt, transition)


def _assert_receipt_lease_mirror(
    receipt: Mapping[str, Any] | WorkflowControlCommandReceiptDB | None,
    transition: WorkflowTransition,
) -> None:
    if not transition.receipt_id:
        if receipt is not None:
            raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
        return
    if receipt is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_not_found")

    def get(name: str) -> Any:
        return receipt.get(name) if isinstance(receipt, Mapping) else getattr(receipt, name)

    receipt_state = {
        TRANSITION_STATE_READY: "pending",
        TRANSITION_STATE_APPLYING: "dispatching",
        TRANSITION_STATE_COMPLETED: "completed",
        TRANSITION_STATE_QUARANTINED: "pending",
        TRANSITION_STATE_REJECTED: "rejected",
    }.get(transition.state)
    expected_owner = transition.claim_owner if transition.state == TRANSITION_STATE_APPLYING else ""
    expected_expiry = transition.claim_expires_at if transition.state == TRANSITION_STATE_APPLYING else 0.0
    if (
        str(get("id") or "") != transition.receipt_id
        or str(get("transition_id") or "") != transition.transition_id
        or str(get("request_fingerprint") or "") != transition.request_fingerprint
        or str(get("effect_fingerprint") or "") != transition.effect_fingerprint
        or str(get("state") or "") != receipt_state
        or str(get("dispatch_owner") or "") != expected_owner
        or int(get("dispatch_generation") or 0) != transition.claim_generation
        or float(get("dispatch_lease_expires_at") or 0.0) != expected_expiry
        or float(get("last_heartbeat_at") or 0.0) != transition.last_heartbeat_at
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")


def _finalization_values(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
    *,
    binding_status: Mapping[str, Any],
    checkpoint_ref: str,
    finalization_proof: Mapping[str, Any],
    outcome_fingerprint: str,
    public_status: Mapping[str, Any],
    claim_generation: int,
    now: float,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    str,
    tuple[WorkflowTransitionEffect, ...],
]:
    supplied_outcome = _optional_outcome_fingerprint(outcome_fingerprint)
    status = _bounded_mapping(binding_status, reason="binding_status", empty=False)
    if not isinstance(checkpoint_ref, str) or not checkpoint_ref or len(checkpoint_ref) > 512:
        raise WorkflowTransitionPersistenceError("workflow_transition_checkpoint_ref_invalid")
    revision = _status_revision(status)
    if revision <= transition.expected_revision:
        raise WorkflowTransitionPersistenceError("workflow_transition_status_revision_not_advanced")
    observed_checkpoint = status.get("checkpoint_ref")
    if not isinstance(observed_checkpoint, str) or observed_checkpoint != checkpoint_ref:
        raise WorkflowTransitionPersistenceError("workflow_transition_status_checkpoint_mismatch")
    canonical_public = _bounded_mapping(
        public_status,
        reason="public_status",
        empty=False,
    )
    if not isinstance(finalization_proof, Mapping):
        raise WorkflowTransitionPersistenceError("workflow_transition_finalization_proof_invalid")
    proof = _bounded_mapping(
        finalization_proof,
        reason="finalization_proof",
        empty=False,
    )
    try:
        finalization_attempts = workflow_transition_finalization_stage_attempt_count(
            transition,
            effects,
        )
        expected_outcome = workflow_transition_outcome_fingerprint(
            transition,
            effects,
            binding_status=status,
            checkpoint_ref=checkpoint_ref,
            finalization_proof=proof,
            public_status=canonical_public,
        )
    except WorkflowTransitionError as exc:
        if str(exc) == "workflow_transition_header_attempt_conflict":
            raise WorkflowTransitionPersistenceError(str(exc)) from exc
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_envelope_invalid") from exc
    if supplied_outcome and supplied_outcome != expected_outcome:
        raise WorkflowTransitionPersistenceError("workflow_transition_outcome_fingerprint_mismatch")

    values = list(effects)
    if not values or values[-1].kind != EFFECT_BINDING_FINALIZE:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_finalize_effect_invalid")
    if any(effect.state != EFFECT_STATE_APPLIED for effect in values[:-1]):
        raise WorkflowTransitionPersistenceError("workflow_transition_effects_incomplete")
    final_effect = values[-1]
    if final_effect.state != EFFECT_STATE_PLANNED or final_effect.applied_generation != 0:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_finalize_effect_conflict")
    final_result = {
        "checkpoint_ref": checkpoint_ref,
        "finalization_stage_attempt_count": finalization_attempts,
        "finalization_proof": proof,
        "outcome_fingerprint": expected_outcome,
        "public_status": canonical_public,
        "receipt_completed": bool(transition.receipt_id),
        "status_revision": revision,
    }
    values[-1] = replace(
        final_effect,
        state=EFFECT_STATE_APPLIED,
        applied_generation=_generation(claim_generation),
        result_payload=final_result,
        result_digest=workflow_transition_finalization_result_digest(final_result),
        revision=final_effect.revision + 1,
        updated_at=now,
    )
    return status, canonical_public, expected_outcome, tuple(values)


def _project_public_status(
    projector: WorkflowTransitionPublicProjectionPort | None,
    *,
    transition: WorkflowTransition,
    binding: Mapping[str, Any],
    binding_status: Mapping[str, Any],
    receipt_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if projector is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_projector_required")
    context = _bounded_mapping(binding, reason="binding_projection_context", empty=False)
    raw_status = _bounded_mapping(binding_status, reason="binding_status", empty=False)
    previous_value = context.get("public_status")
    previous = (
        _bounded_mapping(previous_value, reason="previous_public_status", empty=True)
        if isinstance(previous_value, Mapping)
        else None
    )
    try:
        projected = projector.project(
            transition=transition,
            binding=context,
            binding_status=raw_status,
            previous_public_status=previous or None,
        )
        canonical = _bounded_mapping(projected, reason="public_projection", empty=False)
    except WorkflowTransitionPersistenceError:
        raise
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_projection_invalid") from exc
    if receipt_result is not None:
        supplied = _bounded_mapping(receipt_result, reason="receipt_result", empty=False)
        if canonical_json(canonical) != canonical_json(supplied):
            raise WorkflowTransitionPersistenceError("workflow_transition_receipt_projection_mismatch")
    return canonical


def _effect(
    effects: Sequence[WorkflowTransitionEffect],
    effect_id: str,
) -> tuple[int, WorkflowTransitionEffect]:
    for index, value in enumerate(effects):
        if value.effect_id == str(effect_id):
            return index, value
    raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")


def _assert_effect_rejection_safe(
    effects: Sequence[WorkflowTransitionEffect],
) -> None:
    if any(effect.state != EFFECT_STATE_PLANNED for effect in effects):
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_recovery_required")


def _assert_effect_begin_order(
    effects: Sequence[WorkflowTransitionEffect],
    *,
    effect_index: int,
) -> None:
    """Deny execution authority when a later non-final stage already progressed."""

    if any(
        effect.kind != EFFECT_BINDING_FINALIZE and effect.state != EFFECT_STATE_PLANNED
        for effect in effects[effect_index + 1 :]
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_order_conflict")


def _assert_yield_effect(
    effects: Sequence[WorkflowTransitionEffect],
    *,
    effect_id: str,
    claim_generation: int,
) -> None:
    _index, effect = _effect(effects, str(effect_id))
    generation = _generation(claim_generation)
    current_generation = tuple(
        candidate
        for candidate in effects
        if candidate.state == EFFECT_STATE_APPLIED and candidate.applied_generation == generation
    )
    if (
        effect.kind == EFFECT_BINDING_FINALIZE
        or effect.state != EFFECT_STATE_APPLIED
        or effect.applied_generation != generation
        or current_generation != (effect,)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_yield_effect_conflict")


def _assert_effect_stage_attempt(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
    *,
    effect_index: int,
    result_payload: Mapping[str, Any],
) -> None:
    try:
        if transition.attempt_count != transition.claim_generation:
            raise WorkflowTransitionError("workflow_transition_header_attempt_conflict")
        previous_generation = 0
        for effect in effects[:effect_index]:
            if effect.kind == EFFECT_BINDING_FINALIZE or effect.state != EFFECT_STATE_APPLIED:
                continue
            if (
                effect.applied_generation <= previous_generation
                or effect.applied_generation >= transition.claim_generation
            ):
                raise WorkflowTransitionError("workflow_transition_effect_application_generation_invalid")
            stage_attempts = workflow_transition_effect_stage_attempt_count(effect.result_payload)
            if stage_attempts != effect.applied_generation - previous_generation:
                raise WorkflowTransitionError("workflow_transition_effect_stage_attempt_invalid")
            previous_generation = effect.applied_generation
        supplied = workflow_transition_effect_stage_attempt_count(result_payload)
    except WorkflowTransitionError as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_envelope_invalid") from exc
    expected = transition.claim_generation - previous_generation
    if expected < 1 or supplied != expected:
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_stage_attempt_conflict")


def _claimable(transition: WorkflowTransition, *, now: float) -> bool:
    return bool(
        transition.available_at <= now
        and (
            transition.state == TRANSITION_STATE_READY
            or (transition.state == TRANSITION_STATE_APPLYING and transition.claim_expires_at <= now)
        )
    )


def _row_claimable(row: WorkflowTransitionOutboxDB, *, now: float) -> bool:
    return bool(
        float(row.available_at) <= now
        and (
            row.state == TRANSITION_STATE_READY
            or (row.state == TRANSITION_STATE_APPLYING and float(row.claim_expires_at) <= now)
        )
    )


def _runtime_matches(binding_runtime: str, transition_runtime: str) -> bool:
    if binding_runtime == transition_runtime:
        return True
    return binding_runtime == "local" and transition_runtime == "ananta-native"


def _result_payload(
    value: Mapping[str, Any],
    *,
    result_digest: str,
) -> dict[str, Any]:
    safe = _bounded_mapping(value, reason="effect_result", empty=False)
    if workflow_transition_effect_result_digest(safe) != str(result_digest):
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_digest_mismatch")
    try:
        workflow_transition_effect_stage_attempt_count(safe)
    except WorkflowTransitionError as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_envelope_invalid") from exc
    return safe


def _bounded_mapping(
    value: Mapping[str, Any],
    *,
    reason: str,
    empty: bool,
) -> dict[str, Any]:
    safe = _mapping_copy(value)
    if not isinstance(safe, dict) or (not empty and not safe):
        raise WorkflowTransitionPersistenceError(f"workflow_transition_{reason}_invalid")
    try:
        size = len(canonical_json(safe).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError(f"workflow_transition_{reason}_invalid") from exc
    if size > _MAX_RESULT_BYTES:
        raise WorkflowTransitionPersistenceError(f"workflow_transition_{reason}_too_large")
    return safe


def _mapping_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    thawed = thaw_json(value)
    if not isinstance(thawed, dict):
        raise WorkflowTransitionPersistenceError("workflow_transition_mapping_invalid")
    return thawed


def _status_revision(status: Mapping[str, Any]) -> int:
    value = status.get("revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowTransitionPersistenceError("workflow_transition_status_revision_invalid")
    return value


def _owner_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 256
        or "\x00" in normalized
        or any(not character.isprintable() for character in normalized)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_owner_id_invalid")
    return normalized


def _generation(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowTransitionPersistenceError("workflow_transition_claim_generation_invalid")
    return value


def _lease_seconds(value: Any) -> float:
    if isinstance(value, bool):
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_invalid")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_invalid") from exc
    if not math.isfinite(normalized) or not 1.0 <= normalized <= _MAX_LEASE_SECONDS:
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_invalid")
    return normalized


def _retry_at(value: Any) -> float:
    if isinstance(value, bool):
        raise WorkflowTransitionPersistenceError("workflow_transition_retry_at_invalid")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_retry_at_invalid") from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise WorkflowTransitionPersistenceError("workflow_transition_retry_at_invalid")
    return normalized


def _optional_outcome_fingerprint(value: Any) -> str:
    if not isinstance(value, str):
        raise WorkflowTransitionPersistenceError("workflow_transition_outcome_fingerprint_invalid")
    if value and (len(value) != 64 or any(character not in "0123456789abcdef" for character in value)):
        raise WorkflowTransitionPersistenceError("workflow_transition_outcome_fingerprint_invalid")
    return value


def _reason_code(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 160
        or not normalized[0].islower()
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in normalized)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_reason_code_invalid")
    return normalized


def _limit(value: Any) -> int:
    if isinstance(value, bool):
        raise WorkflowTransitionPersistenceError("workflow_transition_limit_invalid")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_limit_invalid") from exc
    if not 1 <= normalized <= 1000:
        raise WorkflowTransitionPersistenceError("workflow_transition_limit_invalid")
    return normalized
