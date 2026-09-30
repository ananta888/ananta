"""Workflow-transition side-effect authorization contract.

Intent/receipt/observation value types, the read/commit ports, deterministic
identity and digest derivation, and the pure observation/commit planning that
every authorization adapter (in-memory, SQLite, SQLAlchemy) shares.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from agent.services.workflow_runtime.errors import (
    InvalidTransitionError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.side_effect_records import (
    SideEffectRecord,
    _new_record,
)
from agent.services.workflow_runtime.side_effect_validation import (
    _bounded_text,
    _identity,
    _namespaced_digest,
    _nonnegative_integer,
    _positive_integer,
    _positive_timestamp,
    _runtime_identity,
    _sha256,
)
from ananta_contracts.workflow_operation import operation_id_for

WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_INTENT_SCHEMA = (
    "ananta.workflow_transition_side_effect_authorization_intent.v1"
)
WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RECEIPT_SCHEMA = (
    "ananta.workflow_transition_side_effect_authorization_receipt.v1"
)
_WORKFLOW_TRANSITION_SIDE_EFFECT_FENCE_NAMESPACE = "workflow-transition-side-effect-operation-fence.v1"
_WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_NAMESPACE = "workflow-transition-side-effect-authorization-receipt.v1"
_WORKFLOW_TRANSITION_SIDE_EFFECT_INTENT_DIGEST_NAMESPACE = "workflow-transition-side-effect-operation-intent.v1"
_WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_DIGEST_NAMESPACE = (
    "workflow-transition-side-effect-authorization-receipt-digest.v1"
)
_WORKFLOW_TRANSITION_SIDE_EFFECT_OBSERVATION_DIGEST_NAMESPACE = (
    "workflow-transition-side-effect-authorization-observation.v1"
)
_TRANSITION_SIDE_EFFECT_WRITE_CLASSES = frozenset({"idempotent_write", "non_idempotent_write"})

_MAX_OPERATION_CHARS = 512

_MAX_OPERATION_RECEIPTS = 1_000

_TRANSITION_AUTHORIZATION_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "receipt_id",
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
        "creator_claim_generation",
        "transition_request_fingerprint",
        "effect_payload_digest",
        "idempotency_key",
        "prior_status",
        "prior_revision",
        "prior_record_digest",
        "authorized_ledger_revision",
        "authorized_record_digest",
        "authorized_record",
        "planned_at",
        "authorized_at",
        "receipt_digest",
    }
)


@dataclass(frozen=True)
class WorkflowTransitionSideEffectAuthorizationIntent:
    """Exact active-effect intent consumed by the atomic ledger UoW."""

    receipt_id: str
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
    creator_claim_generation: int
    transition_request_fingerprint: str
    effect_payload_digest: str
    idempotency_key: str
    planned_at: float
    schema: str = WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_INTENT_SCHEMA

    def __post_init__(self) -> None:
        _assert_transition_authorization_intent(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "receipt_id": self.receipt_id,
            "transition_id": self.transition_id,
            "effect_id": self.effect_id,
            "runtime_id": self.runtime_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "effect_ordinal": self.effect_ordinal,
            "declared_operation": self.declared_operation,
            "side_effect_class": self.side_effect_class,
            "operation_id": self.operation_id,
            "operation_payload_digest": self.operation_payload_digest,
            "operation_intent_digest": self.operation_intent_digest,
            "operation_fence_id": self.operation_fence_id,
            "authorization_envelope_id": self.authorization_envelope_id,
            "authorization_envelope_digest": self.authorization_envelope_digest,
            "ownership_attempt_id": self.ownership_attempt_id,
            "ownership_fencing_token": self.ownership_fencing_token,
            "creator_claim_generation": self.creator_claim_generation,
            "transition_request_fingerprint": self.transition_request_fingerprint,
            "effect_payload_digest": self.effect_payload_digest,
            "idempotency_key": self.idempotency_key,
            "planned_at": self.planned_at,
        }


@dataclass(frozen=True)
class WorkflowTransitionSideEffectAuthorizationReceipt:
    """Append-only proof that one exact ledger authorization was committed."""

    receipt_id: str
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
    creator_claim_generation: int
    transition_request_fingerprint: str
    effect_payload_digest: str
    idempotency_key: str
    prior_status: str
    prior_revision: int
    prior_record_digest: str
    authorized_ledger_revision: int
    authorized_record_digest: str
    authorized_record: SideEffectRecord
    planned_at: float
    authorized_at: float
    receipt_digest: str
    schema: str = WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        record = SideEffectRecord.from_exact_mapping(
            self.authorized_record.to_dict()
            if isinstance(self.authorized_record, SideEffectRecord)
            else self.authorized_record
        )
        object.__setattr__(self, "authorized_record", record)
        _assert_transition_authorization_receipt(self, authorized_record=record)

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, object],
    ) -> "WorkflowTransitionSideEffectAuthorizationReceipt":
        if not isinstance(raw, Mapping) or set(raw) != _TRANSITION_AUTHORIZATION_RECEIPT_FIELDS:
            raise ValueError("workflow_transition_side_effect_authorization_receipt_invalid")
        return cls(**{name: raw[name] for name in _TRANSITION_AUTHORIZATION_RECEIPT_FIELDS})

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "receipt_id": self.receipt_id,
            "transition_id": self.transition_id,
            "effect_id": self.effect_id,
            "runtime_id": self.runtime_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "effect_ordinal": self.effect_ordinal,
            "declared_operation": self.declared_operation,
            "side_effect_class": self.side_effect_class,
            "operation_id": self.operation_id,
            "operation_payload_digest": self.operation_payload_digest,
            "operation_intent_digest": self.operation_intent_digest,
            "operation_fence_id": self.operation_fence_id,
            "authorization_envelope_id": self.authorization_envelope_id,
            "authorization_envelope_digest": self.authorization_envelope_digest,
            "ownership_attempt_id": self.ownership_attempt_id,
            "ownership_fencing_token": self.ownership_fencing_token,
            "creator_claim_generation": self.creator_claim_generation,
            "transition_request_fingerprint": self.transition_request_fingerprint,
            "effect_payload_digest": self.effect_payload_digest,
            "idempotency_key": self.idempotency_key,
            "prior_status": self.prior_status,
            "prior_revision": self.prior_revision,
            "prior_record_digest": self.prior_record_digest,
            "authorized_ledger_revision": self.authorized_ledger_revision,
            "authorized_record_digest": self.authorized_record_digest,
            "authorized_record": self.authorized_record.to_dict(),
            "planned_at": self.planned_at,
            "authorized_at": self.authorized_at,
            "receipt_digest": self.receipt_digest,
        }


@dataclass(frozen=True)
class WorkflowTransitionSideEffectAuthorizationObservation:
    """One lock/transaction snapshot of receipt aliases and ledger state."""

    intent: WorkflowTransitionSideEffectAuthorizationIntent
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt | None
    operation_receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...]
    ledger_record: SideEffectRecord | None
    observation_digest: str


@runtime_checkable
class WorkflowTransitionSideEffectAuthorizationReadPort(Protocol):
    def observe_transition_authorization(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
    ) -> WorkflowTransitionSideEffectAuthorizationObservation: ...


@runtime_checkable
class WorkflowTransitionSideEffectAuthorizationCommitPort(Protocol):
    def authorize_transition_effect(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
        *,
        expected_observation_digest: str,
    ) -> WorkflowTransitionSideEffectAuthorizationReceipt: ...


def workflow_transition_side_effect_operation_intent_digest(
    *,
    operation_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    declared_operation: str,
    side_effect_class: str,
    operation_payload_digest: str,
) -> str:
    payload = {
        "operation_id": operation_id,
        "tenant_id": tenant_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "step_id": step_id,
        "declared_operation": declared_operation,
        "side_effect_class": side_effect_class,
        "operation_payload_digest": operation_payload_digest,
    }
    return _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_INTENT_DIGEST_NAMESPACE,
        payload,
    )


def workflow_transition_side_effect_operation_fence_id(
    *,
    operation_id: str,
    operation_intent_digest: str,
    ownership_attempt_id: str,
    ownership_fencing_token: int,
    authorization_envelope_id: str,
    authorization_envelope_digest: str,
) -> str:
    payload = {
        "operation_id": operation_id,
        "operation_intent_digest": operation_intent_digest,
        "ownership_attempt_id": ownership_attempt_id,
        "ownership_fencing_token": ownership_fencing_token,
        "authorization_envelope_id": authorization_envelope_id,
        "authorization_envelope_digest": authorization_envelope_digest,
    }
    return "wftsf-" + _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_FENCE_NAMESPACE,
        payload,
    )


def workflow_transition_side_effect_authorization_receipt_id(
    *,
    transition_id: str,
    effect_id: str,
) -> str:
    return "wftsar-" + _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_NAMESPACE,
        {
            "transition_id": transition_id,
            "effect_id": effect_id,
        },
    )


def _assert_transition_authorization_intent(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
) -> None:
    if intent.schema != WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_INTENT_SCHEMA:
        raise ValueError("workflow_transition_side_effect_authorization_intent_schema_unsupported")
    for value, reason in (
        (intent.receipt_id, "receipt_id"),
        (intent.transition_id, "transition_id"),
        (intent.effect_id, "effect_id"),
        (intent.tenant_id, "tenant_id"),
        (intent.workflow_id, "workflow_id"),
        (intent.run_id, "run_id"),
        (intent.step_id, "step_id"),
        (intent.operation_id, "operation_id"),
        (intent.operation_fence_id, "operation_fence_id"),
        (intent.authorization_envelope_id, "authorization_envelope_id"),
        (intent.ownership_attempt_id, "ownership_attempt_id"),
    ):
        _identity(value, reason)
    _runtime_identity(intent.runtime_id)
    _bounded_text(intent.declared_operation, _MAX_OPERATION_CHARS, "declared_operation")
    if (
        not isinstance(intent.side_effect_class, str)
        or intent.side_effect_class not in _TRANSITION_SIDE_EFFECT_WRITE_CLASSES
    ):
        raise ValueError("workflow_transition_side_effect_authorization_class_invalid")
    _positive_integer(intent.effect_ordinal, "effect_ordinal")
    _positive_integer(intent.ownership_fencing_token, "ownership_fencing_token")
    _positive_integer(intent.creator_claim_generation, "creator_claim_generation")
    _positive_timestamp(intent.planned_at, "planned_at")
    for value, reason in (
        (intent.operation_payload_digest, "operation_payload_digest"),
        (intent.operation_intent_digest, "operation_intent_digest"),
        (intent.authorization_envelope_digest, "authorization_envelope_digest"),
        (intent.transition_request_fingerprint, "transition_request_fingerprint"),
        (intent.effect_payload_digest, "effect_payload_digest"),
    ):
        _sha256(value, reason)
    expected_operation = operation_id_for(
        tenant_id=intent.tenant_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        declared_operation=intent.declared_operation,
    )
    expected_intent_digest = workflow_transition_side_effect_operation_intent_digest(
        operation_id=intent.operation_id,
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        declared_operation=intent.declared_operation,
        side_effect_class=intent.side_effect_class,
        operation_payload_digest=intent.operation_payload_digest,
    )
    expected_fence_id = workflow_transition_side_effect_operation_fence_id(
        operation_id=intent.operation_id,
        operation_intent_digest=intent.operation_intent_digest,
        ownership_attempt_id=intent.ownership_attempt_id,
        ownership_fencing_token=intent.ownership_fencing_token,
        authorization_envelope_id=intent.authorization_envelope_id,
        authorization_envelope_digest=intent.authorization_envelope_digest,
    )
    expected_receipt_id = workflow_transition_side_effect_authorization_receipt_id(
        transition_id=intent.transition_id,
        effect_id=intent.effect_id,
    )
    if (
        intent.operation_id != expected_operation
        or intent.operation_intent_digest != expected_intent_digest
        or intent.operation_fence_id != expected_fence_id
        or intent.idempotency_key != intent.operation_fence_id
        or intent.receipt_id != expected_receipt_id
    ):
        raise ValueError("workflow_transition_side_effect_authorization_intent_binding_invalid")


def _assert_transition_authorization_receipt(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
    *,
    authorized_record: SideEffectRecord,
) -> None:
    intent = WorkflowTransitionSideEffectAuthorizationIntent(
        receipt_id=receipt.receipt_id,
        transition_id=receipt.transition_id,
        effect_id=receipt.effect_id,
        runtime_id=receipt.runtime_id,
        tenant_id=receipt.tenant_id,
        workflow_id=receipt.workflow_id,
        run_id=receipt.run_id,
        step_id=receipt.step_id,
        effect_ordinal=receipt.effect_ordinal,
        declared_operation=receipt.declared_operation,
        side_effect_class=receipt.side_effect_class,
        operation_id=receipt.operation_id,
        operation_payload_digest=receipt.operation_payload_digest,
        operation_intent_digest=receipt.operation_intent_digest,
        operation_fence_id=receipt.operation_fence_id,
        authorization_envelope_id=receipt.authorization_envelope_id,
        authorization_envelope_digest=receipt.authorization_envelope_digest,
        ownership_attempt_id=receipt.ownership_attempt_id,
        ownership_fencing_token=receipt.ownership_fencing_token,
        creator_claim_generation=receipt.creator_claim_generation,
        transition_request_fingerprint=receipt.transition_request_fingerprint,
        effect_payload_digest=receipt.effect_payload_digest,
        idempotency_key=receipt.idempotency_key,
        planned_at=receipt.planned_at,
    )
    del intent
    if receipt.schema != WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RECEIPT_SCHEMA:
        raise ValueError("workflow_transition_side_effect_authorization_receipt_schema_unsupported")
    if not isinstance(receipt.prior_status, str) or receipt.prior_status not in {
        "absent",
        "planned",
        "failed",
    }:
        raise ValueError("workflow_transition_side_effect_authorization_prior_state_invalid")
    _nonnegative_integer(receipt.prior_revision, "prior_revision")
    _sha256(receipt.prior_record_digest, "prior_record_digest")
    _positive_integer(receipt.authorized_ledger_revision, "authorized_ledger_revision")
    _sha256(receipt.authorized_record_digest, "authorized_record_digest")
    _sha256(receipt.receipt_digest, "receipt_digest")
    _positive_timestamp(receipt.authorized_at, "authorized_at")
    if receipt.authorized_at != receipt.planned_at:
        raise ValueError("workflow_transition_side_effect_authorization_timestamp_invalid")
    if receipt.prior_status == "absent":
        if receipt.prior_revision != 0:
            raise ValueError("workflow_transition_side_effect_authorization_prior_state_invalid")
    elif receipt.prior_revision < 1:
        raise ValueError("workflow_transition_side_effect_authorization_prior_state_invalid")
    expected_authorized_revision = 2 if receipt.prior_status == "absent" else receipt.prior_revision + 1
    if receipt.authorized_ledger_revision != expected_authorized_revision:
        raise ValueError("workflow_transition_side_effect_authorization_revision_invalid")
    if (
        authorized_record.operation_id != receipt.operation_id
        or authorized_record.tenant_id != receipt.tenant_id
        or authorized_record.workflow_id != receipt.workflow_id
        or authorized_record.run_id != receipt.run_id
        or authorized_record.step_id != receipt.step_id
        or authorized_record.declared_operation != receipt.declared_operation
        or authorized_record.side_effect_class != receipt.side_effect_class
        or authorized_record.status != "authorized"
        or authorized_record.revision != receipt.authorized_ledger_revision
        or authorized_record.fencing_token != receipt.ownership_fencing_token
        or authorized_record.authorization_envelope_id != receipt.authorization_envelope_id
        or authorized_record.attempt_id
        or authorized_record.result_ref
        or authorized_record.failure_code
        or authorized_record.updated_at != receipt.authorized_at
    ):
        raise ValueError("workflow_transition_side_effect_authorization_record_invalid")
    if _side_effect_record_digest(authorized_record) != receipt.authorized_record_digest:
        raise ValueError("workflow_transition_side_effect_authorization_record_digest_mismatch")
    if _transition_authorization_receipt_digest(receipt) != receipt.receipt_digest:
        raise ValueError("workflow_transition_side_effect_authorization_receipt_digest_mismatch")


def _transition_authorization_observation(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    *,
    ledger_record: SideEffectRecord | None,
    receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...],
) -> WorkflowTransitionSideEffectAuthorizationObservation:
    if not isinstance(intent, WorkflowTransitionSideEffectAuthorizationIntent):
        raise ValueError("workflow_transition_side_effect_authorization_intent_invalid")
    if len(receipts) > _MAX_OPERATION_RECEIPTS:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_history_limit")
    aliases = tuple(
        receipt
        for receipt in receipts
        if receipt.receipt_id == intent.receipt_id
        or receipt.effect_id == intent.effect_id
        or receipt.operation_fence_id == intent.operation_fence_id
    )
    if aliases and any(receipt != aliases[0] for receipt in aliases[1:]):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_alias_conflict")
    candidate = aliases[0] if aliases else None
    if candidate is not None:
        _assert_transition_authorization_receipt_matches_intent(candidate, intent)
    operation_receipts = tuple(
        sorted(
            (receipt for receipt in receipts if receipt.operation_id == intent.operation_id),
            key=lambda value: (value.authorized_ledger_revision, value.receipt_id),
        )
    )
    for receipt in operation_receipts:
        _assert_transition_authorization_operation_history(receipt, intent)
    for previous, current in zip(operation_receipts, operation_receipts[1:], strict=False):
        if (
            current.authorized_ledger_revision <= previous.authorized_ledger_revision
            or current.ownership_fencing_token <= previous.ownership_fencing_token
        ):
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_history_conflict")
    if ledger_record is not None:
        _assert_transition_authorization_ledger_binding(ledger_record, intent)
    if candidate is not None:
        _assert_transition_authorization_current_ledger(
            ledger_record,
            receipts=operation_receipts,
        )
    elif ledger_record is None:
        if operation_receipts:
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_ledger_missing")
    elif ledger_record.status == "planned":
        if operation_receipts or not _pristine_planned_record(ledger_record):
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_planned_conflict")
    elif ledger_record.status == "failed":
        _assert_failed_reauthorization(
            ledger_record,
            intent=intent,
            prior_receipts=operation_receipts,
        )
    else:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_missing")
    digest = _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_OBSERVATION_DIGEST_NAMESPACE,
        {
            "intent": intent.to_dict(),
            "receipt": candidate.to_dict() if candidate is not None else None,
            "operation_receipts": [value.to_dict() for value in operation_receipts],
            "ledger_record": ledger_record.to_dict() if ledger_record is not None else None,
        },
    )
    return WorkflowTransitionSideEffectAuthorizationObservation(
        intent=intent,
        receipt=candidate,
        operation_receipts=operation_receipts,
        ledger_record=ledger_record,
        observation_digest=digest,
    )


def _transition_authorization_relevant_receipts(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    receipts: Iterable[WorkflowTransitionSideEffectAuthorizationReceipt],
) -> tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...]:
    return tuple(
        receipt
        for receipt in receipts
        if receipt.operation_id == intent.operation_id
        or receipt.receipt_id == intent.receipt_id
        or receipt.effect_id == intent.effect_id
        or receipt.operation_fence_id == intent.operation_fence_id
    )


def _transition_authorization_commit_values(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    *,
    current: SideEffectRecord | None,
    prior_receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...],
) -> tuple[
    SideEffectRecord,
    SideEffectRecord,
    WorkflowTransitionSideEffectAuthorizationReceipt,
]:
    planned = current or _new_record(
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        declared_operation=intent.declared_operation,
        side_effect_class=intent.side_effect_class,
        timestamp=intent.planned_at,
    )
    if planned.status not in {"planned", "failed"}:
        raise InvalidTransitionError("workflow_transition_side_effect_authorization_state_conflict")
    if planned.status == "planned" and not _pristine_planned_record(planned):
        raise InvalidTransitionError("workflow_transition_side_effect_authorization_planned_conflict")
    if planned.status == "failed":
        _assert_failed_reauthorization(
            planned,
            intent=intent,
            prior_receipts=prior_receipts,
        )
    authorized = replace(
        planned,
        status="authorized",
        revision=planned.revision + 1,
        fencing_token=intent.ownership_fencing_token,
        attempt_id="",
        authorization_envelope_id=intent.authorization_envelope_id,
        result_ref="",
        failure_code="",
        updated_at=intent.planned_at,
    )
    authorized.assert_valid()
    receipt = _new_transition_authorization_receipt(
        intent,
        prior=planned if current is not None else None,
        authorized=authorized,
    )
    return planned, authorized, receipt


def _new_transition_authorization_receipt(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    *,
    prior: SideEffectRecord | None,
    authorized: SideEffectRecord,
) -> WorkflowTransitionSideEffectAuthorizationReceipt:
    values: dict[str, object] = {
        **intent.to_dict(),
        "schema": WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RECEIPT_SCHEMA,
        "prior_status": prior.status if prior is not None else "absent",
        "prior_revision": prior.revision if prior is not None else 0,
        "prior_record_digest": _side_effect_record_digest(prior),
        "authorized_ledger_revision": authorized.revision,
        "authorized_record_digest": _side_effect_record_digest(authorized),
        "authorized_record": authorized,
        "authorized_at": intent.planned_at,
    }
    digest_payload = {
        **values,
        "authorized_record": authorized.to_dict(),
    }
    return WorkflowTransitionSideEffectAuthorizationReceipt(
        **values,
        receipt_digest=_namespaced_digest(
            _WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_DIGEST_NAMESPACE,
            digest_payload,
        ),
    )


def _transition_authorization_receipt_digest(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
) -> str:
    payload = receipt.to_dict()
    payload.pop("receipt_digest", None)
    return _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_DIGEST_NAMESPACE,
        payload,
    )


def _assert_transition_authorization_receipt_matches_intent(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
) -> None:
    expected = intent.to_dict()
    expected.pop("schema")
    expected.pop("creator_claim_generation")
    actual = receipt.to_dict()
    for name, value in expected.items():
        if actual.get(name) != value:
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_conflict")
    if receipt.creator_claim_generation > intent.creator_claim_generation:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_generation_conflict")


def _assert_transition_authorization_operation_history(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
) -> None:
    if (
        receipt.operation_id != intent.operation_id
        or receipt.operation_intent_digest != intent.operation_intent_digest
        or receipt.operation_payload_digest != intent.operation_payload_digest
        or receipt.tenant_id != intent.tenant_id
        or receipt.workflow_id != intent.workflow_id
        or receipt.run_id != intent.run_id
        or receipt.step_id != intent.step_id
        or receipt.declared_operation != intent.declared_operation
        or receipt.side_effect_class != intent.side_effect_class
    ):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_operation_conflict")


def _assert_transition_authorization_ledger_binding(
    record: SideEffectRecord,
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
) -> None:
    if (
        record.operation_id != intent.operation_id
        or record.tenant_id != intent.tenant_id
        or record.workflow_id != intent.workflow_id
        or record.run_id != intent.run_id
        or record.step_id != intent.step_id
        or record.declared_operation != intent.declared_operation
        or record.side_effect_class != intent.side_effect_class
    ):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_ledger_binding_conflict")


def _assert_failed_reauthorization(
    record: SideEffectRecord,
    *,
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    prior_receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...],
) -> None:
    if not prior_receipts:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_prior_receipt_missing")
    latest = prior_receipts[-1]
    if (
        record.status != "failed"
        or record.fencing_token != latest.ownership_fencing_token
        or record.authorization_envelope_id != latest.authorization_envelope_id
        or not record.attempt_id
        or record.revision <= latest.authorized_ledger_revision
        or not record.failure_code
        or intent.ownership_fencing_token <= latest.ownership_fencing_token
    ):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_reauthorization_conflict")


def _assert_transition_authorization_current_ledger(
    record: SideEffectRecord | None,
    *,
    receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...],
) -> None:
    """Reject ledger regressions without binding proof to later mutable progress."""

    if record is None:
        return
    latest = receipts[-1]
    if record.revision < latest.authorized_ledger_revision:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_ledger_revision_regressed")
    if record.revision == latest.authorized_ledger_revision and record != latest.authorized_record:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_ledger_snapshot_conflict")


def _pristine_planned_record(record: SideEffectRecord) -> bool:
    return bool(
        record.status == "planned"
        and record.revision == 1
        and record.fencing_token == 0
        and not record.attempt_id
        and not record.authorization_envelope_id
        and not record.result_ref
        and not record.failure_code
    )


def _side_effect_record_digest(record: SideEffectRecord | None) -> str:
    return _namespaced_digest(
        "workflow-transition-side-effect-ledger-record.v1",
        record.to_dict() if record is not None else {"state": "absent"},
    )


def assert_workflow_transition_side_effect_authorization_observation_digest(
    value: object,
) -> str:
    """Validate the exact digest accepted by every authorization commit adapter."""

    return _sha256(value, "expected_observation_digest")
