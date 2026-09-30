"""Immutable transition, effect and snapshot value types of the outbox.

The dataclasses validate themselves on construction; the plan helpers
validate a complete effect ledger against its transition header.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent.services.workflow_transition_outbox_digests import (
    workflow_admitted_command_digest,
    workflow_transition_effect_id,
    workflow_transition_effect_result_digest,
    workflow_transition_effect_stage_attempt_count,
    workflow_transition_finalization_result_digest,
    workflow_transition_request_fingerprint,
)
from agent.services.workflow_transition_outbox_validation import (
    _MAX_EFFECT_PAYLOAD_BYTES,
    _MAX_EFFECTS,
    _MAX_FINALIZATION_RESULT_PAYLOAD_BYTES,
    _MAX_IDEMPOTENCY_KEY_CHARS,
    _MAX_RESULT_PAYLOAD_BYTES,
    _MAX_STATUS_BYTES,
    FrozenJsonMapping,
    WorkflowTransitionError,
    _bounded_text,
    _digest,
    _freeze_json_mapping,
    _identity,
    _non_negative_integer,
    _reason_code,
    _sha256,
    _timestamp,
    _validated_mapping,
    thaw_json,
)
from agent.services.workflow_transition_outbox_vocabulary import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    EFFECT_STATE_REJECTED,
    EFFECT_STATES,
    TRANSITION_EFFECT_KINDS,
    TRANSITION_KIND_COMMAND,
    TRANSITION_KINDS,
    TRANSITION_RUNTIMES,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_QUARANTINED,
    TRANSITION_STATE_READY,
    TRANSITION_STATE_REJECTED,
    TRANSITION_STATES,
    WORKFLOW_TRANSITION_EFFECT_SCHEMA,
    WORKFLOW_TRANSITION_SCHEMA,
)


@dataclass(frozen=True)
class WorkflowTransitionEffect:
    """One immutable infrastructure intent plus its durable application proof."""

    effect_id: str
    transition_id: str
    ordinal: int
    kind: str
    idempotency_key: str
    payload: FrozenJsonMapping
    payload_digest: str
    state: str = EFFECT_STATE_PLANNED
    applied_generation: int = 0
    result_payload: FrozenJsonMapping = field(default_factory=dict)
    result_digest: str = ""
    revision: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    schema: str = WORKFLOW_TRANSITION_EFFECT_SCHEMA

    def __post_init__(self) -> None:
        _identity(self.effect_id, "effect_id")
        _identity(self.transition_id, "transition_id")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise WorkflowTransitionError("workflow_transition_effect_ordinal_invalid")
        if self.kind not in TRANSITION_EFFECT_KINDS:
            raise WorkflowTransitionError("workflow_transition_effect_kind_invalid")
        _bounded_text(
            self.idempotency_key,
            _MAX_IDEMPOTENCY_KEY_CHARS,
            "effect_idempotency_key",
        )
        if self.state not in EFFECT_STATES:
            raise WorkflowTransitionError("workflow_transition_effect_state_invalid")
        if self.schema != WORKFLOW_TRANSITION_EFFECT_SCHEMA:
            raise WorkflowTransitionError("workflow_transition_effect_schema_unsupported")
        _non_negative_integer(self.applied_generation, "effect_applied_generation")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise WorkflowTransitionError("workflow_transition_effect_revision_invalid")
        _timestamp(self.created_at, "effect_created_at", positive=True)
        _timestamp(self.updated_at, "effect_updated_at", positive=True)
        if self.updated_at < self.created_at:
            raise WorkflowTransitionError("workflow_transition_effect_timestamp_regressed")

        safe_payload = _validated_mapping(
            self.payload,
            maximum=_MAX_EFFECT_PAYLOAD_BYTES,
            reason="effect_payload",
        )
        if _digest(safe_payload, namespace="workflow-transition-effect-payload") != self.payload_digest:
            raise WorkflowTransitionError("workflow_transition_effect_payload_digest_mismatch")
        _sha256(self.payload_digest, "effect_payload_digest")

        safe_result = _validated_mapping(
            self.result_payload,
            maximum=(
                _MAX_FINALIZATION_RESULT_PAYLOAD_BYTES
                if self.kind == EFFECT_BINDING_FINALIZE
                else _MAX_RESULT_PAYLOAD_BYTES
            ),
            reason="effect_result",
        )
        if self.state == EFFECT_STATE_APPLIED:
            if self.applied_generation < 1 or not safe_result or not self.result_digest:
                raise WorkflowTransitionError("workflow_transition_effect_result_missing")
            _sha256(self.result_digest, "effect_result_digest")
            result_digest = (
                workflow_transition_finalization_result_digest(safe_result)
                if self.kind == EFFECT_BINDING_FINALIZE
                else workflow_transition_effect_result_digest(safe_result)
            )
            if result_digest != self.result_digest:
                raise WorkflowTransitionError("workflow_transition_effect_result_digest_mismatch")
        elif safe_result or self.result_digest:
            raise WorkflowTransitionError("workflow_transition_effect_result_unexpected")
        if self.state == EFFECT_STATE_PLANNED and self.applied_generation != 0:
            raise WorkflowTransitionError("workflow_transition_effect_generation_invalid")
        if self.state == EFFECT_STATE_APPLYING and self.applied_generation < 1:
            raise WorkflowTransitionError("workflow_transition_effect_generation_invalid")

        expected_id = workflow_transition_effect_id(
            transition_id=self.transition_id,
            ordinal=self.ordinal,
            kind=self.kind,
            idempotency_key=self.idempotency_key,
        )
        if self.effect_id != expected_id:
            raise WorkflowTransitionError("workflow_transition_effect_id_mismatch")
        object.__setattr__(self, "payload", _freeze_json_mapping(safe_payload))
        object.__setattr__(self, "result_payload", _freeze_json_mapping(safe_result))

    @classmethod
    def build(
        cls,
        *,
        transition_id: str,
        ordinal: int,
        kind: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        created_at: float,
    ) -> "WorkflowTransitionEffect":
        safe_payload = _validated_mapping(
            payload,
            maximum=_MAX_EFFECT_PAYLOAD_BYTES,
            reason="effect_payload",
        )
        return cls(
            effect_id=workflow_transition_effect_id(
                transition_id=transition_id,
                ordinal=ordinal,
                kind=kind,
                idempotency_key=idempotency_key,
            ),
            transition_id=transition_id,
            ordinal=ordinal,
            kind=kind,
            idempotency_key=idempotency_key,
            payload=safe_payload,
            payload_digest=_digest(
                safe_payload,
                namespace="workflow-transition-effect-payload",
            ),
            created_at=created_at,
            updated_at=created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "effect_id": self.effect_id,
            "transition_id": self.transition_id,
            "ordinal": self.ordinal,
            "kind": self.kind,
            "idempotency_key": self.idempotency_key,
            "payload": thaw_json(self.payload),
            "payload_digest": self.payload_digest,
            "state": self.state,
            "applied_generation": self.applied_generation,
            "result_payload": thaw_json(self.result_payload),
            "result_digest": self.result_digest,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class WorkflowTransition:
    """One Hub-owned recoverable start, advance, or command transition."""

    transition_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    runtime_id: str
    kind: str
    request_payload: FrozenJsonMapping
    request_fingerprint: str
    effect_fingerprint: str
    expected_revision: int
    expected_checkpoint_ref: str
    command_id: str = ""
    receipt_id: str = ""
    admitted_command_digest: str = ""
    state: str = TRANSITION_STATE_READY
    result_status: FrozenJsonMapping = field(default_factory=dict)
    result_checkpoint_ref: str = ""
    outcome_fingerprint: str = ""
    claim_owner: str = ""
    claim_generation: int = 0
    claim_expires_at: float = 0.0
    last_heartbeat_at: float = 0.0
    attempt_count: int = 0
    available_at: float = 0.0
    last_error: str = ""
    revision: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float = 0.0
    schema: str = WORKFLOW_TRANSITION_SCHEMA

    def __post_init__(self) -> None:
        for name in ("transition_id", "tenant_id", "workflow_id", "run_id"):
            _identity(getattr(self, name), name)
        if self.runtime_id not in TRANSITION_RUNTIMES:
            raise WorkflowTransitionError("workflow_transition_runtime_invalid")
        if self.kind not in TRANSITION_KINDS:
            raise WorkflowTransitionError("workflow_transition_kind_invalid")
        if self.schema != WORKFLOW_TRANSITION_SCHEMA:
            raise WorkflowTransitionError("workflow_transition_schema_unsupported")
        safe_request = _validated_mapping(
            self.request_payload,
            maximum=_MAX_EFFECT_PAYLOAD_BYTES,
            reason="request_payload",
            empty=False,
        )
        _sha256(self.request_fingerprint, "request_fingerprint")
        if workflow_transition_request_fingerprint(safe_request) != self.request_fingerprint:
            raise WorkflowTransitionError("workflow_transition_request_fingerprint_mismatch")
        _sha256(self.effect_fingerprint, "effect_fingerprint")
        _non_negative_integer(self.expected_revision, "expected_revision")
        _bounded_text(self.expected_checkpoint_ref, 512, "expected_checkpoint_ref")
        _non_negative_integer(self.claim_generation, "claim_generation")
        _non_negative_integer(self.attempt_count, "attempt_count")
        if self.attempt_count != self.claim_generation:
            raise WorkflowTransitionError("workflow_transition_header_attempt_conflict")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise WorkflowTransitionError("workflow_transition_revision_invalid")

        if self.kind == TRANSITION_KIND_COMMAND:
            _identity(self.command_id, "command_id")
            _sha256(self.admitted_command_digest, "admitted_command_digest")
        elif self.command_id or self.admitted_command_digest or self.receipt_id:
            raise WorkflowTransitionError("workflow_transition_command_fields_unexpected")
        if self.receipt_id:
            _identity(self.receipt_id, "receipt_id")
            if self.kind != TRANSITION_KIND_COMMAND or self.receipt_id != self.command_id:
                raise WorkflowTransitionError("workflow_transition_receipt_binding_invalid")

        if self.state not in TRANSITION_STATES:
            raise WorkflowTransitionError("workflow_transition_state_invalid")
        if self.state == TRANSITION_STATE_APPLYING:
            _identity(self.claim_owner, "claim_owner")
            if self.claim_generation < 1 or self.claim_expires_at <= 0 or self.last_heartbeat_at <= 0:
                raise WorkflowTransitionError("workflow_transition_claim_invalid")
        elif self.claim_owner or self.claim_expires_at != 0:
            raise WorkflowTransitionError("workflow_transition_claim_invalid")

        for name in (
            "claim_expires_at",
            "last_heartbeat_at",
            "available_at",
            "created_at",
            "updated_at",
            "completed_at",
        ):
            _timestamp(
                getattr(self, name),
                name,
                positive=name in {"created_at", "updated_at"},
            )
        if self.updated_at < self.created_at:
            raise WorkflowTransitionError("workflow_transition_timestamp_regressed")
        if self.available_at < self.created_at:
            raise WorkflowTransitionError("workflow_transition_available_at_invalid")

        safe_result = _validated_mapping(
            self.result_status,
            maximum=_MAX_STATUS_BYTES,
            reason="result_status",
        )
        if self.state == TRANSITION_STATE_COMPLETED:
            if not safe_result or not self.result_checkpoint_ref or not self.outcome_fingerprint:
                raise WorkflowTransitionError("workflow_transition_completion_proof_missing")
            _bounded_text(self.result_checkpoint_ref, 512, "result_checkpoint_ref")
            _sha256(self.outcome_fingerprint, "outcome_fingerprint")
            if self.completed_at <= 0:
                raise WorkflowTransitionError("workflow_transition_completed_at_invalid")
        elif safe_result or self.result_checkpoint_ref or self.outcome_fingerprint or self.completed_at:
            raise WorkflowTransitionError("workflow_transition_completion_proof_unexpected")
        if self.state in {TRANSITION_STATE_QUARANTINED, TRANSITION_STATE_REJECTED}:
            _reason_code(self.last_error)
        elif self.last_error:
            _reason_code(self.last_error)

        object.__setattr__(self, "request_payload", _freeze_json_mapping(safe_request))
        object.__setattr__(self, "result_status", _freeze_json_mapping(safe_result))

    @classmethod
    def build(
        cls,
        *,
        transition_id: str,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        runtime_id: str,
        kind: str,
        request_payload: Mapping[str, Any],
        effects: Sequence[WorkflowTransitionEffect],
        expected_revision: int,
        expected_checkpoint_ref: str,
        created_at: float,
        command_id: str = "",
        receipt_id: str = "",
        admitted_command: Mapping[str, Any] | None = None,
    ) -> "WorkflowTransition":
        admitted_digest = workflow_admitted_command_digest(admitted_command) if admitted_command is not None else ""
        return cls(
            transition_id=transition_id,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            runtime_id=runtime_id,
            kind=kind,
            request_payload=request_payload,
            command_id=command_id,
            receipt_id=receipt_id,
            request_fingerprint=workflow_transition_request_fingerprint(request_payload),
            admitted_command_digest=admitted_digest,
            effect_fingerprint=workflow_transition_effect_fingerprint(effects),
            expected_revision=expected_revision,
            expected_checkpoint_ref=expected_checkpoint_ref,
            available_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "transition_id": self.transition_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "kind": self.kind,
            "request_payload": thaw_json(self.request_payload),
            "command_id": self.command_id,
            "receipt_id": self.receipt_id,
            "request_fingerprint": self.request_fingerprint,
            "admitted_command_digest": self.admitted_command_digest,
            "effect_fingerprint": self.effect_fingerprint,
            "expected_revision": self.expected_revision,
            "expected_checkpoint_ref": self.expected_checkpoint_ref,
            "state": self.state,
            "result_status": thaw_json(self.result_status),
            "result_checkpoint_ref": self.result_checkpoint_ref,
            "outcome_fingerprint": self.outcome_fingerprint,
            "claim_owner": self.claim_owner,
            "claim_generation": self.claim_generation,
            "claim_expires_at": self.claim_expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "attempt_count": self.attempt_count,
            "available_at": self.available_at,
            "last_error": self.last_error,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class WorkflowTransitionSnapshot:
    """Immutable transition aggregate returned by every store adapter."""

    transition: WorkflowTransition
    effects: tuple[WorkflowTransitionEffect, ...]

    def __post_init__(self) -> None:
        normalized = _validated_effects(
            self.effects,
            transition_id=self.transition.transition_id,
        )
        final_effects = [effect for effect in normalized if effect.kind == EFFECT_BINDING_FINALIZE]
        if len(final_effects) != 1 or final_effects[0].ordinal != len(normalized):
            raise WorkflowTransitionError("workflow_transition_binding_finalize_effect_invalid")
        if workflow_transition_effect_fingerprint(normalized) != self.transition.effect_fingerprint:
            raise WorkflowTransitionError("workflow_transition_effect_fingerprint_mismatch")
        if self.transition.state == TRANSITION_STATE_COMPLETED and any(
            effect.state != EFFECT_STATE_APPLIED for effect in normalized
        ):
            raise WorkflowTransitionError("workflow_transition_completion_effect_proof_missing")
        if self.transition.state == TRANSITION_STATE_REJECTED and any(
            effect.state != EFFECT_STATE_REJECTED for effect in normalized
        ):
            raise WorkflowTransitionError("workflow_transition_rejection_effect_proof_missing")
        object.__setattr__(self, "effects", normalized)


def workflow_transition_finalization_stage_attempt_count(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
) -> int:
    """Validate the applied prefix and return the finalization-stage attempts."""

    values = _validated_effects(effects, transition_id=transition.transition_id)
    if transition.attempt_count != transition.claim_generation:
        raise WorkflowTransitionError("workflow_transition_header_attempt_conflict")
    non_final = [effect for effect in values if effect.kind != EFFECT_BINDING_FINALIZE]
    if any(effect.state != EFFECT_STATE_APPLIED for effect in non_final):
        raise WorkflowTransitionError("workflow_transition_effects_incomplete")
    previous_generation = 0
    for effect in non_final:
        if effect.applied_generation <= previous_generation or effect.applied_generation >= transition.claim_generation:
            raise WorkflowTransitionError("workflow_transition_effect_application_generation_invalid")
        stage_attempts = workflow_transition_effect_stage_attempt_count(effect.result_payload)
        if stage_attempts != effect.applied_generation - previous_generation:
            raise WorkflowTransitionError("workflow_transition_effect_stage_attempt_invalid")
        previous_generation = effect.applied_generation
    finalization_attempts = transition.claim_generation - previous_generation
    if finalization_attempts < 1:
        raise WorkflowTransitionError("workflow_transition_effect_stage_attempt_invalid")
    return finalization_attempts


def workflow_transition_effect_fingerprint(
    effects: Sequence[WorkflowTransitionEffect],
) -> str:
    values = tuple(effects)
    if not values or len(values) > _MAX_EFFECTS:
        raise WorkflowTransitionError("workflow_transition_effect_count_invalid")
    descriptor = [
        {
            "effect_id": effect.effect_id,
            "ordinal": effect.ordinal,
            "kind": effect.kind,
            "idempotency_key": effect.idempotency_key,
            "payload_digest": effect.payload_digest,
        }
        for effect in values
    ]
    return _digest(descriptor, namespace="workflow-transition-effect-plan")


def workflow_transition_outcome_fingerprint(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
    *,
    binding_status: Mapping[str, Any],
    checkpoint_ref: str,
    finalization_proof: Mapping[str, Any],
    public_status: Mapping[str, Any] | None = None,
    receipt_result: Mapping[str, Any] | None = None,
) -> str:
    """Bind effects, raw binding state, and its canonical public projection."""

    values = _validated_effects(effects, transition_id=transition.transition_id)
    non_final = [effect for effect in values if effect.kind != EFFECT_BINDING_FINALIZE]
    finalization_attempts = workflow_transition_finalization_stage_attempt_count(
        transition,
        values,
    )
    status = _validated_mapping(
        binding_status,
        maximum=_MAX_STATUS_BYTES,
        reason="binding_status",
        empty=False,
    )
    _bounded_text(checkpoint_ref, 512, "checkpoint_ref")
    if public_status is not None and receipt_result is not None:
        raise WorkflowTransitionError("workflow_transition_public_status_ambiguous")
    projected = public_status if public_status is not None else receipt_result
    if projected is None:
        raise WorkflowTransitionError("workflow_transition_public_status_missing")
    canonical_status = _validated_mapping(
        projected,
        maximum=_MAX_STATUS_BYTES,
        reason="public_status",
        empty=False,
    )
    proof = _validated_mapping(
        finalization_proof,
        maximum=_MAX_EFFECT_PAYLOAD_BYTES,
        reason="finalization_proof",
        empty=False,
    )
    return _digest(
        {
            "transition_id": transition.transition_id,
            "command_id": transition.command_id,
            "request_fingerprint": transition.request_fingerprint,
            "effect_fingerprint": transition.effect_fingerprint,
            "effect_results": [
                {
                    "effect_id": effect.effect_id,
                    "result_digest": effect.result_digest,
                }
                for effect in non_final
            ],
            "binding_status": status,
            "checkpoint_ref": checkpoint_ref,
            "finalization_stage_attempt_count": finalization_attempts,
            "finalization_proof": proof,
            "public_status": canonical_status,
        },
        namespace="workflow-transition-outcome",
    )


def validate_transition_plan(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
) -> tuple[WorkflowTransitionEffect, ...]:
    """Validate the immutable stage candidate and return its effect tuple."""

    if transition.state != TRANSITION_STATE_READY or any(
        (
            transition.claim_owner,
            transition.claim_generation,
            transition.claim_expires_at,
            transition.last_heartbeat_at,
            transition.attempt_count,
            transition.last_error,
        )
    ):
        raise WorkflowTransitionError("workflow_transition_stage_state_invalid")
    if transition.revision != 1 or transition.updated_at != transition.created_at:
        raise WorkflowTransitionError("workflow_transition_stage_revision_invalid")
    values = _validated_effects(effects, transition_id=transition.transition_id)
    if any(
        effect.state != EFFECT_STATE_PLANNED
        or effect.revision != 1
        or effect.created_at != transition.created_at
        or effect.updated_at != transition.created_at
        for effect in values
    ):
        raise WorkflowTransitionError("workflow_transition_effect_stage_state_invalid")
    final_effects = [effect for effect in values if effect.kind == EFFECT_BINDING_FINALIZE]
    if len(final_effects) != 1 or final_effects[0].ordinal != len(values):
        raise WorkflowTransitionError("workflow_transition_binding_finalize_effect_invalid")
    if workflow_transition_effect_fingerprint(values) != transition.effect_fingerprint:
        raise WorkflowTransitionError("workflow_transition_effect_fingerprint_mismatch")
    return values


def _validated_effects(
    effects: Sequence[WorkflowTransitionEffect],
    *,
    transition_id: str,
) -> tuple[WorkflowTransitionEffect, ...]:
    values = tuple(effects)
    if not values or len(values) > _MAX_EFFECTS:
        raise WorkflowTransitionError("workflow_transition_effect_count_invalid")
    if any(not isinstance(effect, WorkflowTransitionEffect) for effect in values):
        raise WorkflowTransitionError("workflow_transition_effect_invalid")
    if [effect.ordinal for effect in values] != list(range(1, len(values) + 1)):
        raise WorkflowTransitionError("workflow_transition_effect_order_invalid")
    if any(effect.transition_id != transition_id for effect in values):
        raise WorkflowTransitionError("workflow_transition_effect_binding_mismatch")
    if len({effect.effect_id for effect in values}) != len(values):
        raise WorkflowTransitionError("workflow_transition_effect_id_duplicate")
    if len({effect.idempotency_key for effect in values}) != len(values):
        raise WorkflowTransitionError("workflow_transition_effect_idempotency_duplicate")
    return values
