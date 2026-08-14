"""Unwired Hub-ledger authorization effect for workflow transitions.

The adapter authorizes no provider call and grants no capability.  It records
only a previously verified envelope/grant digest in the Hub-owned side-effect
ledger and an append-only historical receipt.  Grant signature, expiry, and
revocation revalidation remain a mandatory live-cutover gate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_runtime.side_effects import (
    WorkflowTransitionSideEffectAuthorizationCommitPort,
    WorkflowTransitionSideEffectAuthorizationIntent,
    WorkflowTransitionSideEffectAuthorizationObservation,
    WorkflowTransitionSideEffectAuthorizationReadPort,
    WorkflowTransitionSideEffectAuthorizationReceipt,
    workflow_transition_side_effect_authorization_receipt_id,
    workflow_transition_side_effect_operation_fence_id,
    workflow_transition_side_effect_operation_intent_digest,
)
from agent.services.workflow_transition_effect_execution import (
    EffectAlreadyApplied,
    EffectApplied,
    EffectExecutable,
    EffectQuarantine,
    EffectRetry,
    WorkflowTransitionEffectAttempt,
    WorkflowTransitionEffectObservation,
    WorkflowTransitionHeartbeatContext,
)
from agent.services.workflow_transition_effect_proofs import (
    WorkflowTransitionEffectAbsenceProof,
    WorkflowTransitionEffectProofContext,
    WorkflowTransitionEffectResourceProof,
    assert_active_workflow_transition_effect_absence_proof_binding,
    assert_active_workflow_transition_effect_proof_binding,
    assert_durable_workflow_transition_effect_proof_binding,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_SIDE_EFFECT_AUTHORIZE,
    TRANSITION_RUNTIMES,
    WorkflowTransition,
    WorkflowTransitionEffect,
    workflow_transition_effect_id,
)
from ananta_contracts.workflow_operation import operation_id_for

WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_EFFECT_SCHEMA = (
    "ananta.workflow_transition_side_effect_authorization_effect.v1"
)
WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RESULT_SCHEMA = (
    "ananta.workflow_transition_side_effect_authorization_result.v1"
)
WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RESOURCE_KIND = "workflow_side_effect_authorization_receipt"
WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_SLOT_KIND = "workflow_side_effect_authorization_slot"

_EFFECT_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "transition_id",
        "effect_id",
        "runtime_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "effect_ordinal",
        "declared_operation",
        "side_effect_class",
        "operation_id",
        "operation_payload_digest",
        "operation_intent_digest",
        "operation_fence_id",
        "authorization_envelope_id",
        "authorization_envelope_digest",
        "ownership_attempt_id",
        "ownership_fencing_token",
        "receipt_id",
    }
)
_RESULT_FIELDS = frozenset({"schema", "receipt"})
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_WRITE_CLASSES = frozenset({"idempotent_write", "non_idempotent_write"})
_MAX_OPERATION_CHARS = 512
_MAX_COUNTER = 2**63 - 1


class WorkflowTransitionSideEffectAuthorizationError(ValueError):
    """Stable fail-closed staged intent or adapter error."""


@runtime_checkable
class WorkflowTransitionSideEffectAuthorizationAuthority(
    WorkflowTransitionSideEffectAuthorizationReadPort,
    WorkflowTransitionSideEffectAuthorizationCommitPort,
    Protocol,
):
    """The single aggregate authority required by the mutating executor."""


@final
@dataclass(frozen=True, slots=True)
class _StagedAuthorization:
    transition_id: str
    effect_id: str
    runtime_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    effect_ordinal: int
    declared_operation: str
    side_effect_class: str
    operation_id: str
    operation_payload_digest: str
    operation_intent_digest: str
    operation_fence_id: str
    authorization_envelope_id: str
    authorization_envelope_digest: str
    ownership_attempt_id: str
    ownership_fencing_token: int
    receipt_id: str


def build_workflow_transition_side_effect_authorization_effect(
    *,
    transition_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    runtime_id: str,
    ordinal: int,
    step_id: str,
    declared_operation: str,
    side_effect_class: str,
    operation_payload_digest: str,
    authorization_envelope_id: str,
    authorization_envelope_digest: str,
    ownership_attempt_id: str,
    ownership_fencing_token: int,
    planned_at: float,
) -> WorkflowTransitionEffect:
    """Build an acyclic, byte-deterministic side-effect authorization intent."""

    try:
        transition = _identity(transition_id, "transition_id")
        tenant = _identity(tenant_id, "tenant_id")
        workflow = _identity(workflow_id, "workflow_id")
        run = _identity(run_id, "run_id")
        runtime = _identity(runtime_id, "runtime_id")
        if runtime not in TRANSITION_RUNTIMES:
            raise WorkflowTransitionSideEffectAuthorizationError(
                "workflow_transition_side_effect_authorization_runtime_invalid"
            )
        position = _positive_integer(ordinal, "ordinal")
        step = _identity(step_id, "step_id")
        operation = _text(
            declared_operation,
            _MAX_OPERATION_CHARS,
            "declared_operation",
        )
        if not isinstance(side_effect_class, str) or side_effect_class not in _WRITE_CLASSES:
            raise WorkflowTransitionSideEffectAuthorizationError(
                "workflow_transition_side_effect_authorization_class_invalid"
            )
        payload_digest = _sha256(operation_payload_digest, "operation_payload_digest")
        envelope_id = _identity(authorization_envelope_id, "authorization_envelope_id")
        envelope_digest = _sha256(
            authorization_envelope_digest,
            "authorization_envelope_digest",
        )
        ownership_attempt = _identity(ownership_attempt_id, "ownership_attempt_id")
        ownership_fence = _positive_integer(
            ownership_fencing_token,
            "ownership_fencing_token",
        )
        timestamp = _positive_timestamp(planned_at, "planned_at")
        operation_id = operation_id_for(
            tenant_id=tenant,
            run_id=run,
            step_id=step,
            declared_operation=operation,
        )
        operation_intent_digest = workflow_transition_side_effect_operation_intent_digest(
            operation_id=operation_id,
            tenant_id=tenant,
            workflow_id=workflow,
            run_id=run,
            step_id=step,
            declared_operation=operation,
            side_effect_class=side_effect_class,
            operation_payload_digest=payload_digest,
        )
        operation_fence_id = workflow_transition_side_effect_operation_fence_id(
            operation_id=operation_id,
            operation_intent_digest=operation_intent_digest,
            ownership_attempt_id=ownership_attempt,
            ownership_fencing_token=ownership_fence,
            authorization_envelope_id=envelope_id,
            authorization_envelope_digest=envelope_digest,
        )
        effect_id = workflow_transition_effect_id(
            transition_id=transition,
            ordinal=position,
            kind=EFFECT_SIDE_EFFECT_AUTHORIZE,
            idempotency_key=operation_fence_id,
        )
        receipt_id = workflow_transition_side_effect_authorization_receipt_id(
            transition_id=transition,
            effect_id=effect_id,
        )
        effect = WorkflowTransitionEffect.build(
            transition_id=transition,
            ordinal=position,
            kind=EFFECT_SIDE_EFFECT_AUTHORIZE,
            idempotency_key=operation_fence_id,
            payload={
                "schema": WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_EFFECT_SCHEMA,
                "transition_id": transition,
                "effect_id": effect_id,
                "runtime_id": runtime,
                "tenant_id": tenant,
                "workflow_id": workflow,
                "run_id": run,
                "step_id": step,
                "effect_ordinal": position,
                "declared_operation": operation,
                "side_effect_class": side_effect_class,
                "operation_id": operation_id,
                "operation_payload_digest": payload_digest,
                "operation_intent_digest": operation_intent_digest,
                "operation_fence_id": operation_fence_id,
                "authorization_envelope_id": envelope_id,
                "authorization_envelope_digest": envelope_digest,
                "ownership_attempt_id": ownership_attempt,
                "ownership_fencing_token": ownership_fence,
                "receipt_id": receipt_id,
            },
            created_at=timestamp,
        )
        if effect.effect_id != effect_id:
            raise WorkflowTransitionSideEffectAuthorizationError(
                "workflow_transition_side_effect_authorization_effect_id_conflict"
            )
        _staged_authorization(effect=effect, runtime_id=runtime)
        return effect
    except WorkflowTransitionSideEffectAuthorizationError:
        raise
    except Exception as exc:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_payload_invalid"
        ) from exc


def assert_active_workflow_transition_side_effect_authorization_proof(
    proof: WorkflowTransitionEffectResourceProof | Mapping[str, Any],
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    reads: WorkflowTransitionSideEffectAuthorizationReadPort,
) -> WorkflowTransitionEffectResourceProof:
    intent = _authorization_intent(
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
    )
    receipt = _required_receipt(reads.observe_transition_authorization(intent))
    return assert_active_workflow_transition_effect_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        resource_kind=WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RESOURCE_KIND,
        resource_id=receipt.receipt_id,
        resource_revision=1,
        resource_digest=receipt.receipt_digest,
    )


def assert_durable_workflow_transition_side_effect_authorization_proof(
    proof: WorkflowTransitionEffectResourceProof | Mapping[str, Any],
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    reads: WorkflowTransitionSideEffectAuthorizationReadPort,
) -> WorkflowTransitionEffectResourceProof:
    intent = _authorization_intent(
        transition=transition,
        effect=effect,
        claim_generation=effect.applied_generation,
    )
    receipt = _required_receipt(reads.observe_transition_authorization(intent))
    return assert_durable_workflow_transition_effect_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        resource_kind=WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RESOURCE_KIND,
        resource_id=receipt.receipt_id,
        resource_revision=1,
        resource_digest=receipt.receipt_digest,
    )


@final
class WorkflowTransitionSideEffectAuthorizationObserver:
    """Read-only observer; it never heartbeats or mutates the ledger."""

    def __init__(
        self,
        *,
        runtime_id: str,
        reads: WorkflowTransitionSideEffectAuthorizationReadPort,
    ) -> None:
        if (
            not isinstance(runtime_id, str)
            or runtime_id not in TRANSITION_RUNTIMES
            or not isinstance(reads, WorkflowTransitionSideEffectAuthorizationReadPort)
        ):
            raise WorkflowTransitionSideEffectAuthorizationError(
                "workflow_transition_side_effect_authorization_observer_invalid"
            )
        self._runtime_id = runtime_id
        self._reads = reads

    def observe_or_adopt(
        self,
        observation: WorkflowTransitionEffectObservation,
        *,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectAlreadyApplied | EffectExecutable | EffectQuarantine:
        del heartbeat
        try:
            if type(observation) is not WorkflowTransitionEffectObservation:
                raise WorkflowTransitionSideEffectAuthorizationError(
                    "workflow_transition_side_effect_authorization_observation_invalid"
                )
            intent = _authorization_intent(
                transition=observation.transition,
                effect=observation.effect,
                claim_generation=observation.claim_generation,
                runtime_id=self._runtime_id,
            )
            snapshot = self._reads.observe_transition_authorization(intent)
            if snapshot.receipt is not None:
                return _already_applied(observation, snapshot.receipt)
            proof = _absence_proof(
                transition=observation.transition,
                effect=observation.effect,
                claim_generation=observation.claim_generation,
                snapshot=snapshot,
            )
            return EffectExecutable(proof.to_dict())
        except Exception:
            return EffectQuarantine("side_effect_authorization_observation_conflict")


@final
class WorkflowTransitionSideEffectAuthorizationExecutor:
    """Atomically authorize the ledger and append its immutable receipt."""

    def __init__(
        self,
        *,
        runtime_id: str,
        authority: WorkflowTransitionSideEffectAuthorizationAuthority,
    ) -> None:
        if (
            not isinstance(runtime_id, str)
            or runtime_id not in TRANSITION_RUNTIMES
            or not isinstance(
                authority,
                WorkflowTransitionSideEffectAuthorizationAuthority,
            )
        ):
            raise WorkflowTransitionSideEffectAuthorizationError(
                "workflow_transition_side_effect_authorization_executor_invalid"
            )
        self._runtime_id = runtime_id
        self._authority = authority

    def execute(
        self,
        attempt: WorkflowTransitionEffectAttempt,
        *,
        executable: EffectExecutable,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectApplied | EffectRetry | EffectQuarantine:
        del heartbeat
        try:
            if type(attempt) is not WorkflowTransitionEffectAttempt or type(executable) is not EffectExecutable:
                raise WorkflowTransitionSideEffectAuthorizationError(
                    "workflow_transition_side_effect_authorization_attempt_invalid"
                )
            intent = _authorization_intent(
                transition=attempt.transition,
                effect=attempt.effect,
                claim_generation=attempt.claim_generation,
                runtime_id=self._runtime_id,
            )
            before = self._authority.observe_transition_authorization(intent)
            if before.receipt is not None:
                return _applied(attempt, before.receipt)
            proof = WorkflowTransitionEffectAbsenceProof.from_mapping(executable.proof_payload)
            _assert_absence(
                proof,
                transition=attempt.transition,
                effect=attempt.effect,
                claim_generation=attempt.claim_generation,
                snapshot=before,
            )
        except Exception:
            return EffectQuarantine("side_effect_authorization_executable_proof_invalid")

        try:
            receipt = self._authority.authorize_transition_effect(
                intent,
                expected_observation_digest=before.observation_digest,
            )
        except Exception:
            return self._after_commit_exception(attempt=attempt, intent=intent)
        try:
            after = self._authority.observe_transition_authorization(intent)
            if after.receipt != receipt:
                return EffectQuarantine("side_effect_authorization_commit_missing")
            return _applied(attempt, receipt)
        except Exception:
            return EffectQuarantine("side_effect_authorization_commit_conflict")

    def _after_commit_exception(
        self,
        *,
        attempt: WorkflowTransitionEffectAttempt,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
    ) -> EffectApplied | EffectRetry | EffectQuarantine:
        try:
            after = self._authority.observe_transition_authorization(intent)
            if after.receipt is not None:
                return _applied(attempt, after.receipt)
            return EffectRetry("side_effect_authorization_unconfirmed")
        except Exception:
            return EffectQuarantine("side_effect_authorization_commit_conflict")


def _authorization_intent(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    runtime_id: str | None = None,
) -> WorkflowTransitionSideEffectAuthorizationIntent:
    staged = _staged_authorization(effect=effect, runtime_id=runtime_id)
    generation = _positive_integer(claim_generation, "claim_generation")
    if (
        not isinstance(transition, WorkflowTransition)
        or effect.transition_id != transition.transition_id
        or staged.transition_id != transition.transition_id
        or staged.runtime_id != transition.runtime_id
        or staged.tenant_id != transition.tenant_id
        or staged.workflow_id != transition.workflow_id
        or staged.run_id != transition.run_id
        or effect.created_at != transition.created_at
    ):
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_binding_invalid"
        )
    return WorkflowTransitionSideEffectAuthorizationIntent(
        receipt_id=staged.receipt_id,
        transition_id=staged.transition_id,
        effect_id=staged.effect_id,
        runtime_id=staged.runtime_id,
        tenant_id=staged.tenant_id,
        workflow_id=staged.workflow_id,
        run_id=staged.run_id,
        step_id=staged.step_id,
        effect_ordinal=staged.effect_ordinal,
        declared_operation=staged.declared_operation,
        side_effect_class=staged.side_effect_class,
        operation_id=staged.operation_id,
        operation_payload_digest=staged.operation_payload_digest,
        operation_intent_digest=staged.operation_intent_digest,
        operation_fence_id=staged.operation_fence_id,
        authorization_envelope_id=staged.authorization_envelope_id,
        authorization_envelope_digest=staged.authorization_envelope_digest,
        ownership_attempt_id=staged.ownership_attempt_id,
        ownership_fencing_token=staged.ownership_fencing_token,
        creator_claim_generation=generation,
        transition_request_fingerprint=transition.request_fingerprint,
        effect_payload_digest=effect.payload_digest,
        idempotency_key=effect.idempotency_key,
        planned_at=effect.created_at,
    )


def _staged_authorization(
    *,
    effect: WorkflowTransitionEffect,
    runtime_id: str | None = None,
) -> _StagedAuthorization:
    if not isinstance(effect, WorkflowTransitionEffect) or effect.kind != EFFECT_SIDE_EFFECT_AUTHORIZE:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_effect_invalid"
        )
    raw = effect.payload
    if not isinstance(raw, Mapping) or set(raw) != _EFFECT_PAYLOAD_FIELDS:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_payload_invalid"
        )
    try:
        staged = _StagedAuthorization(
            transition_id=_identity(raw["transition_id"], "transition_id"),
            effect_id=_identity(raw["effect_id"], "effect_id"),
            runtime_id=_identity(raw["runtime_id"], "runtime_id"),
            tenant_id=_identity(raw["tenant_id"], "tenant_id"),
            workflow_id=_identity(raw["workflow_id"], "workflow_id"),
            run_id=_identity(raw["run_id"], "run_id"),
            step_id=_identity(raw["step_id"], "step_id"),
            effect_ordinal=_positive_integer(raw["effect_ordinal"], "effect_ordinal"),
            declared_operation=_text(
                raw["declared_operation"],
                _MAX_OPERATION_CHARS,
                "declared_operation",
            ),
            side_effect_class=_write_class(raw["side_effect_class"]),
            operation_id=_identity(raw["operation_id"], "operation_id"),
            operation_payload_digest=_sha256(
                raw["operation_payload_digest"],
                "operation_payload_digest",
            ),
            operation_intent_digest=_sha256(
                raw["operation_intent_digest"],
                "operation_intent_digest",
            ),
            operation_fence_id=_identity(raw["operation_fence_id"], "operation_fence_id"),
            authorization_envelope_id=_identity(
                raw["authorization_envelope_id"],
                "authorization_envelope_id",
            ),
            authorization_envelope_digest=_sha256(
                raw["authorization_envelope_digest"],
                "authorization_envelope_digest",
            ),
            ownership_attempt_id=_identity(raw["ownership_attempt_id"], "ownership_attempt_id"),
            ownership_fencing_token=_positive_integer(
                raw["ownership_fencing_token"],
                "ownership_fencing_token",
            ),
            receipt_id=_identity(raw["receipt_id"], "receipt_id"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_payload_invalid"
        ) from exc
    expected_operation = operation_id_for(
        tenant_id=staged.tenant_id,
        run_id=staged.run_id,
        step_id=staged.step_id,
        declared_operation=staged.declared_operation,
    )
    expected_intent_digest = workflow_transition_side_effect_operation_intent_digest(
        operation_id=staged.operation_id,
        tenant_id=staged.tenant_id,
        workflow_id=staged.workflow_id,
        run_id=staged.run_id,
        step_id=staged.step_id,
        declared_operation=staged.declared_operation,
        side_effect_class=staged.side_effect_class,
        operation_payload_digest=staged.operation_payload_digest,
    )
    expected_fence_id = workflow_transition_side_effect_operation_fence_id(
        operation_id=staged.operation_id,
        operation_intent_digest=staged.operation_intent_digest,
        ownership_attempt_id=staged.ownership_attempt_id,
        ownership_fencing_token=staged.ownership_fencing_token,
        authorization_envelope_id=staged.authorization_envelope_id,
        authorization_envelope_digest=staged.authorization_envelope_digest,
    )
    expected_effect = workflow_transition_effect_id(
        transition_id=staged.transition_id,
        ordinal=staged.effect_ordinal,
        kind=EFFECT_SIDE_EFFECT_AUTHORIZE,
        idempotency_key=staged.operation_fence_id,
    )
    expected_receipt = workflow_transition_side_effect_authorization_receipt_id(
        transition_id=staged.transition_id,
        effect_id=staged.effect_id,
    )
    if (
        raw["schema"] != WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_EFFECT_SCHEMA
        or staged.runtime_id not in TRANSITION_RUNTIMES
        or (runtime_id is not None and staged.runtime_id != runtime_id)
        or staged.side_effect_class not in _WRITE_CLASSES
        or staged.operation_id != expected_operation
        or staged.operation_intent_digest != expected_intent_digest
        or staged.operation_fence_id != expected_fence_id
        or effect.idempotency_key != staged.operation_fence_id
        or staged.effect_id != effect.effect_id
        or staged.effect_id != expected_effect
        or staged.receipt_id != expected_receipt
        or staged.transition_id != effect.transition_id
        or staged.effect_ordinal != effect.ordinal
    ):
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_payload_binding_invalid"
        )
    return staged


def _absence_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    snapshot: WorkflowTransitionSideEffectAuthorizationObservation,
) -> WorkflowTransitionEffectAbsenceProof:
    return WorkflowTransitionEffectAbsenceProof(
        context=WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=claim_generation,
        ),
        resource_kind=WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_SLOT_KIND,
        resource_id=snapshot.intent.receipt_id,
        head_revision=(snapshot.ledger_record.revision if snapshot.ledger_record else 0),
        head_digest=snapshot.observation_digest,
    )


def _assert_absence(
    proof: WorkflowTransitionEffectAbsenceProof,
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    snapshot: WorkflowTransitionSideEffectAuthorizationObservation,
) -> None:
    if snapshot.receipt is not None:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_receipt_already_present"
        )
    assert_active_workflow_transition_effect_absence_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        resource_kind=WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_SLOT_KIND,
        resource_id=snapshot.intent.receipt_id,
        head_revision=(snapshot.ledger_record.revision if snapshot.ledger_record else 0),
        head_digest=snapshot.observation_digest,
    )


def _active_receipt_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
) -> WorkflowTransitionEffectResourceProof:
    if receipt.creator_claim_generation > claim_generation:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_generation_conflict"
        )
    return WorkflowTransitionEffectResourceProof(
        context=WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=claim_generation,
        ),
        resource_kind=WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RESOURCE_KIND,
        resource_id=receipt.receipt_id,
        resource_revision=1,
        resource_digest=receipt.receipt_digest,
    )


def _already_applied(
    observation: WorkflowTransitionEffectObservation,
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
) -> EffectAlreadyApplied:
    proof = _active_receipt_proof(
        transition=observation.transition,
        effect=observation.effect,
        claim_generation=observation.claim_generation,
        receipt=receipt,
    )
    return EffectAlreadyApplied(_receipt_result(receipt), proof.to_dict())


def _applied(
    attempt: WorkflowTransitionEffectAttempt,
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
) -> EffectApplied:
    proof = _active_receipt_proof(
        transition=attempt.transition,
        effect=attempt.effect,
        claim_generation=attempt.claim_generation,
        receipt=receipt,
    )
    return EffectApplied(_receipt_result(receipt), proof.to_dict())


def _receipt_result(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
) -> dict[str, object]:
    return {
        "schema": WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RESULT_SCHEMA,
        "receipt": receipt.to_dict(),
    }


def _required_receipt(
    observation: WorkflowTransitionSideEffectAuthorizationObservation,
) -> WorkflowTransitionSideEffectAuthorizationReceipt:
    if observation.receipt is None:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_receipt_missing"
        )
    return observation.receipt


def workflow_transition_side_effect_authorization_receipt_from_result(
    result: Mapping[str, Any],
) -> WorkflowTransitionSideEffectAuthorizationReceipt:
    if not isinstance(result, Mapping) or set(result) != _RESULT_FIELDS:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_result_invalid"
        )
    if result["schema"] != WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RESULT_SCHEMA:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_result_schema_unsupported"
        )
    try:
        return WorkflowTransitionSideEffectAuthorizationReceipt.from_mapping(result["receipt"])
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_result_invalid"
        ) from exc


def _identity(value: object, reason: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise WorkflowTransitionSideEffectAuthorizationError(
            f"workflow_transition_side_effect_authorization_{reason}_invalid"
        )
    return value


def _text(value: object, maximum: int, reason: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum or "\x00" in value:
        raise WorkflowTransitionSideEffectAuthorizationError(
            f"workflow_transition_side_effect_authorization_{reason}_invalid"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkflowTransitionSideEffectAuthorizationError(
            f"workflow_transition_side_effect_authorization_{reason}_invalid"
        ) from exc
    return value


def _sha256(value: object, reason: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise WorkflowTransitionSideEffectAuthorizationError(
            f"workflow_transition_side_effect_authorization_{reason}_invalid"
        )
    return value


def _write_class(value: object) -> str:
    if not isinstance(value, str) or value not in _WRITE_CLASSES:
        raise WorkflowTransitionSideEffectAuthorizationError(
            "workflow_transition_side_effect_authorization_class_invalid"
        )
    return value


def _positive_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > _MAX_COUNTER:
        raise WorkflowTransitionSideEffectAuthorizationError(
            f"workflow_transition_side_effect_authorization_{reason}_invalid"
        )
    return value


def _positive_timestamp(value: object, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise WorkflowTransitionSideEffectAuthorizationError(
            f"workflow_transition_side_effect_authorization_{reason}_invalid"
        )
    timestamp = float(value)
    if timestamp != timestamp or timestamp in {float("inf"), float("-inf")}:
        raise WorkflowTransitionSideEffectAuthorizationError(
            f"workflow_transition_side_effect_authorization_{reason}_invalid"
        )
    return timestamp


__all__ = [
    "WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_EFFECT_SCHEMA",
    "WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RESOURCE_KIND",
    "WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RESULT_SCHEMA",
    "WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_SLOT_KIND",
    "WorkflowTransitionSideEffectAuthorizationAuthority",
    "WorkflowTransitionSideEffectAuthorizationError",
    "WorkflowTransitionSideEffectAuthorizationExecutor",
    "WorkflowTransitionSideEffectAuthorizationObserver",
    "assert_active_workflow_transition_side_effect_authorization_proof",
    "assert_durable_workflow_transition_side_effect_authorization_proof",
    "build_workflow_transition_side_effect_authorization_effect",
    "workflow_transition_side_effect_authorization_receipt_from_result",
]
