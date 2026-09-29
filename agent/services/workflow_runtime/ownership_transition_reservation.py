"""Transition ownership reservation value objects and read/commit ports."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent.services.workflow_runtime.ownership_records import (
    ExecutionOwnership,
    RetryBudgetSnapshot,
    _exact_optional_ownership,
)
from agent.services.workflow_runtime.ownership_transition_errors import WorkflowTransitionOwnershipReservationConflict
from agent.services.workflow_runtime.ownership_transition_identity import (
    _ownership_observation_digest,
    workflow_transition_ownership_attempt_id,
    workflow_transition_ownership_intent_digest,
    workflow_transition_ownership_operation_fence_id,
    workflow_transition_ownership_owner_id,
    workflow_transition_ownership_receipt_digest,
    workflow_transition_ownership_receipt_id,
    workflow_transition_ownership_record_digest,
)
from agent.services.workflow_runtime.ownership_values import (
    _OWNERSHIP_RETRY_CATEGORY,
    RETRY_BUDGET_SCHEMA,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_INTENT_SCHEMA,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_OBSERVATION_SCHEMA,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RECEIPT_SCHEMA,
    _ownership_exact_positive_float,
    _ownership_exact_timestamp,
    _ownership_identity,
    _ownership_namespaced_digest,
    _ownership_non_negative_integer,
    _ownership_positive_integer,
    _ownership_retry_maximum,
    _ownership_sha256,
)


@dataclass(frozen=True, slots=True)
class WorkflowTransitionOwnershipRetryConsumption:
    tenant_id: str
    run_id: str
    retry_id: str
    category: str = _OWNERSHIP_RETRY_CATEGORY

    def __post_init__(self) -> None:
        _ownership_identity(self.tenant_id, "retry_tenant_id")
        _ownership_identity(self.run_id, "retry_run_id")
        _ownership_identity(self.retry_id, "retry_id")
        if self.category != _OWNERSHIP_RETRY_CATEGORY:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_retry_category_invalid")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WorkflowTransitionOwnershipRetryConsumption":
        if not isinstance(raw, Mapping) or set(raw) != {
            "tenant_id",
            "run_id",
            "retry_id",
            "category",
        }:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_retry_consumption_invalid"
            )
        try:
            return cls(
                tenant_id=raw["tenant_id"],
                run_id=raw["run_id"],
                retry_id=raw["retry_id"],
                category=raw["category"],
            )
        except (TypeError, ValueError) as exc:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_retry_consumption_invalid"
            ) from exc

    def to_dict(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "retry_id": self.retry_id,
            "category": self.category,
        }


@dataclass(frozen=True, slots=True)
class WorkflowTransitionOwnershipReservationIntent:
    receipt_id: str
    transition_id: str
    effect_id: str
    runtime_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    effect_ordinal: int
    ownership_intent_digest: str
    owner_id: str
    operation_fence_id: str
    attempt_id: str
    retry_id: str
    transition_request_fingerprint: str
    effect_payload_digest: str
    idempotency_key: str
    lease_seconds: float
    maximum_retries: int
    planned_at: float
    schema: str = WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_INTENT_SCHEMA

    def __post_init__(self) -> None:
        for name in (
            "receipt_id",
            "transition_id",
            "effect_id",
            "runtime_id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "step_id",
            "owner_id",
            "operation_fence_id",
            "attempt_id",
            "retry_id",
        ):
            _ownership_identity(getattr(self, name), name)
        _ownership_positive_integer(self.effect_ordinal, "effect_ordinal")
        for name in (
            "ownership_intent_digest",
            "transition_request_fingerprint",
            "effect_payload_digest",
        ):
            _ownership_sha256(getattr(self, name), name)
        if self.idempotency_key != self.operation_fence_id or self.retry_id != self.operation_fence_id:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_idempotency_binding_invalid"
            )
        lease = _ownership_exact_positive_float(self.lease_seconds, "lease_seconds")
        maximum = _ownership_retry_maximum(self.maximum_retries)
        planned = _ownership_exact_timestamp(self.planned_at, "planned_at")
        lease_expiry = planned + lease
        if not math.isfinite(lease_expiry) or lease_expiry <= planned:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_lease_expiry_invalid")
        if self.schema != WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_INTENT_SCHEMA:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_intent_schema_unsupported"
            )
        expected_digest = workflow_transition_ownership_intent_digest(
            transition_id=self.transition_id,
            runtime_id=self.runtime_id,
            tenant_id=self.tenant_id,
            workflow_id=self.workflow_id,
            run_id=self.run_id,
            step_id=self.step_id,
            effect_ordinal=self.effect_ordinal,
            lease_seconds=lease,
            maximum_retries=maximum,
        )
        expected_owner = workflow_transition_ownership_owner_id(ownership_intent_digest=expected_digest)
        expected_fence = workflow_transition_ownership_operation_fence_id(
            ownership_intent_digest=expected_digest,
            owner_id=expected_owner,
        )
        expected_attempt = workflow_transition_ownership_attempt_id(
            effect_id=self.effect_id,
            operation_fence_id=expected_fence,
        )
        expected_receipt = workflow_transition_ownership_receipt_id(
            transition_id=self.transition_id,
            effect_id=self.effect_id,
        )
        if (
            self.ownership_intent_digest != expected_digest
            or self.owner_id != expected_owner
            or self.operation_fence_id != expected_fence
            or self.attempt_id != expected_attempt
            or self.retry_id != expected_fence
            or self.receipt_id != expected_receipt
        ):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_binding_invalid")

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
            "ownership_intent_digest": self.ownership_intent_digest,
            "owner_id": self.owner_id,
            "operation_fence_id": self.operation_fence_id,
            "attempt_id": self.attempt_id,
            "retry_id": self.retry_id,
            "transition_request_fingerprint": self.transition_request_fingerprint,
            "effect_payload_digest": self.effect_payload_digest,
            "idempotency_key": self.idempotency_key,
            "lease_seconds": self.lease_seconds,
            "maximum_retries": self.maximum_retries,
            "planned_at": self.planned_at,
        }


@dataclass(frozen=True, slots=True)
class WorkflowTransitionOwnershipReservationReceipt:
    intent: WorkflowTransitionOwnershipReservationIntent
    creator_claim_generation: int
    prior_ownership: ExecutionOwnership | None
    prior_record_digest: str
    acquired_ownership: ExecutionOwnership
    acquired_record_digest: str
    retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None
    retry_budget_used_before: int
    retry_budget_used_after: int
    reserved_at: float
    receipt_digest: str
    schema: str = WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_intent_invalid")
        generation = _ownership_positive_integer(self.creator_claim_generation, "creator_claim_generation")
        if self.prior_ownership is not None and not isinstance(self.prior_ownership, ExecutionOwnership):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_prior_invalid")
        if not isinstance(self.acquired_ownership, ExecutionOwnership):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_receipt_acquired_invalid"
            )
        prior = (
            None
            if self.prior_ownership is None
            else ExecutionOwnership.from_exact_mapping(self.prior_ownership.to_dict())
        )
        acquired = ExecutionOwnership.from_exact_mapping(self.acquired_ownership.to_dict())
        consumption = self.retry_consumption
        if consumption is not None and not isinstance(consumption, WorkflowTransitionOwnershipRetryConsumption):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_retry_invalid")
        before = _ownership_non_negative_integer(self.retry_budget_used_before, "retry_budget_used_before")
        after = _ownership_non_negative_integer(self.retry_budget_used_after, "retry_budget_used_after")
        if before > after or after > self.intent.maximum_retries:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_receipt_retry_budget_invalid"
            )
        reserved = _ownership_exact_timestamp(self.reserved_at, "reserved_at")
        _ownership_sha256(self.prior_record_digest, "prior_record_digest")
        _ownership_sha256(self.acquired_record_digest, "acquired_record_digest")
        _ownership_sha256(self.receipt_digest, "receipt_digest")
        if self.schema != WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RECEIPT_SCHEMA:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_receipt_schema_unsupported"
            )
        if self.prior_record_digest != workflow_transition_ownership_record_digest(prior):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_prior_digest_mismatch")
        if self.acquired_record_digest != workflow_transition_ownership_record_digest(acquired):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_acquired_digest_mismatch"
            )
        expected_revision = prior.revision + 1 if prior is not None else 1
        expected_fence = prior.fencing_token + 1 if prior is not None else 1
        intent = self.intent
        if prior is not None and (
            prior.tenant_id != intent.tenant_id
            or prior.workflow_id != intent.workflow_id
            or prior.run_id != intent.run_id
            or prior.step_id != intent.step_id
        ):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_prior_binding_invalid")
        if prior is not None and (
            prior.status not in {"active", "failed", "orphaned", "dead_letter"}
            or (prior.status == "active" and prior.lease_expires_at > reserved)
        ):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_prior_takeover_invalid")
        if (
            acquired.tenant_id != intent.tenant_id
            or acquired.workflow_id != intent.workflow_id
            or acquired.run_id != intent.run_id
            or acquired.step_id != intent.step_id
            or acquired.attempt_id != intent.attempt_id
            or acquired.owner_id != intent.owner_id
            or acquired.status != "active"
            or type(acquired.last_heartbeat_at) is not float
            or type(acquired.lease_expires_at) is not float
            or acquired.revision != expected_revision
            or acquired.fencing_token != expected_fence
            or acquired.last_heartbeat_at != reserved
            or acquired.lease_expires_at != reserved + intent.lease_seconds
            or acquired.lease_expires_at <= reserved
            or acquired.result_ack_key
            or acquired.failure_code
            or reserved < intent.planned_at
        ):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_acquisition_invalid")
        if prior is None:
            if consumption is not None or before != after:
                raise WorkflowTransitionOwnershipReservationConflict(
                    "workflow_transition_ownership_initial_retry_invalid"
                )
        else:
            if (
                consumption is None
                or consumption.tenant_id != intent.tenant_id
                or consumption.run_id != intent.run_id
                or consumption.retry_id != intent.retry_id
                or consumption.category != _OWNERSHIP_RETRY_CATEGORY
                or after != before + 1
                or after > intent.maximum_retries
            ):
                raise WorkflowTransitionOwnershipReservationConflict(
                    "workflow_transition_ownership_recovery_retry_invalid"
                )
        object.__setattr__(self, "prior_ownership", prior)
        object.__setattr__(self, "acquired_ownership", acquired)
        expected_receipt_digest = workflow_transition_ownership_receipt_digest(self)
        if expected_receipt_digest != self.receipt_digest:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_receipt_digest_mismatch"
            )
        del generation

    @property
    def receipt_id(self) -> str:
        return self.intent.receipt_id

    @property
    def transition_id(self) -> str:
        return self.intent.transition_id

    @property
    def effect_id(self) -> str:
        return self.intent.effect_id

    @property
    def operation_fence_id(self) -> str:
        return self.intent.operation_fence_id

    @property
    def attempt_id(self) -> str:
        return self.intent.attempt_id

    @property
    def owner_id(self) -> str:
        return self.intent.owner_id

    @property
    def acquired_revision(self) -> int:
        return self.acquired_ownership.revision

    @property
    def acquired_fencing_token(self) -> int:
        return self.acquired_ownership.fencing_token

    @property
    def retry_consumed(self) -> bool:
        return self.retry_consumption is not None

    @property
    def lease_expires_at(self) -> float:
        return self.acquired_ownership.lease_expires_at

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "intent": self.intent.to_dict(),
            "creator_claim_generation": self.creator_claim_generation,
            "prior_ownership": (self.prior_ownership.to_dict() if self.prior_ownership is not None else None),
            "prior_record_digest": self.prior_record_digest,
            "acquired_ownership": self.acquired_ownership.to_dict(),
            "acquired_record_digest": self.acquired_record_digest,
            "retry_consumption": (self.retry_consumption.to_dict() if self.retry_consumption is not None else None),
            "retry_budget_used_before": self.retry_budget_used_before,
            "retry_budget_used_after": self.retry_budget_used_after,
            "reserved_at": self.reserved_at,
            "receipt_digest": self.receipt_digest,
        }

    @classmethod
    def build(
        cls,
        *,
        intent: WorkflowTransitionOwnershipReservationIntent,
        creator_claim_generation: int,
        prior_ownership: ExecutionOwnership | None,
        acquired_ownership: ExecutionOwnership,
        retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None,
        retry_budget_used_before: int,
        retry_budget_used_after: int,
        reserved_at: float,
    ) -> "WorkflowTransitionOwnershipReservationReceipt":
        prior_digest = workflow_transition_ownership_record_digest(prior_ownership)
        acquired_digest = workflow_transition_ownership_record_digest(acquired_ownership)
        values: dict[str, object] = {
            "schema": WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RECEIPT_SCHEMA,
            "intent": intent.to_dict(),
            "creator_claim_generation": creator_claim_generation,
            "prior_ownership": prior_ownership.to_dict() if prior_ownership is not None else None,
            "prior_record_digest": prior_digest,
            "acquired_ownership": acquired_ownership.to_dict(),
            "acquired_record_digest": acquired_digest,
            "retry_consumption": (retry_consumption.to_dict() if retry_consumption is not None else None),
            "retry_budget_used_before": retry_budget_used_before,
            "retry_budget_used_after": retry_budget_used_after,
            "reserved_at": reserved_at,
        }
        digest = _ownership_namespaced_digest(
            values,
            namespace="workflow-transition-ownership-reservation-receipt",
        )
        return cls(
            intent=intent,
            creator_claim_generation=creator_claim_generation,
            prior_ownership=prior_ownership,
            prior_record_digest=prior_digest,
            acquired_ownership=acquired_ownership,
            acquired_record_digest=acquired_digest,
            retry_consumption=retry_consumption,
            retry_budget_used_before=retry_budget_used_before,
            retry_budget_used_after=retry_budget_used_after,
            reserved_at=reserved_at,
            receipt_digest=digest,
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WorkflowTransitionOwnershipReservationReceipt":
        fields = {
            "schema",
            "intent",
            "creator_claim_generation",
            "prior_ownership",
            "prior_record_digest",
            "acquired_ownership",
            "acquired_record_digest",
            "retry_consumption",
            "retry_budget_used_before",
            "retry_budget_used_after",
            "reserved_at",
            "receipt_digest",
        }
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_invalid")
        try:
            intent_raw = raw["intent"]
            if not isinstance(intent_raw, Mapping):
                raise TypeError("intent")
            intent = workflow_transition_ownership_intent_from_mapping(intent_raw)
            prior_raw = raw["prior_ownership"]
            acquired_raw = raw["acquired_ownership"]
            retry_raw = raw["retry_consumption"]
            if not isinstance(acquired_raw, Mapping):
                raise TypeError("acquired")
            return cls(
                intent=intent,
                creator_claim_generation=raw["creator_claim_generation"],
                prior_ownership=(None if prior_raw is None else ExecutionOwnership.from_exact_mapping(prior_raw)),
                prior_record_digest=raw["prior_record_digest"],
                acquired_ownership=ExecutionOwnership.from_exact_mapping(acquired_raw),
                acquired_record_digest=raw["acquired_record_digest"],
                retry_consumption=(
                    None if retry_raw is None else WorkflowTransitionOwnershipRetryConsumption.from_mapping(retry_raw)
                ),
                retry_budget_used_before=raw["retry_budget_used_before"],
                retry_budget_used_after=raw["retry_budget_used_after"],
                reserved_at=raw["reserved_at"],
                receipt_digest=raw["receipt_digest"],
                schema=raw["schema"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_receipt_invalid"
            ) from exc


@dataclass(frozen=True, slots=True)
class WorkflowTransitionOwnershipReservationObservation:
    intent: WorkflowTransitionOwnershipReservationIntent
    claim_generation: int
    current: ExecutionOwnership | None
    current_history: ExecutionOwnership | None
    acquired_history: ExecutionOwnership | None
    retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None
    retry_budget: RetryBudgetSnapshot
    receipt: WorkflowTransitionOwnershipReservationReceipt | None
    receipt_alias_digests: tuple[str, ...]
    observation_digest: str
    schema: str = WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_OBSERVATION_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_intent_invalid"
            )
        generation = _ownership_positive_integer(self.claim_generation, "claim_generation")
        if self.schema != WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_OBSERVATION_SCHEMA:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_schema_unsupported"
            )
        current = _exact_optional_ownership(self.current, "current")
        current_history = _exact_optional_ownership(self.current_history, "current_history")
        acquired_history = _exact_optional_ownership(self.acquired_history, "acquired_history")
        if self.retry_consumption is not None and not isinstance(
            self.retry_consumption, WorkflowTransitionOwnershipRetryConsumption
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_retry_invalid"
            )
        if type(self.retry_budget) is not RetryBudgetSnapshot or (
            self.retry_budget.schema != RETRY_BUDGET_SCHEMA
            or self.retry_budget.tenant_id != self.intent.tenant_id
            or self.retry_budget.run_id != self.intent.run_id
            or isinstance(self.retry_budget.used, bool)
            or not isinstance(self.retry_budget.used, int)
            or self.retry_budget.used < 0
            or isinstance(self.retry_budget.maximum, bool)
            or not isinstance(self.retry_budget.maximum, int)
            or self.retry_budget.maximum != self.intent.maximum_retries
            or self.retry_budget.used > self.retry_budget.maximum
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_budget_invalid"
            )
        receipt = self.receipt
        if receipt is not None and (
            not isinstance(receipt, WorkflowTransitionOwnershipReservationReceipt)
            or receipt.intent != self.intent
            or receipt.creator_claim_generation > generation
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_receipt_invalid"
            )
        if (current is None) != (current_history is None) or (current is not None and current != current_history):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_current_history_invalid"
            )
        if receipt is None:
            if acquired_history is not None or self.retry_consumption is not None:
                raise WorkflowTransitionOwnershipReservationConflict(
                    "workflow_transition_ownership_observation_partial"
                )
        elif acquired_history != receipt.acquired_ownership or self.retry_consumption != receipt.retry_consumption:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_evidence_invalid"
            )
        if (
            not isinstance(self.receipt_alias_digests, tuple)
            or any(not isinstance(value, str) for value in self.receipt_alias_digests)
            or self.receipt_alias_digests != tuple(sorted(set(self.receipt_alias_digests)))
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_alias_invalid"
            )
        for value in self.receipt_alias_digests:
            _ownership_sha256(value, "receipt_alias_digest")
        if (receipt is None and self.receipt_alias_digests) or (
            receipt is not None and receipt.receipt_digest not in self.receipt_alias_digests
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_alias_invalid"
            )
        _ownership_sha256(self.observation_digest, "observation_digest")
        expected_digest = _ownership_observation_digest(
            intent=self.intent,
            claim_generation=generation,
            current=current,
            current_history=current_history,
            acquired_history=acquired_history,
            retry_consumption=self.retry_consumption,
            retry_budget=self.retry_budget,
            receipt=receipt,
            receipt_alias_digests=self.receipt_alias_digests,
        )
        if expected_digest != self.observation_digest:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_observation_digest_mismatch"
            )
        object.__setattr__(self, "current", current)
        object.__setattr__(self, "current_history", current_history)
        object.__setattr__(self, "acquired_history", acquired_history)


@dataclass(frozen=True, slots=True)
class WorkflowTransitionOwnershipReservationEvidence:
    intent: WorkflowTransitionOwnershipReservationIntent
    receipt: WorkflowTransitionOwnershipReservationReceipt | None
    prior_history: ExecutionOwnership | None
    acquired_history: ExecutionOwnership | None
    retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None
    receipt_alias_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_evidence_intent_invalid"
            )
        prior = _exact_optional_ownership(self.prior_history, "evidence_prior")
        acquired = _exact_optional_ownership(self.acquired_history, "evidence_acquired")
        if self.retry_consumption is not None and not isinstance(
            self.retry_consumption, WorkflowTransitionOwnershipRetryConsumption
        ):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_retry_invalid")
        if (
            not isinstance(self.receipt_alias_digests, tuple)
            or any(not isinstance(value, str) for value in self.receipt_alias_digests)
            or self.receipt_alias_digests != tuple(sorted(set(self.receipt_alias_digests)))
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_evidence_alias_conflict"
            )
        for value in self.receipt_alias_digests:
            _ownership_sha256(value, "receipt_alias_digest")
        if self.receipt is None:
            if (
                prior is not None
                or acquired is not None
                or self.retry_consumption is not None
                or self.receipt_alias_digests
            ):
                raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_partial")
            return
        if not isinstance(self.receipt, WorkflowTransitionOwnershipReservationReceipt):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_evidence_receipt_conflict"
            )
        if self.receipt.intent != self.intent:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_evidence_intent_conflict"
            )
        if acquired != self.receipt.acquired_ownership:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_evidence_history_conflict"
            )
        if self.retry_consumption != self.receipt.retry_consumption:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_evidence_retry_conflict"
            )
        if prior != self.receipt.prior_ownership:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_evidence_prior_conflict"
            )
        if self.receipt_alias_digests != (self.receipt.receipt_digest,):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_evidence_alias_conflict"
            )
        object.__setattr__(self, "prior_history", prior)
        object.__setattr__(self, "acquired_history", acquired)


@runtime_checkable
class WorkflowTransitionOwnershipReservationReadPort(Protocol):
    def observe_transition_reservation(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation: ...


@runtime_checkable
class WorkflowTransitionOwnershipReservationHistoricalReadPort(Protocol):
    def read_transition_reservation_history(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence: ...


@runtime_checkable
class WorkflowTransitionOwnershipReservationCommitPort(Protocol):
    def reserve_transition_effect(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
        expected_observation_digest: str,
        reserved_at: float,
    ) -> WorkflowTransitionOwnershipReservationReceipt: ...


def workflow_transition_ownership_intent_from_mapping(
    raw: Mapping[str, Any],
) -> WorkflowTransitionOwnershipReservationIntent:
    fields = {
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
        "ownership_intent_digest",
        "owner_id",
        "operation_fence_id",
        "attempt_id",
        "retry_id",
        "transition_request_fingerprint",
        "effect_payload_digest",
        "idempotency_key",
        "lease_seconds",
        "maximum_retries",
        "planned_at",
    }
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
    try:
        return WorkflowTransitionOwnershipReservationIntent(
            receipt_id=raw["receipt_id"],
            transition_id=raw["transition_id"],
            effect_id=raw["effect_id"],
            runtime_id=raw["runtime_id"],
            tenant_id=raw["tenant_id"],
            workflow_id=raw["workflow_id"],
            run_id=raw["run_id"],
            step_id=raw["step_id"],
            effect_ordinal=raw["effect_ordinal"],
            ownership_intent_digest=raw["ownership_intent_digest"],
            owner_id=raw["owner_id"],
            operation_fence_id=raw["operation_fence_id"],
            attempt_id=raw["attempt_id"],
            retry_id=raw["retry_id"],
            transition_request_fingerprint=raw["transition_request_fingerprint"],
            effect_payload_digest=raw["effect_payload_digest"],
            idempotency_key=raw["idempotency_key"],
            lease_seconds=raw["lease_seconds"],
            maximum_retries=raw["maximum_retries"],
            planned_at=raw["planned_at"],
            schema=raw["schema"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid") from exc
