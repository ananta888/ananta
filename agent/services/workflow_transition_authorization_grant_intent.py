"""Staged Ed25519 authorization-grant intent: schemas, identities and parsing.

Historical integrity and current authority are intentionally separate here.
The retained-key verifier proves only signature mathematics for an issuance
that already exists and MUST NOT be used to authorize a new grant.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_authorization_grant_service import workflow_authorization_grant_digest
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.security import (
    AUTHORIZATION_ENVELOPE_SCHEMA,
    RuntimeAuthorizationEnvelope,
    SignatureVerificationKeyRingPort,
)
from agent.services.workflow_transition_authorization_grant_validation import (
    _MAX_EFFECT_BYTES,
    _MAX_ENVELOPE_BYTES,
    WorkflowTransitionAuthorizationGrantError,
    _bounded_mapping,
    _budget_mapping,
    _copy_json,
    _identity,
    _JsonBudget,
    _opaque_id,
    _positive_integer,
    _positive_timestamp,
    _scope_identity,
    _sha256,
    _text,
    _ttl,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_AUTHORIZATION_GRANT,
    TRANSITION_RUNTIMES,
    WorkflowTransition,
    WorkflowTransitionEffect,
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


__all__ = [
    "RetainedEd25519AuthorizationEnvelopeIntegrityVerifier",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ABSENCE_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_EFFECT_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_ISSUANCE_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESOURCE_KIND",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_RESULT_SCHEMA",
    "WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_SLOT_KIND",
    "WorkflowAuthorizationEnvelopeHistoricalIntegrityPort",
    "workflow_transition_authorization_grant_envelope_id",
    "workflow_transition_authorization_grant_idempotency_key",
    "workflow_transition_authorization_grant_nonce",
]
