"""Issuance evidence, results and proofs for staged authorization grants.

Pure projections of a Hub-committed grant into the deterministic issuance
result, resource/absence proofs, and the durable/current proof assertions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrant,
    WorkflowAuthorizationGrantReadPort,
    assert_workflow_authorization_grant_projection,
)
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.security import SignatureVerificationKeyRingPort
from agent.services.workflow_transition_authorization_grant_intent import (
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ABSENCE_SCHEMA,
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ISSUANCE_SCHEMA,
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESOURCE_KIND,
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESULT_SCHEMA,
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_SLOT_KIND,
    WorkflowAuthorizationEnvelopeHistoricalIntegrityPort,
    _GrantIntent,
    _intent_from_effect,
    _verify_current,
    _verify_historical,
)
from agent.services.workflow_transition_authorization_grant_validation import (
    WorkflowTransitionAuthorizationGrantError,
    _bounded_mapping,
    _clock_value,
    _identity,
    _positive_timestamp,
    _scope_identity,
    _sha256,
    _text,
)
from agent.services.workflow_transition_effect_proofs import (
    WorkflowTransitionEffectAbsenceProof,
    WorkflowTransitionEffectProofContext,
    WorkflowTransitionEffectResourceProof,
    assert_active_workflow_transition_effect_proof_binding,
    assert_durable_workflow_transition_effect_proof_binding,
    workflow_transition_effect_resource_digest,
)
from agent.services.workflow_transition_outbox import (
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_stage_attempt_count,
)
from ananta_contracts.runtime_authorization_crypto import ED25519_ALGORITHM

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


__all__ = [
    "assert_active_workflow_transition_authorization_grant_proof",
    "assert_current_workflow_transition_authorization_grant_validity",
    "assert_durable_workflow_transition_authorization_grant_proof",
]
