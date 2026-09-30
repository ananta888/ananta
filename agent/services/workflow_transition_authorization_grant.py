"""Unwired Ed25519 authorization-grant workflow-transition effect.

Historical integrity and current authority are intentionally separate here.
The retained-key verifier proves only signature mathematics for an issuance
that already exists.  It ignores current key/contract revocation and expiry
and therefore MUST NOT be used to authorize a new grant or a provider call.
The mutating path always uses a distinct revocation-aware verifier and an
injected clock immediately before the Hub grant commit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrant,
    WorkflowAuthorizationGrantConflict,
    WorkflowAuthorizationGrantReadPort,
    WorkflowTransitionAuthorizationGrantCommitPort,
    workflow_authorization_grant_digest,
)
from agent.services.workflow_runtime.security import (
    RuntimeAuthorizationEnvelope,
    SignatureSigningKeyRingPort,
    SignatureVerificationKeyRingPort,
)
from agent.services.workflow_transition_authorization_grant_evidence import (
    _absence_proof,
    _absence_values,
    _issuance,
    _read_grant,
    _resource_proof,
    _result,
    assert_active_workflow_transition_authorization_grant_proof,
    assert_current_workflow_transition_authorization_grant_validity,
    assert_durable_workflow_transition_authorization_grant_proof,
)
from agent.services.workflow_transition_authorization_grant_intent import (
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ABSENCE_SCHEMA,
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_EFFECT_SCHEMA,
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ISSUANCE_SCHEMA,
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESOURCE_KIND,
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESULT_SCHEMA,
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_SLOT_KIND,
    RetainedEd25519AuthorizationEnvelopeIntegrityVerifier,
    WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
    _GrantIntent,
    _intent_from_effect,
    _strict_envelope,
    _verify_current,
    workflow_transition_authorization_grant_envelope_id,
    workflow_transition_authorization_grant_idempotency_key,
    workflow_transition_authorization_grant_nonce,
)
from agent.services.workflow_transition_authorization_grant_validation import (
    _MAX_EFFECT_BYTES,
    WorkflowTransitionAuthorizationGrantError,
    _assert_provider_attempt_budget,
    _bounded_mapping,
    _budget_mapping,
    _clock_value,
    _identity,
    _positive_integer,
    _positive_timestamp,
    _provider_attempt_sequence,
    _provider_binding_sequence,
    _scope_identity,
    _sha256,
    _text,
    _text_sequence,
    _ttl,
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
    assert_active_workflow_transition_effect_absence_proof_binding,
    workflow_transition_effect_resource_digest,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_AUTHORIZATION_GRANT,
    TRANSITION_RUNTIMES,
    WorkflowTransition,
    WorkflowTransitionEffect,
    workflow_transition_effect_id,
)
from ananta_contracts.runtime_authorization_crypto import ED25519_ALGORITHM


@runtime_checkable
class WorkflowTransitionAuthorizationGrantAuthority(
    WorkflowAuthorizationGrantReadPort,
    WorkflowTransitionAuthorizationGrantCommitPort,
    Protocol,
):
    """Exact read plus deterministic grant commit; revoke is absent."""


def build_workflow_transition_authorization_grant_effect(
    *,
    signing_key_ring: SignatureSigningKeyRingPort,
    transition_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    runtime_id: str,
    ordinal: int,
    step_id: str,
    plan_hash: str,
    policy_version: str,
    allowed_tools: Sequence[str] = (),
    allowed_artifacts: Sequence[str] = (),
    allowed_provider_bindings: Sequence[Mapping[str, Any]] = (),
    provider_attempt_plan: Sequence[Mapping[str, Any]] = (),
    budgets: Mapping[str, int | float] | None = None,
    ttl_seconds: float,
    planned_at: float,
) -> WorkflowTransitionEffect:
    """Sign and stage one byte-deterministic Ed25519 grant intent."""

    try:
        transition = _identity(transition_id, "transition_id")
        tenant = _scope_identity(tenant_id, "tenant_id")
        workflow = _scope_identity(workflow_id, "workflow_id")
        run = _scope_identity(run_id, "run_id")
        runtime = _identity(runtime_id, "runtime_id")
        if runtime not in TRANSITION_RUNTIMES:
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_runtime_invalid")
        position = _positive_integer(ordinal, "ordinal")
        step = _scope_identity(step_id, "step_id")
        plan = _sha256(plan_hash, "plan_hash")
        policy = _text(policy_version, 256, "policy_version")
        ttl = _ttl(ttl_seconds)
        issued_at = _positive_timestamp(planned_at, "planned_at")
        if (
            not hasattr(signing_key_ring, "signature_algorithm")
            or signing_key_ring.signature_algorithm != ED25519_ALGORITHM
        ):
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_ed25519_required")
        tools = _text_sequence(allowed_tools, reason="allowed_tools")
        artifacts = _text_sequence(
            allowed_artifacts,
            reason="allowed_artifacts",
        )
        provider_bindings = _provider_binding_sequence(
            allowed_provider_bindings,
        )
        attempt_plan = _provider_attempt_sequence(
            provider_attempt_plan,
        )
        budget_values = _budget_mapping(
            {} if budgets is None else budgets,
        )
        _assert_provider_attempt_budget(
            budgets=budget_values,
            attempt_plan=attempt_plan,
        )
        envelope = RuntimeAuthorizationEnvelope.issue(
            key_ring=signing_key_ring,
            tenant_id=tenant,
            workflow_id=workflow,
            run_id=run,
            step_id=step,
            plan_hash=plan,
            policy_version=policy,
            allowed_tools=tools,
            allowed_artifacts=artifacts,
            allowed_provider_bindings=provider_bindings,
            provider_attempt_plan=attempt_plan,
            budgets=budget_values,
            ttl_seconds=ttl,
            now=issued_at,
            envelope_id=workflow_transition_authorization_grant_envelope_id(
                transition_id=transition,
                ordinal=position,
            ),
            nonce=workflow_transition_authorization_grant_nonce(
                transition_id=transition,
                ordinal=position,
            ),
        )
        envelope = _strict_envelope(envelope.to_dict())
        envelope.verify(
            key_ring=signing_key_ring,
            tenant_id=tenant,
            workflow_id=workflow,
            run_id=run,
            step_id=step,
            plan_hash=plan,
            policy_version=policy,
            now=issued_at,
        )
        envelope_raw = envelope.to_dict()
        envelope_digest = workflow_authorization_grant_digest(envelope)
        idempotency_key = workflow_transition_authorization_grant_idempotency_key(
            envelope_id=envelope.envelope_id,
            envelope_digest=envelope_digest,
        )
        effect_id = workflow_transition_effect_id(
            transition_id=transition,
            ordinal=position,
            kind=EFFECT_AUTHORIZATION_GRANT,
            idempotency_key=idempotency_key,
        )
        payload = {
            "schema": WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_EFFECT_SCHEMA,
            "transition_id": transition,
            "effect_id": effect_id,
            "runtime_id": runtime,
            "effect_ordinal": position,
            "signature_algorithm": ED25519_ALGORITHM,
            "ttl_seconds": ttl,
            "envelope_digest": envelope_digest,
            "envelope": envelope_raw,
        }
        _bounded_mapping(payload, maximum=_MAX_EFFECT_BYTES, reason="effect_payload")
        effect = WorkflowTransitionEffect.build(
            transition_id=transition,
            ordinal=position,
            kind=EFFECT_AUTHORIZATION_GRANT,
            idempotency_key=idempotency_key,
            payload=payload,
            created_at=issued_at,
        )
        _intent_from_effect(effect, transition=None)
        return effect
    except WorkflowTransitionAuthorizationGrantError:
        raise
    except Exception as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_payload_invalid"
        ) from exc


@final
class WorkflowTransitionAuthorizationGrantObserver:
    __slots__ = ("_clock", "_current_verifier", "_historical", "_reads")

    def __init__(
        self,
        *,
        reads: WorkflowAuthorizationGrantReadPort,
        historical_integrity: WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
        current_verifier: SignatureVerificationKeyRingPort,
        clock: Callable[[], float],
    ) -> None:
        if (
            not isinstance(reads, WorkflowAuthorizationGrantReadPort)
            or not isinstance(
                historical_integrity,
                WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
            )
            or historical_integrity.signature_algorithm != ED25519_ALGORITHM
            or not callable(clock)
            or getattr(current_verifier, "signature_algorithm", None) != ED25519_ALGORITHM
        ):
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_observer_invalid")
        self._reads = reads
        self._historical = historical_integrity
        self._current_verifier = current_verifier
        self._clock = clock

    def observe_or_adopt(
        self,
        observation: WorkflowTransitionEffectObservation,
        *,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectAlreadyApplied | EffectExecutable | EffectRetry | EffectQuarantine:
        del heartbeat
        try:
            intent = _intent_from_observation(observation)
        except Exception:
            return EffectQuarantine("authorization_grant_observation_invalid")
        try:
            grant = _read_grant(self._reads, intent)
        except WorkflowAuthorizationGrantConflict:
            return EffectQuarantine("authorization_grant_observation_conflict")
        except Exception:
            return EffectRetry("authorization_grant_observation_retry")
        try:
            if grant is not None:
                return _already_applied(
                    transition=observation.transition,
                    effect=observation.effect,
                    claim_generation=observation.claim_generation,
                    intent=intent,
                    grant=grant,
                    historical=self._historical,
                )
            try:
                _verify_current(
                    intent,
                    verifier=self._current_verifier,
                    now=_clock_value(self._clock),
                )
            except Exception:
                return self._resolve_current_failure(observation, intent)
            return EffectExecutable(
                proof_payload=_absence_proof(
                    transition=observation.transition,
                    effect=observation.effect,
                    claim_generation=observation.claim_generation,
                    intent=intent,
                ).to_dict()
            )
        except Exception:
            return EffectQuarantine("authorization_grant_observation_invalid")

    def _resolve_current_failure(
        self,
        observation: WorkflowTransitionEffectObservation,
        intent: _GrantIntent,
    ) -> EffectAlreadyApplied | EffectRetry | EffectQuarantine:
        try:
            grant = _read_grant(self._reads, intent)
        except WorkflowAuthorizationGrantConflict:
            return EffectQuarantine("authorization_grant_observation_conflict")
        except Exception:
            return EffectRetry("authorization_grant_observation_retry")
        if grant is None:
            return EffectQuarantine("authorization_grant_current_authority_invalid")
        try:
            return _already_applied(
                transition=observation.transition,
                effect=observation.effect,
                claim_generation=observation.claim_generation,
                intent=intent,
                grant=grant,
                historical=self._historical,
            )
        except Exception:
            return EffectQuarantine("authorization_grant_observation_conflict")


@final
class WorkflowTransitionAuthorizationGrantExecutor:
    __slots__ = ("_authority", "_clock", "_current_verifier", "_historical")

    def __init__(
        self,
        *,
        authority: WorkflowTransitionAuthorizationGrantAuthority,
        historical_integrity: WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
        current_verifier: SignatureVerificationKeyRingPort,
        clock: Callable[[], float],
    ) -> None:
        if (
            not isinstance(authority, WorkflowTransitionAuthorizationGrantAuthority)
            or not isinstance(
                historical_integrity,
                WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
            )
            or historical_integrity.signature_algorithm != ED25519_ALGORITHM
            or not callable(clock)
            or getattr(current_verifier, "signature_algorithm", None) != ED25519_ALGORITHM
        ):
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_executor_invalid")
        self._authority = authority
        self._historical = historical_integrity
        self._current_verifier = current_verifier
        self._clock = clock

    def execute(
        self,
        attempt: WorkflowTransitionEffectAttempt,
        *,
        executable: EffectExecutable,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectApplied | EffectRetry | EffectQuarantine:
        del heartbeat
        try:
            intent = _intent_from_attempt(attempt)
        except Exception:
            return EffectQuarantine("authorization_grant_execution_invalid")
        try:
            existing = _read_grant(self._authority, intent)
        except WorkflowAuthorizationGrantConflict:
            return EffectQuarantine("authorization_grant_execution_conflict")
        except Exception:
            return EffectRetry("authorization_grant_execution_retry")
        try:
            if existing is not None:
                return _applied(
                    transition=attempt.transition,
                    effect=attempt.effect,
                    claim_generation=attempt.claim_generation,
                    intent=intent,
                    grant=existing,
                    historical=self._historical,
                )
            expected_absence = _absence_values(intent)
            assert_active_workflow_transition_effect_absence_proof_binding(
                executable.proof_payload,
                transition=attempt.transition,
                effect=attempt.effect,
                claim_generation=attempt.claim_generation,
                resource_kind=WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_SLOT_KIND,
                resource_id=intent.envelope.envelope_id,
                head_revision=0,
                head_digest=workflow_transition_effect_resource_digest(expected_absence),
            )
            try:
                _verify_current(
                    intent,
                    verifier=self._current_verifier,
                    now=_clock_value(self._clock),
                )
            except Exception:
                return self._resolve_current_failure(attempt, intent)
            try:
                self._authority.commit_transition_grant(
                    intent.envelope,
                    recorded_at=attempt.effect.created_at,
                )
            except Exception:
                return self._resolve_commit_exception(attempt, intent)
            try:
                stored = _read_grant(self._authority, intent)
            except WorkflowAuthorizationGrantConflict:
                return EffectQuarantine("authorization_grant_commit_conflict")
            except Exception:
                return EffectRetry("authorization_grant_commit_read_retry")
            if stored is None:
                return EffectQuarantine("authorization_grant_commit_invalid")
            return _applied(
                transition=attempt.transition,
                effect=attempt.effect,
                claim_generation=attempt.claim_generation,
                intent=intent,
                grant=stored,
                historical=self._historical,
            )
        except Exception:
            return EffectQuarantine("authorization_grant_execution_invalid")

    def _resolve_current_failure(
        self,
        attempt: WorkflowTransitionEffectAttempt,
        intent: _GrantIntent,
    ) -> EffectApplied | EffectRetry | EffectQuarantine:
        try:
            stored = _read_grant(self._authority, intent)
        except WorkflowAuthorizationGrantConflict:
            return EffectQuarantine("authorization_grant_execution_conflict")
        except Exception:
            return EffectRetry("authorization_grant_execution_retry")
        if stored is None:
            return EffectQuarantine("authorization_grant_current_authority_invalid")
        try:
            return _applied(
                transition=attempt.transition,
                effect=attempt.effect,
                claim_generation=attempt.claim_generation,
                intent=intent,
                grant=stored,
                historical=self._historical,
            )
        except Exception:
            return EffectQuarantine("authorization_grant_execution_conflict")

    def _resolve_commit_exception(
        self,
        attempt: WorkflowTransitionEffectAttempt,
        intent: _GrantIntent,
    ) -> EffectApplied | EffectRetry | EffectQuarantine:
        try:
            stored = _read_grant(self._authority, intent)
        except WorkflowAuthorizationGrantConflict:
            return EffectQuarantine("authorization_grant_commit_conflict")
        except Exception:
            return EffectRetry("authorization_grant_commit_read_retry")
        if stored is None:
            return EffectRetry("authorization_grant_commit_retry")
        try:
            return _applied(
                transition=attempt.transition,
                effect=attempt.effect,
                claim_generation=attempt.claim_generation,
                intent=intent,
                grant=stored,
                historical=self._historical,
            )
        except Exception:
            return EffectQuarantine("authorization_grant_commit_conflict")


def _intent_from_observation(
    observation: WorkflowTransitionEffectObservation,
) -> _GrantIntent:
    if not isinstance(observation, WorkflowTransitionEffectObservation):
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_observation_invalid")
    return _intent_from_effect(observation.effect, transition=observation.transition)


def _intent_from_attempt(attempt: WorkflowTransitionEffectAttempt) -> _GrantIntent:
    if not isinstance(attempt, WorkflowTransitionEffectAttempt):
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_attempt_invalid")
    return _intent_from_effect(attempt.effect, transition=attempt.transition)


def _already_applied(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    intent: _GrantIntent,
    grant: WorkflowAuthorizationGrant,
    historical: WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
) -> EffectAlreadyApplied:
    issuance = _issuance(intent, grant, historical)
    proof = _resource_proof(
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        intent=intent,
        issuance=issuance,
    )
    return EffectAlreadyApplied(
        result_payload=_result(issuance),
        proof_payload=proof.to_dict(),
    )


def _applied(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    intent: _GrantIntent,
    grant: WorkflowAuthorizationGrant,
    historical: WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
) -> EffectApplied:
    issuance = _issuance(intent, grant, historical)
    proof = _resource_proof(
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        intent=intent,
        issuance=issuance,
    )
    return EffectApplied(
        result_payload=_result(issuance),
        proof_payload=proof.to_dict(),
    )


__all__ = [
    "RetainedEd25519AuthorizationEnvelopeIntegrityVerifier",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ABSENCE_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_EFFECT_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ISSUANCE_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESOURCE_KIND",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESULT_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_SLOT_KIND",
    "WorkflowAuthorizationEnvelopeHistoricalIntegrityPort",
    "WorkflowTransitionAuthorizationGrantAuthority",
    "WorkflowTransitionAuthorizationGrantError",
    "WorkflowTransitionAuthorizationGrantExecutor",
    "WorkflowTransitionAuthorizationGrantObserver",
    "assert_active_workflow_transition_authorization_grant_proof",
    "assert_current_workflow_transition_authorization_grant_validity",
    "assert_durable_workflow_transition_authorization_grant_proof",
    "build_workflow_transition_authorization_grant_effect",
    "workflow_transition_authorization_grant_envelope_id",
    "workflow_transition_authorization_grant_idempotency_key",
    "workflow_transition_authorization_grant_nonce",
]
