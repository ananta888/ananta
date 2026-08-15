"""Unwired Ed25519 authorization-grant workflow-transition effect.

Historical integrity and current authority are intentionally separate here.
The retained-key verifier proves only signature mathematics for an issuance
that already exists.  It ignores current key/contract revocation and expiry
and therefore MUST NOT be used to authorize a new grant or a provider call.
The mutating path always uses a distinct revocation-aware verifier and an
injected clock immediately before the Hub grant commit.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrant,
    WorkflowAuthorizationGrantConflict,
    WorkflowAuthorizationGrantReadPort,
    WorkflowTransitionAuthorizationGrantCommitPort,
    assert_workflow_authorization_grant_projection,
    workflow_authorization_grant_digest,
)
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.security import (
    AUTHORIZATION_ENVELOPE_SCHEMA,
    RuntimeAuthorizationEnvelope,
    SignatureSigningKeyRingPort,
    SignatureVerificationKeyRingPort,
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
    WorkflowTransitionEffectScalars,
    assert_active_workflow_transition_effect_absence_proof_binding,
    assert_active_workflow_transition_effect_proof_binding,
    assert_durable_workflow_transition_effect_proof_binding,
    workflow_transition_effect_resource_digest,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_AUTHORIZATION_GRANT,
    TRANSITION_RUNTIMES,
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_id,
    workflow_transition_effect_stage_attempt_count,
)
from ananta_contracts.provider_execution import (
    ProviderBindingAuthorization,
    ProviderProfileAttemptPlanEntry,
)
from ananta_contracts.runtime_authorization_crypto import (
    ED25519_ALGORITHM,
    Ed25519VerificationKeyRing,
)

WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_EFFECT_SCHEMA = "ananta.workflow_transition_authorization_grant_effect.v1"
WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESULT_SCHEMA = "ananta.workflow_transition_authorization_grant_result.v1"
WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ISSUANCE_SCHEMA = "ananta.workflow_transition_authorization_grant_issuance.v1"
WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ABSENCE_SCHEMA = "ananta.workflow_transition_authorization_grant_absence.v1"
WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESOURCE_KIND = "workflow_authorization_grant_issuance"
WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_SLOT_KIND = "workflow_authorization_grant_slot"

_EFFECT_FIELDS = frozenset(
    {
        "schema",
        "transition_id",
        "effect_id",
        "runtime_id",
        "effect_ordinal",
        "signature_algorithm",
        "ttl_seconds",
        "envelope_digest",
        "envelope",
    }
)
_ENVELOPE_BASE_FIELDS = frozenset(
    {
        "schema",
        "envelope_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "plan_hash",
        "policy_version",
        "allowed_tools",
        "allowed_artifacts",
        "budgets",
        "issued_at",
        "expires_at",
        "nonce",
        "key_id",
        "signature",
    }
)
_ENVELOPE_OPTIONAL_FIELDS = frozenset({"allowed_provider_bindings", "provider_attempt_plan"})
_RESULT_FIELDS = frozenset({"schema", "issuance"})
_ISSUANCE_FIELDS = frozenset(
    {
        "schema",
        "signature_algorithm",
        "envelope_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "plan_hash",
        "policy_version",
        "envelope_digest",
        "issued_revision",
        "issued_at",
        "expires_at",
    }
)
_MAX_ENVELOPE_BYTES = 240_000
_MAX_EFFECT_BYTES = 260_000
_MAX_JSON_DEPTH = 32
_MAX_JSON_ITEMS = 10_000
_MAX_TEXT_BYTES = 240_000
_MAX_TTL_SECONDS = 31_536_000.0
_MAX_COUNTER = 2**63 - 1


class WorkflowTransitionAuthorizationGrantError(ValueError):
    """Stable fail-closed staged-grant or semantic-proof error."""


_SCALARS = WorkflowTransitionEffectScalars(
    error=WorkflowTransitionAuthorizationGrantError,
    prefix="workflow_transition_authorization_grant",
)


@runtime_checkable
class WorkflowTransitionAuthorizationGrantAuthority(
    WorkflowAuthorizationGrantReadPort,
    WorkflowTransitionAuthorizationGrantCommitPort,
    Protocol,
):
    """Exact read plus deterministic grant commit; revoke is absent."""


@runtime_checkable
class WorkflowAuthorizationEnvelopeHistoricalIntegrityPort(Protocol):
    """Math-only verification of an issuance; never current authority."""

    @property
    def signature_algorithm(self) -> str: ...

    def verify_issued(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        plan_hash: str,
        policy_version: str,
    ) -> None: ...


@final
class RetainedEd25519AuthorizationEnvelopeIntegrityVerifier:
    """Verify historical signature mathematics using retained public keys.

    The constructed key ring deliberately has no revocation state.  This class
    does not accept a clock and verifies at the signed ``issued_at`` instant.
    Missing retained keys and invalid signatures still fail closed.
    """

    __slots__ = ("_key_ring",)

    def __init__(self, retained_public_keys: Mapping[str, str | bytes]) -> None:
        if not isinstance(retained_public_keys, Mapping):
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_retained_keys_invalid"
            )
        try:
            self._key_ring = Ed25519VerificationKeyRing(dict(retained_public_keys))
        except Exception as exc:
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_retained_keys_invalid"
            ) from exc

    @property
    def signature_algorithm(self) -> str:
        return ED25519_ALGORITHM

    def verify_issued(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        plan_hash: str,
        policy_version: str,
    ) -> None:
        try:
            envelope.verify(
                key_ring=self._key_ring,
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                plan_hash=plan_hash,
                policy_version=policy_version,
                now=envelope.issued_at,
            )
        except Exception as exc:
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_historical_integrity_invalid"
            ) from exc


@final
@dataclass(frozen=True, slots=True)
class _GrantIntent:
    transition_id: str
    effect_id: str
    runtime_id: str
    effect_ordinal: int
    signature_algorithm: str
    ttl_seconds: float
    envelope_digest: str
    envelope: RuntimeAuthorizationEnvelope


@dataclass(slots=True)
class _JsonBudget:
    remaining_items: int = _MAX_JSON_ITEMS
    remaining_text_bytes: int = _MAX_TEXT_BYTES

    def item(self) -> None:
        self.remaining_items -= 1
        if self.remaining_items < 0:
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")

    def text(self, value: str) -> str:
        self.item()
        try:
            self.remaining_text_bytes -= len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_json_invalid"
            ) from exc
        if self.remaining_text_bytes < 0:
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_too_large")
        return value


def workflow_transition_authorization_grant_envelope_id(
    *,
    transition_id: str,
    ordinal: int,
) -> str:
    return _opaque_id(
        "wftag",
        "workflow-transition-authorization-grant-envelope.v1",
        _identity(transition_id, "transition_id"),
        str(_positive_integer(ordinal, "ordinal")),
    )


def workflow_transition_authorization_grant_nonce(
    *,
    transition_id: str,
    ordinal: int,
) -> str:
    return _opaque_id(
        "wftagn",
        "workflow-transition-authorization-grant-nonce.v1",
        _identity(transition_id, "transition_id"),
        str(_positive_integer(ordinal, "ordinal")),
    )


def workflow_transition_authorization_grant_idempotency_key(
    *,
    envelope_id: str,
    envelope_digest: str,
) -> str:
    return _opaque_id(
        "wftagi",
        "workflow-transition-authorization-grant-idempotency.v1",
        _identity(envelope_id, "envelope_id"),
        _sha256(envelope_digest, "envelope_digest"),
    )


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


def assert_active_workflow_transition_authorization_grant_proof(
    proof: WorkflowTransitionEffectResourceProof | Mapping[str, Any],
    *,
    result_payload: Mapping[str, Any],
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    reads: WorkflowAuthorizationGrantReadPort,
    historical_integrity: WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
) -> WorkflowTransitionEffectResourceProof:
    intent = _intent_from_effect(effect, transition=transition)
    grant = _required_grant(reads, intent)
    issuance = _issuance(intent, grant, historical_integrity)
    _assert_result(result_payload, issuance)
    return assert_active_workflow_transition_effect_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        resource_kind=WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESOURCE_KIND,
        resource_id=intent.envelope.envelope_id,
        resource_revision=1,
        resource_digest=workflow_transition_effect_resource_digest(issuance),
    )


def assert_durable_workflow_transition_authorization_grant_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    reads: WorkflowAuthorizationGrantReadPort,
    historical_integrity: WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
) -> WorkflowTransitionEffectResourceProof:
    proof, result_payload = _persisted_evidence(effect)
    intent = _intent_from_effect(effect, transition=transition)
    grant = _required_grant(reads, intent)
    issuance = _issuance(intent, grant, historical_integrity)
    _assert_result(result_payload, issuance)
    return assert_durable_workflow_transition_effect_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        resource_kind=WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESOURCE_KIND,
        resource_id=intent.envelope.envelope_id,
        resource_revision=1,
        resource_digest=workflow_transition_effect_resource_digest(issuance),
    )


def assert_current_workflow_transition_authorization_grant_validity(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    reads: WorkflowAuthorizationGrantReadPort,
    historical_integrity: WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
    current_verifier: SignatureVerificationKeyRingPort,
    clock: Callable[[], float],
) -> None:
    """Assert point-in-time validity; this is not a downstream capability.

    The read and verification are intentionally non-mutating and non-atomic.
    A later live slice must combine this check with ledger authorization in one
    Hub-owned authority boundary; callers must not treat this snapshot as a
    provider-call capability.
    """

    proof, result_payload = _persisted_evidence(effect)
    intent = _intent_from_effect(effect, transition=transition)
    grant = _required_grant(reads, intent)
    issuance = _issuance(intent, grant, historical_integrity)
    _assert_result(result_payload, issuance)
    assert_durable_workflow_transition_effect_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        resource_kind=WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESOURCE_KIND,
        resource_id=intent.envelope.envelope_id,
        resource_revision=1,
        resource_digest=workflow_transition_effect_resource_digest(issuance),
    )
    now = _clock_value(clock)
    _verify_current(intent, verifier=current_verifier, now=now)
    if grant.status != "active" or grant.revision != 1 or grant.revocation_reason or grant.expires_at <= now:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_current_invalid")
    return None


def _persisted_evidence(
    effect: WorkflowTransitionEffect,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    try:
        workflow_transition_effect_stage_attempt_count(effect.result_payload)
        envelope = thaw_json(effect.result_payload)
        result = envelope["effect_result"]
        proof = envelope["effect_proof"]
        if not isinstance(result, Mapping) or not isinstance(proof, Mapping):
            raise TypeError("persisted evidence is not a mapping")
        return proof, result
    except Exception as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_persisted_proof_invalid"
        ) from exc


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


def _intent_from_effect(
    effect: WorkflowTransitionEffect,
    *,
    transition: WorkflowTransition | None,
) -> _GrantIntent:
    if not isinstance(effect, WorkflowTransitionEffect) or effect.kind != EFFECT_AUTHORIZATION_GRANT:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_effect_invalid")
    payload = _bounded_mapping(
        effect.payload,
        maximum=_MAX_EFFECT_BYTES,
        reason="effect_payload",
    )
    if set(payload) != _EFFECT_FIELDS:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_effect_payload_invalid"
        )
    if payload["schema"] != WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_EFFECT_SCHEMA:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_effect_schema_unsupported"
        )
    position = _positive_integer(payload["effect_ordinal"], "effect_ordinal")
    runtime = _identity(payload["runtime_id"], "runtime_id")
    if runtime not in TRANSITION_RUNTIMES:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_runtime_invalid")
    if payload["signature_algorithm"] != ED25519_ALGORITHM:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_ed25519_required")
    if type(payload["ttl_seconds"]) is not float:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_ttl_seconds_invalid")
    ttl = _ttl(payload["ttl_seconds"])
    envelope = _strict_envelope(payload["envelope"])
    envelope_digest = _sha256(payload["envelope_digest"], "envelope_digest")
    transition_id = _identity(payload["transition_id"], "transition_id")
    effect_id = _identity(payload["effect_id"], "effect_id")
    expected_envelope_id = workflow_transition_authorization_grant_envelope_id(
        transition_id=transition_id,
        ordinal=position,
    )
    expected_nonce = workflow_transition_authorization_grant_nonce(
        transition_id=transition_id,
        ordinal=position,
    )
    expected_idempotency = workflow_transition_authorization_grant_idempotency_key(
        envelope_id=envelope.envelope_id,
        envelope_digest=envelope_digest,
    )
    if (
        effect.transition_id != transition_id
        or effect.effect_id != effect_id
        or effect.ordinal != position
        or effect.idempotency_key != expected_idempotency
        or envelope.envelope_id != expected_envelope_id
        or envelope.nonce != expected_nonce
        or envelope.issued_at != effect.created_at
        or envelope.expires_at != envelope.issued_at + ttl
        or workflow_authorization_grant_digest(envelope) != envelope_digest
    ):
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_effect_binding_invalid"
        )
    if transition is not None and (
        transition.transition_id != transition_id
        or transition.runtime_id != runtime
        or transition.tenant_id != envelope.tenant_id
        or transition.workflow_id != envelope.workflow_id
        or transition.run_id != envelope.run_id
        or transition.created_at != envelope.issued_at
    ):
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_transition_binding_invalid"
        )
    return _GrantIntent(
        transition_id=transition_id,
        effect_id=effect_id,
        runtime_id=runtime,
        effect_ordinal=position,
        signature_algorithm=ED25519_ALGORITHM,
        ttl_seconds=ttl,
        envelope_digest=envelope_digest,
        envelope=envelope,
    )


def _strict_envelope(raw: Any) -> RuntimeAuthorizationEnvelope:
    copied = _copy_json(raw, depth=0, budget=_JsonBudget())
    if not isinstance(copied, dict):
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_envelope_invalid")
    fields = set(copied)
    if (
        not _ENVELOPE_BASE_FIELDS.issubset(fields)
        or fields - (_ENVELOPE_BASE_FIELDS | _ENVELOPE_OPTIONAL_FIELDS)
        or copied["schema"] != AUTHORIZATION_ENVELOPE_SCHEMA
    ):
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_envelope_invalid")
    for field in (
        "envelope_id",
        "plan_hash",
        "policy_version",
        "nonce",
        "key_id",
        "signature",
    ):
        _text(copied[field], 512, f"envelope_{field}")
    _text(copied["policy_version"], 256, "envelope_policy_version")
    for field in ("tenant_id", "workflow_id", "run_id", "step_id"):
        _scope_identity(copied[field], f"envelope_{field}")
    _sha256(copied["plan_hash"], "envelope_plan_hash")
    _positive_timestamp(copied["issued_at"], "envelope_issued_at")
    _positive_timestamp(copied["expires_at"], "envelope_expires_at")
    for field in ("allowed_tools", "allowed_artifacts"):
        values = copied[field]
        if not isinstance(values, list) or len(values) > 1_000:
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_envelope_invalid")
        for value in values:
            _text(value, 512, f"envelope_{field}")
    try:
        _budget_mapping(copied["budgets"])
    except WorkflowTransitionAuthorizationGrantError as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_envelope_invalid"
        ) from exc
    for field in _ENVELOPE_OPTIONAL_FIELDS & fields:
        if not isinstance(copied[field], list) or len(copied[field]) > 8:
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_envelope_invalid")
    _bounded_mapping(copied, maximum=_MAX_ENVELOPE_BYTES, reason="envelope")
    try:
        envelope = RuntimeAuthorizationEnvelope.from_mapping(copied)
        envelope._assert_structure()
    except Exception as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_envelope_invalid"
        ) from exc
    if canonical_json(envelope.to_dict()) != canonical_json(copied):
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_envelope_roundtrip_invalid"
        )
    return envelope


def _verify_current(
    intent: _GrantIntent,
    *,
    verifier: SignatureVerificationKeyRingPort,
    now: float,
) -> None:
    if getattr(verifier, "signature_algorithm", None) != ED25519_ALGORITHM:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_ed25519_required")
    try:
        intent.envelope.verify(
            key_ring=verifier,
            tenant_id=intent.envelope.tenant_id,
            workflow_id=intent.envelope.workflow_id,
            run_id=intent.envelope.run_id,
            step_id=intent.envelope.step_id,
            plan_hash=intent.envelope.plan_hash,
            policy_version=intent.envelope.policy_version,
            now=now,
        )
    except Exception as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_current_authority_invalid"
        ) from exc


def _verify_historical(
    intent: _GrantIntent,
    historical: WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
) -> None:
    if getattr(historical, "signature_algorithm", None) != ED25519_ALGORITHM:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_ed25519_required")
    historical.verify_issued(
        intent.envelope,
        tenant_id=intent.envelope.tenant_id,
        workflow_id=intent.envelope.workflow_id,
        run_id=intent.envelope.run_id,
        step_id=intent.envelope.step_id,
        plan_hash=intent.envelope.plan_hash,
        policy_version=intent.envelope.policy_version,
    )


def _read_grant(
    reads: WorkflowAuthorizationGrantReadPort,
    intent: _GrantIntent,
) -> WorkflowAuthorizationGrant | None:
    return reads.get(
        tenant_id=intent.envelope.tenant_id,
        workflow_id=intent.envelope.workflow_id,
        run_id=intent.envelope.run_id,
        step_id=intent.envelope.step_id,
        envelope_id=intent.envelope.envelope_id,
    )


def _required_grant(
    reads: WorkflowAuthorizationGrantReadPort,
    intent: _GrantIntent,
) -> WorkflowAuthorizationGrant:
    grant = _read_grant(reads, intent)
    if grant is None:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_missing")
    return grant


def _issuance(
    intent: _GrantIntent,
    grant: WorkflowAuthorizationGrant,
    historical: WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
) -> dict[str, Any]:
    try:
        assert_workflow_authorization_grant_projection(grant)
    except Exception as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_resource_conflict"
        ) from exc
    _verify_historical(intent, historical)
    expected = (
        intent.envelope.envelope_id,
        intent.envelope.tenant_id,
        intent.envelope.workflow_id,
        intent.envelope.run_id,
        intent.envelope.step_id,
        intent.envelope.plan_hash,
        intent.envelope.policy_version,
        intent.envelope_digest,
        intent.envelope.issued_at,
        intent.envelope.expires_at,
    )
    actual = (
        grant.envelope_id,
        grant.tenant_id,
        grant.workflow_id,
        grant.run_id,
        grant.step_id,
        grant.plan_hash,
        grant.policy_version,
        grant.grant_digest,
        grant.issued_at,
        grant.expires_at,
    )
    legal_active = grant.status == "active" and grant.revision == 1 and not grant.revocation_reason
    legal_revoked = grant.status == "revoked" and grant.revision == 2 and bool(grant.revocation_reason)
    if actual != expected or not (legal_active or legal_revoked):
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_resource_conflict")
    return {
        "schema": WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ISSUANCE_SCHEMA,
        "signature_algorithm": ED25519_ALGORITHM,
        "envelope_id": grant.envelope_id,
        "tenant_id": grant.tenant_id,
        "workflow_id": grant.workflow_id,
        "run_id": grant.run_id,
        "step_id": grant.step_id,
        "plan_hash": grant.plan_hash,
        "policy_version": grant.policy_version,
        "envelope_digest": grant.grant_digest,
        "issued_revision": 1,
        "issued_at": grant.issued_at,
        "expires_at": grant.expires_at,
    }


def _result(issuance: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESULT_SCHEMA,
        "issuance": dict(issuance),
    }


def _assert_result(
    result_payload: Mapping[str, Any],
    issuance: Mapping[str, Any],
) -> None:
    result = _bounded_mapping(result_payload, maximum=16_384, reason="result")
    if set(result) != _RESULT_FIELDS or result.get("schema") != (WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESULT_SCHEMA):
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_result_invalid")
    raw_issuance = result.get("issuance")
    if not isinstance(raw_issuance, dict) or set(raw_issuance) != _ISSUANCE_FIELDS:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_result_invalid")
    if (
        raw_issuance.get("schema") != WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ISSUANCE_SCHEMA
        or raw_issuance.get("signature_algorithm") != ED25519_ALGORITHM
        or type(raw_issuance.get("issued_revision")) is not int
        or raw_issuance.get("issued_revision") != 1
        or type(raw_issuance.get("issued_at")) is not float
        or type(raw_issuance.get("expires_at")) is not float
    ):
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_result_invalid")
    for field in ("envelope_id",):
        _identity(raw_issuance.get(field), f"result_{field}")
    for field in ("tenant_id", "workflow_id", "run_id", "step_id"):
        _scope_identity(raw_issuance.get(field), f"result_{field}")
    _text(raw_issuance.get("policy_version"), 256, "result_policy_version")
    _sha256(raw_issuance.get("plan_hash"), "result_plan_hash")
    _sha256(raw_issuance.get("envelope_digest"), "result_envelope_digest")
    issued_at = _positive_timestamp(
        raw_issuance.get("issued_at"),
        "result_issued_at",
    )
    expires_at = _positive_timestamp(
        raw_issuance.get("expires_at"),
        "result_expires_at",
    )
    if expires_at <= issued_at or canonical_json(result) != canonical_json(_result(issuance)):
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_result_conflict")


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


def _resource_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    intent: _GrantIntent,
    issuance: Mapping[str, Any],
) -> WorkflowTransitionEffectResourceProof:
    return WorkflowTransitionEffectResourceProof(
        context=WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=claim_generation,
        ),
        resource_kind=WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESOURCE_KIND,
        resource_id=intent.envelope.envelope_id,
        resource_revision=1,
        resource_digest=workflow_transition_effect_resource_digest(issuance),
    )


def _absence_values(intent: _GrantIntent) -> dict[str, Any]:
    return {
        "schema": WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ABSENCE_SCHEMA,
        "resource_kind": WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_SLOT_KIND,
        "resource_id": intent.envelope.envelope_id,
        "envelope_digest": intent.envelope_digest,
        "absent": True,
    }


def _absence_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    intent: _GrantIntent,
) -> WorkflowTransitionEffectAbsenceProof:
    return WorkflowTransitionEffectAbsenceProof(
        context=WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=claim_generation,
        ),
        resource_kind=WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_SLOT_KIND,
        resource_id=intent.envelope.envelope_id,
        head_revision=0,
        head_digest=workflow_transition_effect_resource_digest(_absence_values(intent)),
    )


def _bounded_mapping(
    value: Any,
    *,
    maximum: int,
    reason: str,
) -> dict[str, Any]:
    copied = _copy_json(value, depth=0, budget=_JsonBudget())
    if not isinstance(copied, dict):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    try:
        encoded = canonical_json(copied).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError) as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            f"workflow_transition_authorization_grant_{reason}_invalid"
        ) from exc
    if len(encoded) > maximum:
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_too_large")
    return copied


def _input_mapping(value: Any, *, reason: str) -> dict[str, Any]:
    copied = _copy_json(value, depth=0, budget=_JsonBudget())
    if not isinstance(copied, dict):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    return copied


def _text_sequence(value: Any, *, reason: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    if len(value) > 1_000:
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    return [_text(item, 512, reason) for item in value]


def _mapping_sequence(value: Any, *, reason: str) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    if len(value) > 8:
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    return [_input_mapping(item, reason=reason) for item in value]


def _provider_binding_sequence(value: Any) -> list[dict[str, Any]]:
    raw_values = _mapping_sequence(
        value,
        reason="allowed_provider_bindings",
    )
    canonical: list[dict[str, Any]] = []
    for raw in raw_values:
        try:
            normalized = ProviderBindingAuthorization.from_mapping(raw).to_dict()
        except Exception as exc:
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_allowed_provider_bindings_invalid"
            ) from exc
        if canonical_json(raw) != canonical_json(normalized):
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_allowed_provider_bindings_invalid"
            )
        canonical.append(normalized)
    return canonical


def _provider_attempt_sequence(value: Any) -> list[dict[str, Any]]:
    raw_values = _mapping_sequence(
        value,
        reason="provider_attempt_plan",
    )
    canonical: list[dict[str, Any]] = []
    for raw in raw_values:
        try:
            normalized = ProviderProfileAttemptPlanEntry.from_mapping(raw).to_dict()
        except Exception as exc:
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_provider_attempt_plan_invalid"
            ) from exc
        if canonical_json(raw) != canonical_json(normalized):
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_provider_attempt_plan_invalid"
            )
        canonical.append(normalized)
    return canonical


def _budget_mapping(value: Any) -> dict[str, int | float]:
    copied = _input_mapping(value, reason="budgets")
    if len(copied) > 256:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_budgets_invalid")
    budgets: dict[str, int | float] = {}
    for name, amount in copied.items():
        _text(name, 256, "budget_name")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or amount < 0
            or amount > _MAX_COUNTER
            or (isinstance(amount, float) and not math.isfinite(amount))
            or (name == "provider_attempts" and type(amount) is not int)
        ):
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_budgets_invalid")
        budgets[name] = amount
    return budgets


def _assert_provider_attempt_budget(
    *,
    budgets: Mapping[str, int | float],
    attempt_plan: Sequence[Mapping[str, Any]],
) -> None:
    if not attempt_plan:
        return
    maximum = budgets.get("provider_attempts")
    planned = sum(item["maximum_attempts"] for item in attempt_plan)
    if type(maximum) is not int or maximum != planned:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_provider_attempt_plan_budget_invalid"
        )


def _copy_json(value: Any, *, depth: int, budget: _JsonBudget) -> Any:
    if depth > _MAX_JSON_DEPTH:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")
    if isinstance(value, str):
        return budget.text(value)
    budget.item()
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256 or "\x00" in key:
                raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")
            budget.text(key)
            copied[key] = _copy_json(item, depth=depth + 1, budget=budget)
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_json(item, depth=depth + 1, budget=budget) for item in value]
    raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")


def _identity(value: Any, reason: str) -> str:
    return _SCALARS.identity(value, reason)


def _scope_identity(value: Any, reason: str) -> str:
    try:
        normalized = require_canonical_identity(value, field_name=reason)
        return _identity(normalized, reason)
    except Exception as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            f"workflow_transition_authorization_grant_{reason}_invalid"
        ) from exc


def _text(value: Any, maximum: int, reason: str) -> str:
    return _SCALARS.text(value, reason, maximum=maximum)


def _sha256(value: Any, reason: str) -> str:
    return _SCALARS.sha256(value, reason)


def _positive_integer(value: Any, reason: str) -> int:
    return _SCALARS.positive_integer(value, reason, maximum=_MAX_COUNTER)


def _positive_timestamp(value: Any, reason: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or value > _MAX_COUNTER
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    return float(value)


def _ttl(value: Any) -> float:
    ttl = _positive_timestamp(value, "ttl_seconds")
    if ttl > _MAX_TTL_SECONDS:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_ttl_seconds_invalid")
    return ttl


def _clock_value(clock: Callable[[], float]) -> float:
    try:
        return _positive_timestamp(clock(), "clock")
    except Exception as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_clock_invalid"
        ) from exc


def _opaque_id(prefix: str, namespace: str, *parts: str) -> str:
    payload = "\x00".join((namespace, *parts)).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "RetainedEd25519AuthorizationEnvelopeIntegrityVerifier",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_EFFECT_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ISSUANCE_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESULT_SCHEMA",
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
