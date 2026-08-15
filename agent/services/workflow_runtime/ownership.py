"""Hub-owned run/step leases, fencing, acknowledgements, and retry budgets.

Every worker/runtime claim receives a unique attempt ID and monotonically
increasing fencing token. Tool/native runtimes pass that fencing token to the
side-effect ledger and checkpoint store. Stale heartbeats, results, failures, and
side-effect completions fail closed.

The retry budget is shared across categories (hub task, runtime, tool, provider,
or Temporal activity). ``retry_id`` makes delivery idempotent while ``used`` is a
single combined counter, preventing nested runtimes from multiplying retries.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    InvalidTransitionError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent
from ananta_contracts.hub_task_gateway import RETRY_CATEGORIES

EXECUTION_OWNERSHIP_SCHEMA = "ananta.execution_ownership.v1"
RETRY_BUDGET_SCHEMA = "ananta.retry_budget.v1"
OWNERSHIP_STATUSES = frozenset({"active", "completed", "failed", "orphaned", "dead_letter"})
WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_INTENT_SCHEMA = "ananta.workflow_transition_ownership_reservation_intent.v1"
WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RECEIPT_SCHEMA = "ananta.workflow_transition_ownership_reservation_receipt.v1"
WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_OBSERVATION_SCHEMA = (
    "ananta.workflow_transition_ownership_reservation_observation.v1"
)

_OWNERSHIP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_OWNERSHIP_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_OWNERSHIP_MAX_COUNTER = 2**63 - 1
_OWNERSHIP_MAX_LEGACY_REVISION = 2_147_483_647
_OWNERSHIP_MAX_RETRIES = 2_147_483_647
_OWNERSHIP_RETRY_CATEGORY = "hub_task"


def ownership_event(
    ownership: "ExecutionOwnership",
    *,
    correlation_id: str,
    causation_id: str,
    actor: str = "hub",
) -> CanonicalWorkflowEvent:
    """Map an atomically committed lease revision to a canonical event."""

    event_suffix = {
        "active": "ownership_claimed",
        "completed": "result_acknowledged",
        "failed": "attempt_failed",
        "orphaned": "orphaned",
        "dead_letter": "dead_lettered",
    }[ownership.status]
    return CanonicalWorkflowEvent.build(
        tenant_id=ownership.tenant_id,
        workflow_id=ownership.workflow_id,
        run_id=ownership.run_id,
        step_id=ownership.step_id,
        attempt=ownership.fencing_token,
        event_type=f"workflow.step.{event_suffix}",
        correlation_id=correlation_id,
        causation_id=causation_id,
        dedupe_key=(f"ownership:{ownership.run_id}:{ownership.step_id}:{ownership.attempt_id}:{ownership.revision}"),
        actor=actor,
        payload={
            "attempt_id": ownership.attempt_id,
            "owner_id": ownership.owner_id,
            "fencing_token": ownership.fencing_token,
            "lease_expires_at": ownership.lease_expires_at,
            "result_ack_key": ownership.result_ack_key,
            "failure_code": ownership.failure_code,
        },
        occurred_at=ownership.last_heartbeat_at,
        event_id=(f"wfe-ownership-{ownership.run_id}-{ownership.step_id}-{ownership.attempt_id}-{ownership.revision}"),
    )


@dataclass(frozen=True)
class ExecutionOwnership:
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    attempt_id: str
    owner_id: str
    fencing_token: int
    revision: int
    status: str
    lease_expires_at: float
    last_heartbeat_at: float
    result_ack_key: str = ""
    failure_code: str = ""
    schema: str = EXECUTION_OWNERSHIP_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.attempt_id,
            self.owner_id,
        )
        if any(not value for value in required):
            raise ValueError("execution_ownership_binding_required")
        if self.status not in OWNERSHIP_STATUSES:
            raise ValueError("execution_ownership_status_invalid")
        if self.fencing_token < 1 or self.revision < 1:
            raise ValueError("execution_ownership_fencing_invalid")
        if self.schema != EXECUTION_OWNERSHIP_SCHEMA:
            raise ValueError("execution_ownership_schema_unsupported")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ExecutionOwnership":
        value = cls(
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            attempt_id=str(raw.get("attempt_id") or ""),
            owner_id=str(raw.get("owner_id") or ""),
            fencing_token=int(raw.get("fencing_token") or 0),
            revision=int(raw.get("revision") or 0),
            status=str(raw.get("status") or ""),
            lease_expires_at=float(raw.get("lease_expires_at") or 0),
            last_heartbeat_at=float(raw.get("last_heartbeat_at") or 0),
            result_ack_key=str(raw.get("result_ack_key") or ""),
            failure_code=str(raw.get("failure_code") or ""),
            schema=str(raw.get("schema") or EXECUTION_OWNERSHIP_SCHEMA),
        )
        value.assert_valid()
        return value

    @classmethod
    def from_exact_mapping(cls, raw: Mapping[str, Any]) -> "ExecutionOwnership":
        """Hydrate transition authority without defaults, coercion, or aliases."""

        fields = {
            "schema",
            "tenant_id",
            "workflow_id",
            "run_id",
            "step_id",
            "attempt_id",
            "owner_id",
            "fencing_token",
            "revision",
            "status",
            "lease_expires_at",
            "last_heartbeat_at",
            "result_ack_key",
            "failure_code",
        }
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise ValueError("execution_ownership_exact_mapping_invalid")
        value = cls(
            tenant_id=_ownership_legacy_text(raw["tenant_id"], "tenant_id", empty=False),
            workflow_id=_ownership_legacy_text(raw["workflow_id"], "workflow_id", empty=False),
            run_id=_ownership_legacy_text(raw["run_id"], "run_id", empty=False),
            step_id=_ownership_legacy_text(raw["step_id"], "step_id", empty=False),
            attempt_id=_ownership_legacy_text(raw["attempt_id"], "attempt_id", empty=False),
            owner_id=_ownership_legacy_text(raw["owner_id"], "owner_id", empty=False),
            fencing_token=_ownership_positive_legacy_counter(raw["fencing_token"], "fencing_token"),
            revision=_ownership_positive_legacy_counter(raw["revision"], "revision"),
            status=_ownership_status(raw["status"]),
            lease_expires_at=_ownership_finite_timestamp(raw["lease_expires_at"], "lease_expires_at"),
            last_heartbeat_at=_ownership_finite_timestamp(raw["last_heartbeat_at"], "last_heartbeat_at"),
            result_ack_key=_ownership_legacy_text(raw["result_ack_key"], "result_ack_key", empty=True),
            failure_code=_ownership_legacy_text(raw["failure_code"], "failure_code", empty=True),
            schema=_ownership_exact_schema(raw["schema"], EXECUTION_OWNERSHIP_SCHEMA),
        )
        if value.status == "active" and (value.result_ack_key or value.failure_code):
            raise ValueError("execution_ownership_active_terminal_fields_invalid")
        if value.status == "completed" and (not value.result_ack_key or value.failure_code):
            raise ValueError("execution_ownership_completion_fields_invalid")
        if value.status in {"failed", "orphaned", "dead_letter"} and (not value.failure_code or value.result_ack_key):
            raise ValueError("execution_ownership_failure_fields_invalid")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "owner_id": self.owner_id,
            "fencing_token": self.fencing_token,
            "revision": self.revision,
            "status": self.status,
            "lease_expires_at": self.lease_expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "result_ack_key": self.result_ack_key,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True)
class OwnershipClaim:
    ownership: ExecutionOwnership
    acquired: bool
    reason: str


@dataclass(frozen=True)
class RetryBudgetSnapshot:
    tenant_id: str
    run_id: str
    used: int
    maximum: int
    schema: str = RETRY_BUDGET_SCHEMA

    @property
    def remaining(self) -> int:
        return max(0, self.maximum - self.used)


class WorkflowTransitionOwnershipReservationError(RuntimeError):
    """Stable transition-only ownership persistence failure."""


class WorkflowTransitionOwnershipReservationConflict(WorkflowTransitionOwnershipReservationError):
    """A proven binding, projection, or partial-state conflict."""


class WorkflowTransitionOwnershipReservationStale(WorkflowTransitionOwnershipReservationError):
    """A retryable optimistic observation or compare-and-set loss."""


class WorkflowTransitionOwnershipReservationHeld(WorkflowTransitionOwnershipReservationError):
    """Another exact live owner currently holds the requested step."""


class WorkflowTransitionOwnershipReservationUnavailable(WorkflowTransitionOwnershipReservationError):
    """The authoritative reservation snapshot could not be read."""


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


class RetryBudgetOwner(Protocol):
    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot: ...

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot: ...


class ExecutionOwnershipStore(RetryBudgetOwner, Protocol):
    def claim(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        now: float | None = None,
    ) -> OwnershipClaim: ...

    def heartbeat(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        lease_seconds: float,
        now: float | None = None,
    ) -> ExecutionOwnership: ...

    def acknowledge_result(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        result_ack_key: str,
        now: float | None = None,
    ) -> ExecutionOwnership: ...

    def fail_attempt(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        failure_code: str,
        dead_letter: bool = False,
        now: float | None = None,
    ) -> ExecutionOwnership: ...

    def reconcile_orphan(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        now: float | None = None,
    ) -> ExecutionOwnership | None: ...

    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None: ...


class InMemoryExecutionOwnershipStore:
    def __init__(self) -> None:
        self._current: dict[tuple[str, str, str], ExecutionOwnership] = {}
        self._history: dict[tuple[str, str, str], list[ExecutionOwnership]] = {}
        self._retry_used: dict[tuple[str, str], int] = {}
        self._retry_maximum: dict[tuple[str, str], int] = {}
        self._retry_ids: dict[tuple[str, str, str], str] = {}
        self._transition_reservation_receipts: dict[str, WorkflowTransitionOwnershipReservationReceipt] = {}
        self._lock = threading.RLock()

    def claim(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        now: float | None = None,
    ) -> OwnershipClaim:
        timestamp = _validate_lease(lease_seconds, now)
        key = (str(tenant_id), str(run_id), str(step_id))
        with self._lock:
            current = self._current.get(key)
            if current is not None and current.workflow_id != str(workflow_id):
                raise OptimisticConcurrencyError("execution_ownership_workflow_binding_conflict")
            if current and current.status == "completed":
                return OwnershipClaim(current, False, "already_completed")
            if current and current.status == "active" and current.lease_expires_at > timestamp:
                reason = "already_owned" if current.owner_id == str(owner_id) else "lease_held"
                return OwnershipClaim(current, False, reason)
            next_fence = (current.fencing_token + 1) if current else 1
            attempt_id = f"att-{uuid.uuid4().hex}"
            if current is not None:
                self.consume_retry(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    retry_id=attempt_id,
                    category="hub_task",
                    maximum=maximum_retries,
                )
            ownership = ExecutionOwnership(
                tenant_id=str(tenant_id),
                workflow_id=str(workflow_id),
                run_id=str(run_id),
                step_id=str(step_id),
                attempt_id=attempt_id,
                owner_id=str(owner_id),
                fencing_token=next_fence,
                revision=(current.revision + 1) if current else 1,
                status="active",
                lease_expires_at=timestamp + float(lease_seconds),
                last_heartbeat_at=timestamp,
            )
            ownership.assert_valid()
            self._save(key, ownership)
            return OwnershipClaim(ownership, True, "acquired" if current is None else "recovered")

    def observe_transition_reservation(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        with self._lock:
            return self._transition_reservation_observation_unlocked(
                intent,
                claim_generation=claim_generation,
            )

    def read_transition_reservation_history(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        with self._lock:
            if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
                raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
            key = (intent.tenant_id, intent.run_id, intent.step_id)
            retry_category = self._retry_ids.get((intent.tenant_id, intent.run_id, intent.retry_id))
            consumption = (
                None
                if retry_category is None
                else WorkflowTransitionOwnershipRetryConsumption(
                    tenant_id=intent.tenant_id,
                    run_id=intent.run_id,
                    retry_id=intent.retry_id,
                    category=retry_category,
                )
            )
            receipts = _transition_ownership_relevant_receipts(
                intent,
                current=None,
                receipts=tuple(self._transition_reservation_receipts.values()),
                include_prospective=False,
            )
            revisions = {
                revision
                for receipt in receipts
                for revision in (
                    receipt.acquired_revision,
                    receipt.prior_ownership.revision if receipt.prior_ownership is not None else 0,
                )
                if revision > 0
            }
            history = tuple(
                value
                for value in self._history.get(key, ())
                if value.revision in revisions or value.attempt_id == intent.attempt_id
            )
            return _transition_ownership_evidence(
                intent,
                history=history,
                retry_consumption=consumption,
                receipts=receipts,
            )

    def reserve_transition_effect(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
        expected_observation_digest: str,
        reserved_at: float,
    ) -> WorkflowTransitionOwnershipReservationReceipt:
        with self._lock:
            evidence = self.read_transition_reservation_history(intent)
            if evidence.receipt is not None:
                if evidence.receipt.creator_claim_generation > creator_claim_generation:
                    raise WorkflowTransitionOwnershipReservationConflict(
                        "workflow_transition_ownership_receipt_generation_conflict"
                    )
                return evidence.receipt
            observation = self._transition_reservation_observation_unlocked(
                intent,
                claim_generation=creator_claim_generation,
            )
            acquired, consumption, budget, receipt = _transition_ownership_reservation_values(
                observation,
                creator_claim_generation=creator_claim_generation,
                expected_observation_digest=expected_observation_digest,
                reserved_at=reserved_at,
            )
            if observation.receipt is not None:
                return observation.receipt

            key = (intent.tenant_id, intent.run_id, intent.step_id)
            budget_key = (intent.tenant_id, intent.run_id)
            current_values = dict(self._current)
            history_values = {slot: list(values) for slot, values in self._history.items()}
            retry_used = dict(self._retry_used)
            retry_maximum = dict(self._retry_maximum)
            retry_ids = dict(self._retry_ids)
            receipts = dict(self._transition_reservation_receipts)
            self._transition_reservation_fault("before_retry", consumption)
            if consumption is not None:
                retry_key = (intent.tenant_id, intent.run_id, intent.retry_id)
                if retry_key in retry_ids:
                    raise WorkflowTransitionOwnershipReservationConflict(
                        "workflow_transition_ownership_retry_without_receipt"
                    )
                retry_ids[retry_key] = consumption.category
                retry_used[budget_key] = budget.used
                retry_maximum[budget_key] = budget.maximum
            self._transition_reservation_fault("after_retry", consumption)
            current_values[key] = acquired
            self._transition_reservation_fault("after_current", acquired)
            history_values.setdefault(key, []).append(acquired)
            self._transition_reservation_fault("after_history", acquired)
            receipts[receipt.receipt_id] = receipt
            self._transition_reservation_fault("after_receipt", receipt)
            self._transition_reservation_fault("before_commit", receipt)

            previous = (
                self._current,
                self._history,
                self._retry_used,
                self._retry_maximum,
                self._retry_ids,
                self._transition_reservation_receipts,
            )
            try:
                self._current = current_values
                self._history = history_values
                self._retry_used = retry_used
                self._retry_maximum = retry_maximum
                self._retry_ids = retry_ids
                self._transition_reservation_receipts = receipts
            except BaseException:
                (
                    self._current,
                    self._history,
                    self._retry_used,
                    self._retry_maximum,
                    self._retry_ids,
                    self._transition_reservation_receipts,
                ) = previous
                raise
        self._transition_reservation_fault("after_commit", receipt)
        return receipt

    def _transition_reservation_observation_unlocked(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
        key = (intent.tenant_id, intent.run_id, intent.step_id)
        current = self._current.get(key)
        all_history = tuple(self._history.get(key, ()))
        relevant_receipts = _transition_ownership_relevant_receipts(
            intent,
            current=current,
            receipts=tuple(self._transition_reservation_receipts.values()),
            include_prospective=True,
        )
        anchor_revisions = {
            revision
            for receipt in relevant_receipts
            for revision in (
                receipt.acquired_revision,
                receipt.prior_ownership.revision if receipt.prior_ownership is not None else 0,
            )
            if revision > 0
        }
        if current is not None:
            anchor_revisions.add(current.revision)
        history = tuple(
            value
            for value in all_history
            if value.revision in anchor_revisions or value.attempt_id == intent.attempt_id
        )
        if current is None and not history and all_history:
            history = (all_history[-1],)
        retry_category = self._retry_ids.get((intent.tenant_id, intent.run_id, intent.retry_id))
        consumption = (
            None
            if retry_category is None
            else WorkflowTransitionOwnershipRetryConsumption(
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                retry_id=intent.retry_id,
                category=retry_category,
            )
        )
        budget_key = (intent.tenant_id, intent.run_id)
        configured_maximum = self._retry_maximum.get(budget_key)
        maximum = intent.maximum_retries if configured_maximum is None else configured_maximum
        budget = RetryBudgetSnapshot(
            intent.tenant_id,
            intent.run_id,
            used=self._retry_used.get(budget_key, 0),
            maximum=maximum,
        )
        return _transition_ownership_observation(
            intent,
            claim_generation=claim_generation,
            current=current,
            history=history,
            retry_consumption=consumption,
            retry_budget=budget,
            receipts=relevant_receipts,
        )

    def _transition_reservation_fault(self, stage: str, value: object) -> None:
        del stage, value

    def heartbeat(self, **values: Any) -> ExecutionOwnership:
        timestamp = _validate_lease(float(values["lease_seconds"]), values.get("now"))
        with self._lock:
            key, current = self._owned(values)
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise FencingTokenError("heartbeat_owner_no_longer_active")
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("ownership_lease_expired")
            updated = replace(
                current,
                revision=current.revision + 1,
                last_heartbeat_at=timestamp,
                lease_expires_at=timestamp + float(values["lease_seconds"]),
            )
            self._save(key, updated)
            return updated

    def acknowledge_result(self, **values: Any) -> ExecutionOwnership:
        result_ack_key = str(values.get("result_ack_key") or "")
        if not result_ack_key:
            raise ValueError("result_ack_key_required")
        timestamp = _timestamp(values.get("now"))
        with self._lock:
            key, current = self._owned(values)
            if current.status == "completed" and current.result_ack_key == result_ack_key:
                return current
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active" or current.lease_expires_at <= timestamp:
                raise FencingTokenError("result_owner_not_active")
            updated = replace(
                current,
                revision=current.revision + 1,
                status="completed",
                result_ack_key=result_ack_key,
                lease_expires_at=timestamp,
            )
            self._save(key, updated)
            return updated

    def fail_attempt(self, *, failure_code: str, dead_letter: bool = False, **values: Any) -> ExecutionOwnership:
        with self._lock:
            key, current = self._owned(values)
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise InvalidTransitionError("failure_requires_active_ownership")
            timestamp = _timestamp(values.get("now"))
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("failure_owner_lease_expired")
            updated = replace(
                current,
                revision=current.revision + 1,
                status="dead_letter" if dead_letter else "failed",
                failure_code=str(failure_code or "execution_failed"),
                lease_expires_at=timestamp,
            )
            self._save(key, updated)
            return updated

    def reconcile_orphan(
        self, *, tenant_id: str, run_id: str, step_id: str, now: float | None = None
    ) -> ExecutionOwnership | None:
        timestamp = float(now if now is not None else time.time())
        key = (str(tenant_id), str(run_id), str(step_id))
        with self._lock:
            current = self._current.get(key)
            if current is None or current.status != "active" or current.lease_expires_at > timestamp:
                return None
            updated = replace(
                current,
                revision=current.revision + 1,
                status="orphaned",
                failure_code="lease_expired",
            )
            self._save(key, updated)
            return updated

    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        with self._lock:
            return self._current.get((str(tenant_id), str(run_id), str(step_id)))

    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot:
        if maximum < 0 or not retry_id or category not in RETRY_CATEGORIES:
            raise ValueError("retry_budget_input_invalid")
        key = (str(tenant_id), str(run_id))
        dedupe = (*key, str(retry_id))
        with self._lock:
            current = self._retry_used.get(key, 0)
            configured_maximum = self._retry_maximum.get(key)
            if configured_maximum is not None and configured_maximum != int(maximum):
                raise InvalidTransitionError("retry_budget_maximum_mismatch")
            existing_category = self._retry_ids.get(dedupe)
            if existing_category is not None and existing_category != str(category):
                raise InvalidTransitionError("retry_budget_retry_id_binding_mismatch")
            if existing_category is not None:
                return RetryBudgetSnapshot(*key, used=current, maximum=int(maximum))
            if current >= int(maximum):
                raise InvalidTransitionError("retry_budget_exhausted")
            self._retry_maximum[key] = int(maximum)
            self._retry_ids[dedupe] = str(category)
            self._retry_used[key] = current + 1
            return RetryBudgetSnapshot(*key, used=current + 1, maximum=int(maximum))

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot:
        with self._lock:
            used = self._retry_used.get((str(tenant_id), str(run_id)), 0)
            configured_maximum = self._retry_maximum.get((str(tenant_id), str(run_id)))
            if configured_maximum is not None and configured_maximum != int(maximum):
                raise InvalidTransitionError("retry_budget_maximum_mismatch")
        return RetryBudgetSnapshot(str(tenant_id), str(run_id), used=used, maximum=int(maximum))

    def _owned(self, values: dict[str, Any]) -> tuple[tuple[str, str, str], ExecutionOwnership]:
        key = (str(values["tenant_id"]), str(values["run_id"]), str(values["step_id"]))
        current = self._current.get(key)
        if current is None:
            raise KeyError("execution_ownership_not_found")
        _assert_owner(
            current,
            attempt_id=str(values["attempt_id"]),
            owner_id=str(values["owner_id"]),
            fencing_token=int(values["fencing_token"]),
        )
        return key, current

    def _save(self, key: tuple[str, str, str], value: ExecutionOwnership) -> None:
        value.assert_valid()
        self._current[key] = value
        self._history.setdefault(key, []).append(value)


class SQLiteExecutionOwnershipStore:
    """SQLite lease store; ownership and combined retry budget mutate atomically."""

    def __init__(self, database: str | Path):
        self._connection = sqlite3.connect(str(database), timeout=30, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._lock = threading.RLock()
        _preflight_direct_ownership_schema(self._connection)
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow_execution_ownership (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                fencing_token INTEGER NOT NULL,
                ownership_json TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id, step_id)
            );
            CREATE TABLE IF NOT EXISTS workflow_execution_attempt_history (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                attempt_id TEXT NOT NULL,
                ownership_json TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id, step_id, revision)
            );
            CREATE TABLE IF NOT EXISTS workflow_retry_budgets (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                used INTEGER NOT NULL,
                maximum INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, run_id)
            );
            CREATE TABLE IF NOT EXISTS workflow_retry_consumptions (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                retry_id TEXT NOT NULL,
                category TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id, retry_id)
            );
            CREATE TABLE IF NOT EXISTS workflow_transition_ownership_reservations (
                receipt_id TEXT NOT NULL PRIMARY KEY,
                transition_id TEXT NOT NULL,
                effect_id TEXT NOT NULL,
                operation_fence_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                runtime_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                ownership_intent_digest TEXT NOT NULL,
                acquisition_record_digest TEXT NOT NULL,
                receipt_digest TEXT NOT NULL,
                creator_claim_generation INTEGER NOT NULL,
                acquired_revision INTEGER NOT NULL,
                acquired_fencing_token INTEGER NOT NULL,
                maximum_retries INTEGER NOT NULL,
                retry_consumed INTEGER NOT NULL,
                planned_at REAL NOT NULL,
                reserved_at REAL NOT NULL,
                lease_expires_at REAL NOT NULL,
                receipt_json TEXT NOT NULL,
                CONSTRAINT uq_workflow_transition_ownership_res_effect UNIQUE (effect_id),
                CONSTRAINT uq_workflow_transition_ownership_res_fence UNIQUE (operation_fence_id),
                CONSTRAINT uq_workflow_transition_ownership_res_attempt UNIQUE (attempt_id),
                CONSTRAINT uq_workflow_transition_ownership_res_revision
                    UNIQUE (tenant_id, run_id, step_id, acquired_revision),
                CONSTRAINT uq_workflow_transition_ownership_res_current_fence
                    UNIQUE (tenant_id, run_id, step_id, acquired_fencing_token),
                CONSTRAINT ck_workflow_transition_ownership_res_valid CHECK (
                    creator_claim_generation > 0
                    AND acquired_revision > 0
                    AND acquired_revision <= 2147483647
                    AND acquired_fencing_token > 0
                    AND acquired_fencing_token <= 2147483647
                    AND maximum_retries >= 0
                    AND maximum_retries <= 2147483647
                    AND retry_consumed IN (0, 1)
                    AND planned_at > 0
                    AND reserved_at >= planned_at
                    AND lease_expires_at > reserved_at
                )
            );
            CREATE INDEX IF NOT EXISTS ix_workflow_transition_ownership_res_transition
                ON workflow_transition_ownership_reservations (transition_id);
            CREATE INDEX IF NOT EXISTS ix_workflow_transition_ownership_res_tenant_run
                ON workflow_transition_ownership_reservations (tenant_id, run_id);
            CREATE INDEX IF NOT EXISTS ix_workflow_transition_ownership_res_scope
                ON workflow_transition_ownership_reservations (tenant_id, run_id, step_id);
            CREATE INDEX IF NOT EXISTS ix_workflow_transition_ownership_res_owner
                ON workflow_transition_ownership_reservations (owner_id);
            """
        )
        _preflight_direct_ownership_schema(self._connection)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def claim(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        now: float | None = None,
    ) -> OwnershipClaim:
        timestamp = _validate_lease(lease_seconds, now)
        key = (str(tenant_id), str(run_id), str(step_id))
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read(*key)
                if current is not None and current.workflow_id != str(workflow_id):
                    raise OptimisticConcurrencyError("execution_ownership_workflow_binding_conflict")
                if current and current.status == "completed":
                    self._connection.commit()
                    return OwnershipClaim(current, False, "already_completed")
                if current and current.status == "active" and current.lease_expires_at > timestamp:
                    reason = "already_owned" if current.owner_id == str(owner_id) else "lease_held"
                    self._connection.commit()
                    return OwnershipClaim(current, False, reason)
                attempt_id = f"att-{uuid.uuid4().hex}"
                if current is not None:
                    self._consume_retry_in_transaction(
                        tenant_id=str(tenant_id),
                        run_id=str(run_id),
                        retry_id=attempt_id,
                        category="hub_task",
                        maximum=int(maximum_retries),
                    )
                ownership = ExecutionOwnership(
                    tenant_id=str(tenant_id),
                    workflow_id=str(workflow_id),
                    run_id=str(run_id),
                    step_id=str(step_id),
                    attempt_id=attempt_id,
                    owner_id=str(owner_id),
                    fencing_token=(current.fencing_token + 1) if current else 1,
                    revision=(current.revision + 1) if current else 1,
                    status="active",
                    lease_expires_at=timestamp + float(lease_seconds),
                    last_heartbeat_at=timestamp,
                )
                self._write(ownership, expected_revision=current.revision if current else 0)
                self._connection.commit()
                return OwnershipClaim(ownership, True, "acquired" if current is None else "recovered")
            except Exception:
                self._connection.rollback()
                raise

    def observe_transition_reservation(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        try:
            return self._observe_transition_reservation_once(
                intent,
                claim_generation=claim_generation,
            )
        except WorkflowTransitionOwnershipReservationConflict:
            raise
        except sqlite3.Error as exc:
            raise WorkflowTransitionOwnershipReservationUnavailable(
                "workflow_transition_ownership_read_unavailable"
            ) from exc

    def read_transition_reservation_history(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        try:
            return self._read_transition_reservation_history_once(intent)
        except WorkflowTransitionOwnershipReservationConflict:
            raise
        except sqlite3.Error as exc:
            raise WorkflowTransitionOwnershipReservationUnavailable(
                "workflow_transition_ownership_history_unavailable"
            ) from exc

    def reserve_transition_effect(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
        expected_observation_digest: str,
        reserved_at: float,
    ) -> WorkflowTransitionOwnershipReservationReceipt:
        try:
            return self._reserve_transition_effect_once(
                intent,
                creator_claim_generation=creator_claim_generation,
                expected_observation_digest=expected_observation_digest,
                reserved_at=reserved_at,
            )
        except WorkflowTransitionOwnershipReservationStale:
            raise
        except WorkflowTransitionOwnershipReservationConflict:
            raise
        except (sqlite3.IntegrityError, OptimisticConcurrencyError) as exc:
            try:
                evidence = self._read_transition_reservation_history_once(intent)
                if evidence.receipt is not None and (
                    evidence.receipt.creator_claim_generation <= creator_claim_generation
                ):
                    return evidence.receipt
                observation = self._observe_transition_reservation_once(
                    intent,
                    claim_generation=creator_claim_generation,
                )
            except WorkflowTransitionOwnershipReservationConflict:
                raise
            except sqlite3.Error as read_exc:
                raise WorkflowTransitionOwnershipReservationUnavailable(
                    "workflow_transition_ownership_commit_unavailable"
                ) from read_exc
            if observation.receipt is not None:
                return observation.receipt
            raise WorkflowTransitionOwnershipReservationStale("workflow_transition_ownership_commit_stale") from exc
        except sqlite3.Error as exc:
            try:
                evidence = self._read_transition_reservation_history_once(intent)
            except WorkflowTransitionOwnershipReservationConflict:
                raise
            except sqlite3.Error as read_exc:
                raise WorkflowTransitionOwnershipReservationUnavailable(
                    "workflow_transition_ownership_commit_unavailable"
                ) from read_exc
            if evidence.receipt is not None and evidence.receipt.creator_claim_generation <= creator_claim_generation:
                return evidence.receipt
            raise WorkflowTransitionOwnershipReservationUnavailable(
                "workflow_transition_ownership_commit_unavailable"
            ) from exc

    def _observe_transition_reservation_once(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                observation = self._read_transition_reservation_observation(
                    intent,
                    claim_generation=claim_generation,
                )
                self._connection.commit()
                return observation
            except BaseException:
                self._connection.rollback()
                raise

    def _read_transition_reservation_history_once(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                evidence = self._read_transition_reservation_history_in_transaction(intent)
                self._connection.commit()
                return evidence
            except BaseException:
                self._connection.rollback()
                raise

    def _read_transition_reservation_history_in_transaction(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
        rows = self._connection.execute(
            """
            SELECT * FROM workflow_transition_ownership_reservations
            WHERE receipt_id = ? OR effect_id = ?
               OR operation_fence_id = ? OR attempt_id = ?
            ORDER BY receipt_id
            """,
            (
                intent.receipt_id,
                intent.effect_id,
                intent.operation_fence_id,
                intent.attempt_id,
            ),
        ).fetchall()
        receipts = tuple(_direct_transition_reservation_receipt(row) for row in rows)
        distinct = {value.receipt_digest: value for value in receipts}
        if len(distinct) == 1:
            receipt = next(iter(distinct.values()))
            revisions = [receipt.acquired_revision]
            if receipt.prior_ownership is not None:
                revisions.append(receipt.prior_ownership.revision)
            placeholders = ",".join("?" for _ in revisions)
            history_rows = self._connection.execute(
                f"""
                SELECT tenant_id, run_id, step_id, revision, attempt_id, ownership_json
                FROM workflow_execution_attempt_history
                WHERE tenant_id = ? AND run_id = ? AND step_id = ?
                  AND revision IN ({placeholders})
                """,
                (
                    intent.tenant_id,
                    intent.run_id,
                    intent.step_id,
                    *revisions,
                ),
            ).fetchall()
        else:
            history_rows = self._connection.execute(
                """
                SELECT tenant_id, run_id, step_id, revision, attempt_id, ownership_json
                FROM workflow_execution_attempt_history
                WHERE tenant_id = ? AND run_id = ? AND step_id = ? AND attempt_id = ?
                LIMIT 2
                """,
                (
                    intent.tenant_id,
                    intent.run_id,
                    intent.step_id,
                    intent.attempt_id,
                ),
            ).fetchall()
        retry_row = self._connection.execute(
            """
            SELECT tenant_id, run_id, retry_id, category
            FROM workflow_retry_consumptions
            WHERE tenant_id = ? AND run_id = ? AND retry_id = ?
            """,
            (intent.tenant_id, intent.run_id, intent.retry_id),
        ).fetchone()
        return _transition_ownership_evidence(
            intent,
            history=tuple(_direct_exact_history(row) for row in history_rows),
            retry_consumption=(
                None if retry_row is None else WorkflowTransitionOwnershipRetryConsumption.from_mapping(dict(retry_row))
            ),
            receipts=receipts,
        )

    def _reserve_transition_effect_once(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
        expected_observation_digest: str,
        reserved_at: float,
    ) -> WorkflowTransitionOwnershipReservationReceipt:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                evidence = self._read_transition_reservation_history_in_transaction(intent)
                if evidence.receipt is not None:
                    if evidence.receipt.creator_claim_generation > creator_claim_generation:
                        raise WorkflowTransitionOwnershipReservationConflict(
                            "workflow_transition_ownership_receipt_generation_conflict"
                        )
                    self._connection.commit()
                    return evidence.receipt
                observation = self._read_transition_reservation_observation(
                    intent,
                    claim_generation=creator_claim_generation,
                )
                acquired, consumption, budget, receipt = _transition_ownership_reservation_values(
                    observation,
                    creator_claim_generation=creator_claim_generation,
                    expected_observation_digest=expected_observation_digest,
                    reserved_at=reserved_at,
                )
                if observation.receipt is not None:
                    self._connection.commit()
                    return observation.receipt
                self._transition_reservation_fault("before_retry", consumption)
                if consumption is not None:
                    self._connection.execute(
                        """
                        INSERT INTO workflow_retry_consumptions
                        (tenant_id, run_id, retry_id, category) VALUES (?, ?, ?, ?)
                        """,
                        (
                            consumption.tenant_id,
                            consumption.run_id,
                            consumption.retry_id,
                            consumption.category,
                        ),
                    )
                    budget_row = self._connection.execute(
                        """
                        UPDATE workflow_retry_budgets SET used = ?
                        WHERE tenant_id = ? AND run_id = ? AND used = ? AND maximum = ?
                        """,
                        (
                            budget.used,
                            intent.tenant_id,
                            intent.run_id,
                            observation.retry_budget.used,
                            intent.maximum_retries,
                        ),
                    )
                    if budget_row.rowcount == 0:
                        if observation.retry_budget.used != 0:
                            raise WorkflowTransitionOwnershipReservationStale(
                                "workflow_transition_ownership_retry_budget_cas_failed"
                            )
                        self._connection.execute(
                            """
                            INSERT INTO workflow_retry_budgets
                            (tenant_id, run_id, used, maximum) VALUES (?, ?, ?, ?)
                            """,
                            (
                                intent.tenant_id,
                                intent.run_id,
                                budget.used,
                                budget.maximum,
                            ),
                        )
                self._transition_reservation_fault("after_retry", consumption)
                self._write(acquired, expected_revision=observation.current.revision if observation.current else 0)
                self._transition_reservation_fault("after_current", acquired)
                self._transition_reservation_fault("after_history", acquired)
                self._insert_transition_reservation_receipt(receipt)
                self._transition_reservation_fault("after_receipt", receipt)
                self._transition_reservation_fault("before_commit", receipt)
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
            self._transition_reservation_fault("after_commit", receipt)
            return receipt

    def _read_transition_reservation_observation(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
        current = self._read_exact_ownership(
            intent.tenant_id,
            intent.run_id,
            intent.step_id,
        )
        receipts = self._read_transition_reservation_receipts(intent, current=current)
        anchor_revisions = {
            revision
            for receipt in receipts
            for revision in (
                receipt.acquired_revision,
                receipt.prior_ownership.revision if receipt.prior_ownership is not None else 0,
            )
            if revision > 0
        }
        if current is not None:
            anchor_revisions.add(current.revision)
        clauses = ["attempt_id = ?"]
        values: list[object] = [
            intent.tenant_id,
            intent.run_id,
            intent.step_id,
            intent.attempt_id,
        ]
        if anchor_revisions:
            clauses.append(f"revision IN ({','.join('?' for _ in anchor_revisions)})")
            values.extend(sorted(anchor_revisions))
        if current is None:
            clauses.append(
                "revision = (SELECT MAX(revision) FROM workflow_execution_attempt_history "
                "WHERE tenant_id = ? AND run_id = ? AND step_id = ?)"
            )
            values.extend([intent.tenant_id, intent.run_id, intent.step_id])
        history_rows = self._connection.execute(
            f"""
            SELECT tenant_id, run_id, step_id, revision, attempt_id, ownership_json
            FROM workflow_execution_attempt_history
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
              AND ({" OR ".join(clauses)})
            ORDER BY revision ASC
            """,
            tuple(values),
        ).fetchall()
        history = tuple(_direct_exact_history(row) for row in history_rows)
        retry_row = self._connection.execute(
            """
            SELECT tenant_id, run_id, retry_id, category
            FROM workflow_retry_consumptions
            WHERE tenant_id = ? AND run_id = ? AND retry_id = ?
            """,
            (intent.tenant_id, intent.run_id, intent.retry_id),
        ).fetchone()
        retry_consumption = (
            None if retry_row is None else WorkflowTransitionOwnershipRetryConsumption.from_mapping(dict(retry_row))
        )
        budget_row = self._connection.execute(
            """
            SELECT used, maximum FROM workflow_retry_budgets
            WHERE tenant_id = ? AND run_id = ?
            """,
            (intent.tenant_id, intent.run_id),
        ).fetchone()
        if budget_row is not None and (
            type(budget_row["used"]) is not int
            or type(budget_row["maximum"]) is not int
            or budget_row["used"] < 0
            or budget_row["maximum"] < 0
            or budget_row["maximum"] > _OWNERSHIP_MAX_RETRIES
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_retry_budget_projection_conflict"
            )
        budget = RetryBudgetSnapshot(
            intent.tenant_id,
            intent.run_id,
            used=budget_row["used"] if budget_row else 0,
            maximum=budget_row["maximum"] if budget_row else intent.maximum_retries,
        )
        return _transition_ownership_observation(
            intent,
            claim_generation=claim_generation,
            current=current,
            history=history,
            retry_consumption=retry_consumption,
            retry_budget=budget,
            receipts=receipts,
        )

    def _read_transition_reservation_receipts(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        current: ExecutionOwnership | None,
    ) -> tuple[WorkflowTransitionOwnershipReservationReceipt, ...]:
        alias_values: list[object] = [
            intent.receipt_id,
            intent.effect_id,
            intent.operation_fence_id,
            intent.attempt_id,
        ]
        sql = """
            SELECT * FROM workflow_transition_ownership_reservations
            WHERE receipt_id = ? OR effect_id = ? OR operation_fence_id = ? OR attempt_id = ?
        """
        if current is not None:
            sql += """
                OR attempt_id = ?
                OR (
                    tenant_id = ? AND run_id = ? AND step_id = ?
                    AND (acquired_revision = ? OR acquired_fencing_token = ?)
                )
            """
            alias_values.extend(
                [
                    current.attempt_id,
                    current.tenant_id,
                    current.run_id,
                    current.step_id,
                    current.revision,
                    current.fencing_token,
                ]
            )
        if current is None:
            sql += """
                OR (tenant_id = ? AND run_id = ? AND step_id = ?)
            """
            alias_values.extend([intent.tenant_id, intent.run_id, intent.step_id])
        elif (
            current.revision < _OWNERSHIP_MAX_LEGACY_REVISION and current.fencing_token < _OWNERSHIP_MAX_LEGACY_REVISION
        ):
            next_revision = current.revision + 1
            next_fencing_token = current.fencing_token + 1
            sql += """
                OR (
                    tenant_id = ? AND run_id = ? AND step_id = ?
                    AND (acquired_revision >= ? OR acquired_fencing_token >= ?)
                )
            """
            alias_values.extend(
                [
                    intent.tenant_id,
                    intent.run_id,
                    intent.step_id,
                    next_revision,
                    next_fencing_token,
                ]
            )
        rows = self._connection.execute(sql + " ORDER BY receipt_id LIMIT 17", tuple(alias_values)).fetchall()
        return tuple(_direct_transition_reservation_receipt(row) for row in rows)

    def _read_exact_ownership(self, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        row = self._connection.execute(
            """
            SELECT tenant_id, run_id, step_id, revision, fencing_token, ownership_json
            FROM workflow_execution_ownership
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
            """,
            (tenant_id, run_id, step_id),
        ).fetchone()
        return None if row is None else _direct_exact_current(row)

    def _insert_transition_reservation_receipt(self, receipt: WorkflowTransitionOwnershipReservationReceipt) -> None:
        values = _direct_transition_reservation_receipt_values(receipt)
        self._connection.execute(
            """
            INSERT INTO workflow_transition_ownership_reservations (
                receipt_id, transition_id, effect_id, operation_fence_id,
                attempt_id, owner_id, tenant_id, workflow_id, run_id, runtime_id,
                step_id, ownership_intent_digest, acquisition_record_digest,
                receipt_digest, creator_claim_generation, acquired_revision,
                acquired_fencing_token, maximum_retries, retry_consumed,
                planned_at, reserved_at, lease_expires_at, receipt_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            values,
        )

    def _transition_reservation_fault(self, stage: str, value: object) -> None:
        del stage, value

    def heartbeat(self, **values: Any) -> ExecutionOwnership:
        timestamp = _validate_lease(float(values["lease_seconds"]), values.get("now"))
        return self._mutate_owned(
            values,
            lambda current: _heartbeat(current, values=values, timestamp=timestamp),
        )

    def acknowledge_result(self, **values: Any) -> ExecutionOwnership:
        result_ack_key = str(values.get("result_ack_key") or "")
        if not result_ack_key:
            raise ValueError("result_ack_key_required")
        timestamp = _timestamp(values.get("now"))
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(values)
                _assert_owner_from_values(current, values)
                if current.status == "completed" and current.result_ack_key == result_ack_key:
                    self._connection.commit()
                    return current
                _assert_expected_revision(current, int(values["expected_revision"]))
                if current.status != "active" or current.lease_expires_at <= timestamp:
                    raise FencingTokenError("result_owner_not_active")
                updated = replace(
                    current,
                    revision=current.revision + 1,
                    status="completed",
                    result_ack_key=result_ack_key,
                    lease_expires_at=timestamp,
                )
                self._write(updated, expected_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def fail_attempt(self, *, failure_code: str, dead_letter: bool = False, **values: Any) -> ExecutionOwnership:
        def mutate(current: ExecutionOwnership) -> ExecutionOwnership:
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise InvalidTransitionError("failure_requires_active_ownership")
            timestamp = _timestamp(values.get("now"))
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("failure_owner_lease_expired")
            return replace(
                current,
                revision=current.revision + 1,
                status="dead_letter" if dead_letter else "failed",
                failure_code=str(failure_code or "execution_failed"),
                lease_expires_at=timestamp,
            )

        return self._mutate_owned(values, mutate)

    def reconcile_orphan(
        self, *, tenant_id: str, run_id: str, step_id: str, now: float | None = None
    ) -> ExecutionOwnership | None:
        timestamp = float(now if now is not None else time.time())
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read(str(tenant_id), str(run_id), str(step_id))
                if current is None or current.status != "active" or current.lease_expires_at > timestamp:
                    self._connection.commit()
                    return None
                updated = replace(
                    current,
                    revision=current.revision + 1,
                    status="orphaned",
                    failure_code="lease_expired",
                )
                self._write(updated, expected_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        with self._lock:
            return self._read(str(tenant_id), str(run_id), str(step_id))

    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                value = self._consume_retry_in_transaction(
                    tenant_id=str(tenant_id),
                    run_id=str(run_id),
                    retry_id=str(retry_id),
                    category=str(category),
                    maximum=int(maximum),
                )
                self._connection.commit()
                return value
            except Exception:
                self._connection.rollback()
                raise

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot:
        with self._lock:
            row = self._connection.execute(
                "SELECT used, maximum FROM workflow_retry_budgets WHERE tenant_id = ? AND run_id = ?",
                (str(tenant_id), str(run_id)),
            ).fetchone()
        if row is not None and int(row["maximum"]) != int(maximum):
            raise InvalidTransitionError("retry_budget_maximum_mismatch")
        return RetryBudgetSnapshot(
            str(tenant_id), str(run_id), used=int(row["used"] if row else 0), maximum=int(maximum)
        )

    def _consume_retry_in_transaction(
        self, *, tenant_id: str, run_id: str, retry_id: str, category: str, maximum: int
    ) -> RetryBudgetSnapshot:
        if maximum < 0 or not retry_id or category not in RETRY_CATEGORIES:
            raise ValueError("retry_budget_input_invalid")
        duplicate = self._connection.execute(
            """
            SELECT category FROM workflow_retry_consumptions
            WHERE tenant_id = ? AND run_id = ? AND retry_id = ?
            """,
            (tenant_id, run_id, retry_id),
        ).fetchone()
        row = self._connection.execute(
            "SELECT used, maximum FROM workflow_retry_budgets WHERE tenant_id = ? AND run_id = ?",
            (tenant_id, run_id),
        ).fetchone()
        used = int(row["used"] if row else 0)
        if row is not None and int(row["maximum"]) != maximum:
            raise InvalidTransitionError("retry_budget_maximum_mismatch")
        if duplicate and str(duplicate["category"]) != category:
            raise InvalidTransitionError("retry_budget_retry_id_binding_mismatch")
        if duplicate:
            return RetryBudgetSnapshot(tenant_id, run_id, used=used, maximum=maximum)
        if used >= maximum:
            raise InvalidTransitionError("retry_budget_exhausted")
        self._connection.execute(
            """
            INSERT INTO workflow_retry_consumptions (tenant_id, run_id, retry_id, category)
            VALUES (?, ?, ?, ?)
            """,
            (tenant_id, run_id, retry_id, category),
        )
        self._connection.execute(
            """
            INSERT INTO workflow_retry_budgets (tenant_id, run_id, used, maximum) VALUES (?, ?, 1, ?)
            ON CONFLICT (tenant_id, run_id) DO UPDATE SET used = used + 1
            """,
            (tenant_id, run_id, maximum),
        )
        return RetryBudgetSnapshot(tenant_id, run_id, used=used + 1, maximum=maximum)

    def _mutate_owned(
        self,
        values: dict[str, Any],
        mutate: Callable[[ExecutionOwnership], ExecutionOwnership],
    ) -> ExecutionOwnership:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(values)
                _assert_owner_from_values(current, values)
                updated = mutate(current)
                self._write(updated, expected_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def _read(self, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        row = self._connection.execute(
            """
            SELECT ownership_json FROM workflow_execution_ownership
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
            """,
            (tenant_id, run_id, step_id),
        ).fetchone()
        return ExecutionOwnership.from_mapping(json.loads(str(row["ownership_json"]))) if row else None

    def _read_required(self, values: dict[str, Any]) -> ExecutionOwnership:
        current = self._read(str(values["tenant_id"]), str(values["run_id"]), str(values["step_id"]))
        if current is None:
            raise KeyError("execution_ownership_not_found")
        return current

    def _write(self, value: ExecutionOwnership, *, expected_revision: int) -> None:
        value.assert_valid()
        payload = canonical_json(value.to_dict())
        if expected_revision == 0:
            self._connection.execute(
                """
                INSERT INTO workflow_execution_ownership
                (tenant_id, run_id, step_id, revision, fencing_token, ownership_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (value.tenant_id, value.run_id, value.step_id, value.revision, value.fencing_token, payload),
            )
        else:
            cursor = self._connection.execute(
                """
                UPDATE workflow_execution_ownership
                SET revision = ?, fencing_token = ?, ownership_json = ?
                WHERE tenant_id = ? AND run_id = ? AND step_id = ? AND revision = ?
                """,
                (
                    value.revision,
                    value.fencing_token,
                    payload,
                    value.tenant_id,
                    value.run_id,
                    value.step_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError("execution_ownership_compare_and_set_failed")
        self._connection.execute(
            """
            INSERT INTO workflow_execution_attempt_history
            (tenant_id, run_id, step_id, revision, attempt_id, ownership_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (value.tenant_id, value.run_id, value.step_id, value.revision, value.attempt_id, payload),
        )


def _direct_exact_current(row: sqlite3.Row) -> ExecutionOwnership:
    try:
        raw = json.loads(str(row["ownership_json"]))
        if not isinstance(raw, Mapping):
            raise TypeError("ownership")
        value = ExecutionOwnership.from_exact_mapping(raw)
        if (
            value.tenant_id != row["tenant_id"]
            or value.run_id != row["run_id"]
            or value.step_id != row["step_id"]
            or value.revision != row["revision"]
            or value.fencing_token != row["fencing_token"]
        ):
            raise ValueError("projection")
        return value
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_current_projection_conflict"
        ) from exc


def _direct_exact_history(row: sqlite3.Row) -> ExecutionOwnership:
    try:
        raw = json.loads(str(row["ownership_json"]))
        if not isinstance(raw, Mapping):
            raise TypeError("ownership")
        value = ExecutionOwnership.from_exact_mapping(raw)
        if (
            value.tenant_id != row["tenant_id"]
            or value.run_id != row["run_id"]
            or value.step_id != row["step_id"]
            or value.revision != row["revision"]
            or value.attempt_id != row["attempt_id"]
        ):
            raise ValueError("projection")
        return value
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_history_projection_conflict"
        ) from exc


def _direct_transition_reservation_receipt_values(
    receipt: WorkflowTransitionOwnershipReservationReceipt,
) -> tuple[object, ...]:
    intent = receipt.intent
    return (
        receipt.receipt_id,
        receipt.transition_id,
        receipt.effect_id,
        receipt.operation_fence_id,
        receipt.attempt_id,
        receipt.owner_id,
        intent.tenant_id,
        intent.workflow_id,
        intent.run_id,
        intent.runtime_id,
        intent.step_id,
        intent.ownership_intent_digest,
        receipt.acquired_record_digest,
        receipt.receipt_digest,
        receipt.creator_claim_generation,
        receipt.acquired_revision,
        receipt.acquired_fencing_token,
        intent.maximum_retries,
        int(receipt.retry_consumed),
        intent.planned_at,
        receipt.reserved_at,
        receipt.lease_expires_at,
        canonical_json(receipt.to_dict()),
    )


def _direct_transition_reservation_receipt(
    row: sqlite3.Row,
) -> WorkflowTransitionOwnershipReservationReceipt:
    try:
        for name in (
            "receipt_id",
            "transition_id",
            "effect_id",
            "operation_fence_id",
            "attempt_id",
            "owner_id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "runtime_id",
            "step_id",
            "ownership_intent_digest",
            "acquisition_record_digest",
            "receipt_digest",
            "receipt_json",
        ):
            if type(row[name]) is not str:
                raise TypeError(name)
        for name in (
            "creator_claim_generation",
            "acquired_revision",
            "acquired_fencing_token",
            "maximum_retries",
            "retry_consumed",
        ):
            if type(row[name]) is not int:
                raise TypeError(name)
        for name in ("planned_at", "reserved_at", "lease_expires_at"):
            if type(row[name]) is not float or not math.isfinite(row[name]):
                raise TypeError(name)
        raw = json.loads(str(row["receipt_json"]))
        if not isinstance(raw, Mapping):
            raise TypeError("receipt")
        receipt = WorkflowTransitionOwnershipReservationReceipt.from_mapping(raw)
        expected = _direct_transition_reservation_receipt_values(receipt)[:-1]
        actual = tuple(
            row[name]
            for name in (
                "receipt_id",
                "transition_id",
                "effect_id",
                "operation_fence_id",
                "attempt_id",
                "owner_id",
                "tenant_id",
                "workflow_id",
                "run_id",
                "runtime_id",
                "step_id",
                "ownership_intent_digest",
                "acquisition_record_digest",
                "receipt_digest",
                "creator_claim_generation",
                "acquired_revision",
                "acquired_fencing_token",
                "maximum_retries",
                "retry_consumed",
                "planned_at",
                "reserved_at",
                "lease_expires_at",
            )
        )
        if actual != expected:
            raise ValueError("projection")
        return receipt
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_receipt_projection_conflict"
        ) from exc


_DIRECT_OWNERSHIP_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "workflow_execution_ownership": (
        "tenant_id",
        "run_id",
        "step_id",
        "revision",
        "fencing_token",
        "ownership_json",
    ),
    "workflow_execution_attempt_history": (
        "tenant_id",
        "run_id",
        "step_id",
        "revision",
        "attempt_id",
        "ownership_json",
    ),
    "workflow_retry_budgets": ("tenant_id", "run_id", "used", "maximum"),
    "workflow_retry_consumptions": ("tenant_id", "run_id", "retry_id", "category"),
    "workflow_transition_ownership_reservations": (
        "receipt_id",
        "transition_id",
        "effect_id",
        "operation_fence_id",
        "attempt_id",
        "owner_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "runtime_id",
        "step_id",
        "ownership_intent_digest",
        "acquisition_record_digest",
        "receipt_digest",
        "creator_claim_generation",
        "acquired_revision",
        "acquired_fencing_token",
        "maximum_retries",
        "retry_consumed",
        "planned_at",
        "reserved_at",
        "lease_expires_at",
        "receipt_json",
    ),
}

_DIRECT_OWNERSHIP_TABLE_TYPES: dict[str, tuple[str, ...]] = {
    "workflow_execution_ownership": ("TEXT", "TEXT", "TEXT", "INTEGER", "INTEGER", "TEXT"),
    "workflow_execution_attempt_history": (
        "TEXT",
        "TEXT",
        "TEXT",
        "INTEGER",
        "TEXT",
        "TEXT",
    ),
    "workflow_retry_budgets": ("TEXT", "TEXT", "INTEGER", "INTEGER"),
    "workflow_retry_consumptions": ("TEXT", "TEXT", "TEXT", "TEXT"),
    "workflow_transition_ownership_reservations": (
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "INTEGER",
        "INTEGER",
        "INTEGER",
        "INTEGER",
        "INTEGER",
        "REAL",
        "REAL",
        "REAL",
        "TEXT",
    ),
}

_DIRECT_OWNERSHIP_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "workflow_execution_ownership": ("tenant_id", "run_id", "step_id"),
    "workflow_execution_attempt_history": (
        "tenant_id",
        "run_id",
        "step_id",
        "revision",
    ),
    "workflow_retry_budgets": ("tenant_id", "run_id"),
    "workflow_retry_consumptions": ("tenant_id", "run_id", "retry_id"),
    "workflow_transition_ownership_reservations": ("receipt_id",),
}

_DIRECT_RESERVATION_INDEXES = {
    "ix_workflow_transition_ownership_res_transition": ("transition_id",),
    "ix_workflow_transition_ownership_res_tenant_run": ("tenant_id", "run_id"),
    "ix_workflow_transition_ownership_res_scope": ("tenant_id", "run_id", "step_id"),
    "ix_workflow_transition_ownership_res_owner": ("owner_id",),
}

_DIRECT_RESERVATION_UNIQUES = (
    (("receipt_id",), "pk"),
    (("effect_id",), "u"),
    (("operation_fence_id",), "u"),
    (("attempt_id",), "u"),
    (("tenant_id", "run_id", "step_id", "acquired_revision"), "u"),
    (("tenant_id", "run_id", "step_id", "acquired_fencing_token"), "u"),
)

_DIRECT_RESERVATION_CHECK_TERMS = (
    "creator_claim_generation > 0",
    "acquired_revision > 0",
    "acquired_revision <= 2147483647",
    "acquired_fencing_token > 0",
    "acquired_fencing_token <= 2147483647",
    "maximum_retries >= 0",
    "maximum_retries <= 2147483647",
    "retry_consumed IN (0, 1)",
    "planned_at > 0",
    "reserved_at >= planned_at",
    "lease_expires_at > reserved_at",
)


def _preflight_direct_ownership_schema(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    for table, columns in _DIRECT_OWNERSHIP_TABLE_COLUMNS.items():
        if table in tables:
            _assert_direct_ownership_table(connection, table, columns)
    index_rows = connection.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'").fetchall()
    indexes = {str(row[0]): str(row[1]) for row in index_rows}
    for name in _DIRECT_RESERVATION_INDEXES:
        if name in indexes and indexes[name] != "workflow_transition_ownership_reservations":
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    if "workflow_transition_ownership_reservations" in tables:
        _assert_direct_transition_reservation_schema(connection)


def _assert_direct_transition_reservation_schema(connection: sqlite3.Connection) -> None:
    table = "workflow_transition_ownership_reservations"
    _assert_direct_ownership_table(
        connection,
        table,
        _DIRECT_OWNERSHIP_TABLE_COLUMNS[table],
    )
    unique_columns: list[tuple[tuple[str, ...], str]] = []
    actual_indexes: dict[str, tuple[str, ...]] = {}
    for index in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
        name = str(index[1])
        columns = tuple(str(value[2]) for value in connection.execute(f'PRAGMA index_info("{name}")').fetchall())
        if int(index[4]) != 0:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
        if int(index[2]) == 1:
            unique_columns.append((columns, str(index[3])))
        else:
            actual_indexes[name] = columns
    if sorted(unique_columns) != sorted(_DIRECT_RESERVATION_UNIQUES) or actual_indexes != _DIRECT_RESERVATION_INDEXES:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    if connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    check_expression = _direct_named_check_expression(
        str(sql_row[0] if sql_row else ""),
        "ck_workflow_transition_ownership_res_valid",
    )
    actual_terms = sorted(_direct_check_term(value) for value in re.split(r"\band\b", check_expression, flags=re.I))
    expected_terms = sorted(_direct_check_term(value) for value in _DIRECT_RESERVATION_CHECK_TERMS)
    if actual_terms != expected_terms:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")


def _direct_named_check_expression(sql: str, name: str) -> str:
    if len(tuple(re.finditer(r"\bcheck\s*\(", sql, flags=re.I))) != 1:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    matches = tuple(
        re.finditer(
            rf"\bconstraint\s+{re.escape(name)}\s+check\s*\(",
            sql,
            flags=re.I,
        )
    )
    if len(matches) != 1:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    start = matches[0].end()
    depth = 1
    for position in range(start, len(sql)):
        character = sql[position]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return sql[start:position]
    raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")


def _direct_check_term(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).lower()
    while normalized.startswith("(") and normalized.endswith(")"):
        depth = 0
        encloses_all = True
        for index, character in enumerate(normalized):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and index != len(normalized) - 1:
                    encloses_all = False
                    break
            if depth < 0:
                encloses_all = False
                break
        if not encloses_all or depth != 0:
            break
        normalized = normalized[1:-1]
    if not normalized:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    return normalized


def _assert_direct_ownership_table(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> None:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    actual_columns = tuple(str(row[1]) for row in rows)
    actual_types = tuple(str(row[2]).upper() for row in rows)
    primary_key = tuple(name for _, name in sorted((int(row[5]), str(row[1])) for row in rows if int(row[5]) > 0))
    unexpected_indexes = False
    if table != "workflow_transition_ownership_reservations":
        unexpected_indexes = any(
            str(row[3]) != "pk" for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall()
        )
    if (
        actual_columns != columns
        or actual_types != _DIRECT_OWNERSHIP_TABLE_TYPES[table]
        or primary_key != _DIRECT_OWNERSHIP_PRIMARY_KEYS[table]
        or any(int(row[3]) != 1 for row in rows)
        or unexpected_indexes
        or connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    ):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")


def _transition_ownership_relevant_receipts(
    intent: WorkflowTransitionOwnershipReservationIntent,
    *,
    current: ExecutionOwnership | None,
    receipts: tuple[WorkflowTransitionOwnershipReservationReceipt, ...],
    include_prospective: bool,
) -> tuple[WorkflowTransitionOwnershipReservationReceipt, ...]:
    relevant: list[WorkflowTransitionOwnershipReservationReceipt] = []
    prospective_available = (
        include_prospective
        and current is not None
        and (
            current.revision < _OWNERSHIP_MAX_LEGACY_REVISION and current.fencing_token < _OWNERSHIP_MAX_LEGACY_REVISION
        )
    )
    next_revision = current.revision + 1 if prospective_available and current is not None else 1
    next_fencing_token = current.fencing_token + 1 if prospective_available and current is not None else 1
    for receipt in receipts:
        if not isinstance(receipt, WorkflowTransitionOwnershipReservationReceipt):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_receipt_projection_conflict"
            )
        candidate_alias = (
            receipt.receipt_id == intent.receipt_id
            or receipt.effect_id == intent.effect_id
            or receipt.operation_fence_id == intent.operation_fence_id
            or receipt.attempt_id == intent.attempt_id
        )
        current_alias = current is not None and (
            receipt.attempt_id == current.attempt_id
            or (
                receipt.intent.tenant_id == current.tenant_id
                and receipt.intent.run_id == current.run_id
                and receipt.intent.step_id == current.step_id
                and (
                    receipt.acquired_revision == current.revision
                    or receipt.acquired_fencing_token == current.fencing_token
                )
            )
        )
        same_scope = (
            receipt.intent.tenant_id == intent.tenant_id
            and receipt.intent.run_id == intent.run_id
            and receipt.intent.step_id == intent.step_id
        )
        prospective_alias = (
            include_prospective
            and same_scope
            and (
                current is None
                or (
                    prospective_available
                    and (
                        receipt.acquired_revision >= next_revision
                        or receipt.acquired_fencing_token >= next_fencing_token
                    )
                )
            )
        )
        if candidate_alias or current_alias or prospective_alias:
            relevant.append(receipt)
    if len(relevant) > 16:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_alias_limit")
    return tuple(sorted(relevant, key=lambda value: (value.receipt_id, value.receipt_digest)))


def _transition_ownership_observation(
    intent: WorkflowTransitionOwnershipReservationIntent,
    *,
    claim_generation: int,
    current: ExecutionOwnership | None,
    history: tuple[ExecutionOwnership, ...],
    retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None,
    retry_budget: RetryBudgetSnapshot,
    receipts: tuple[WorkflowTransitionOwnershipReservationReceipt, ...],
) -> WorkflowTransitionOwnershipReservationObservation:
    if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
    generation = _ownership_positive_integer(claim_generation, "claim_generation")
    current_copy = None if current is None else ExecutionOwnership.from_exact_mapping(current.to_dict())
    history_copy = tuple(ExecutionOwnership.from_exact_mapping(value.to_dict()) for value in history)
    if len(history_copy) > 1_000:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_history_limit")
    for value in history_copy:
        if (
            value.tenant_id != intent.tenant_id
            or value.workflow_id != intent.workflow_id
            or value.run_id != intent.run_id
            or value.step_id != intent.step_id
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_history_binding_conflict"
            )
    if current_copy is not None and (
        current_copy.tenant_id != intent.tenant_id
        or current_copy.workflow_id != intent.workflow_id
        or current_copy.run_id != intent.run_id
        or current_copy.step_id != intent.step_id
    ):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_current_binding_conflict")
    current_matches = (
        tuple(value for value in history_copy if current_copy is not None and value.revision == current_copy.revision)
        if current_copy is not None
        else ()
    )
    if current_copy is not None and (len(current_matches) != 1 or current_matches[0] != current_copy):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_current_history_conflict")
    if current_copy is None and history_copy:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_orphan_history_conflict")
    current_history = current_matches[0] if current_matches else None

    relevant = _transition_ownership_relevant_receipts(
        intent,
        current=current_copy,
        receipts=receipts,
        include_prospective=True,
    )
    candidates = tuple(
        receipt
        for receipt in relevant
        if (
            receipt.receipt_id == intent.receipt_id
            or receipt.effect_id == intent.effect_id
            or receipt.operation_fence_id == intent.operation_fence_id
            or receipt.attempt_id == intent.attempt_id
        )
    )
    if candidates and any(receipt.intent != intent for receipt in candidates):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_alias_conflict")
    distinct_candidates = {receipt.receipt_digest: receipt for receipt in candidates}
    if len(distinct_candidates) > 1:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_alias_conflict")
    receipt = next(iter(distinct_candidates.values()), None)
    if any(candidate not in candidates for candidate in relevant):
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_prospective_receipt_conflict"
        )
    if receipt is not None and receipt.creator_claim_generation > generation:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_receipt_generation_conflict"
        )
    current_receipts = tuple(
        candidate
        for candidate in relevant
        if current_copy is not None
        and (
            candidate.attempt_id == current_copy.attempt_id
            or (
                candidate.intent.tenant_id == current_copy.tenant_id
                and candidate.intent.run_id == current_copy.run_id
                and candidate.intent.step_id == current_copy.step_id
                and (
                    candidate.acquired_revision == current_copy.revision
                    or candidate.acquired_fencing_token == current_copy.fencing_token
                )
            )
        )
    )
    if receipt is None and current_receipts:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_current_receipt_conflict")
    acquired_history: ExecutionOwnership | None = None
    if receipt is not None:
        acquired_matches = tuple(value for value in history_copy if value.revision == receipt.acquired_revision)
        if len(acquired_matches) != 1 or acquired_matches[0] != receipt.acquired_ownership:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_acquired_history_conflict"
            )
        acquired_history = acquired_matches[0]
        if receipt.retry_consumption is not None:
            if retry_consumption != receipt.retry_consumption:
                raise WorkflowTransitionOwnershipReservationConflict(
                    "workflow_transition_ownership_retry_consumption_conflict"
                )
        elif retry_consumption is not None:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_retry_consumption_unexpected"
            )
    else:
        if retry_consumption is not None:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_retry_without_receipt")
        if current_copy is not None and (
            current_copy.attempt_id == intent.attempt_id or current_copy.owner_id == intent.owner_id
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_current_without_receipt"
            )
        if any(value.attempt_id == intent.attempt_id or value.owner_id == intent.owner_id for value in history_copy):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_history_without_receipt"
            )

    if (
        retry_budget.tenant_id != intent.tenant_id
        or retry_budget.run_id != intent.run_id
        or retry_budget.maximum != intent.maximum_retries
        or retry_budget.used < 0
        or retry_budget.used > retry_budget.maximum
    ):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_retry_budget_conflict")
    aliases = tuple(sorted({value.receipt_digest for value in relevant}))
    digest = _ownership_observation_digest(
        intent=intent,
        claim_generation=generation,
        current=current_copy,
        current_history=current_history,
        acquired_history=acquired_history,
        retry_consumption=retry_consumption,
        retry_budget=retry_budget,
        receipt=receipt,
        receipt_alias_digests=aliases,
    )
    return WorkflowTransitionOwnershipReservationObservation(
        intent=intent,
        claim_generation=generation,
        current=current_copy,
        current_history=current_history,
        acquired_history=acquired_history,
        retry_consumption=retry_consumption,
        retry_budget=retry_budget,
        receipt=receipt,
        receipt_alias_digests=aliases,
        observation_digest=digest,
    )


def _transition_ownership_evidence(
    intent: WorkflowTransitionOwnershipReservationIntent,
    *,
    history: tuple[ExecutionOwnership, ...],
    retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None,
    receipts: tuple[WorkflowTransitionOwnershipReservationReceipt, ...],
) -> WorkflowTransitionOwnershipReservationEvidence:
    if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
    candidates = tuple(
        receipt
        for receipt in receipts
        if (
            receipt.receipt_id == intent.receipt_id
            or receipt.effect_id == intent.effect_id
            or receipt.operation_fence_id == intent.operation_fence_id
            or receipt.attempt_id == intent.attempt_id
        )
    )
    distinct = {receipt.receipt_digest: receipt for receipt in candidates}
    if not distinct:
        if any(value.attempt_id == intent.attempt_id for value in history) or retry_consumption is not None:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_partial")
        return WorkflowTransitionOwnershipReservationEvidence(
            intent=intent,
            receipt=None,
            prior_history=None,
            acquired_history=None,
            retry_consumption=None,
            receipt_alias_digests=(),
        )
    if len(distinct) != 1:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_receipt_conflict")
    receipt = next(iter(distinct.values()))
    if receipt.intent != intent:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_receipt_conflict")
    exact_history = tuple(ExecutionOwnership.from_exact_mapping(value.to_dict()) for value in history)
    acquired = tuple(
        value
        for value in exact_history
        if value.revision == receipt.acquired_revision and value.attempt_id == receipt.attempt_id
    )
    if len(acquired) != 1 or acquired[0] != receipt.acquired_ownership:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_history_conflict")
    prior = (
        ()
        if receipt.prior_ownership is None
        else tuple(
            value
            for value in exact_history
            if value.revision == receipt.prior_ownership.revision
            and value.attempt_id == receipt.prior_ownership.attempt_id
        )
    )
    if receipt.prior_ownership is not None and (len(prior) != 1 or prior[0] != receipt.prior_ownership):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_prior_conflict")
    return WorkflowTransitionOwnershipReservationEvidence(
        intent=intent,
        receipt=receipt,
        prior_history=prior[0] if prior else None,
        acquired_history=acquired[0],
        retry_consumption=retry_consumption,
        receipt_alias_digests=(receipt.receipt_digest,),
    )


def _transition_ownership_reservation_values(
    observation: WorkflowTransitionOwnershipReservationObservation,
    *,
    creator_claim_generation: int,
    expected_observation_digest: str,
    reserved_at: float,
) -> tuple[
    ExecutionOwnership,
    WorkflowTransitionOwnershipRetryConsumption | None,
    RetryBudgetSnapshot,
    WorkflowTransitionOwnershipReservationReceipt,
]:
    generation = _ownership_positive_integer(creator_claim_generation, "creator_claim_generation")
    expected = _ownership_sha256(expected_observation_digest, "expected_observation_digest")
    timestamp = _ownership_exact_timestamp(reserved_at, "reserved_at")
    if observation.claim_generation != generation:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_observation_conflict")
    if observation.receipt is not None:
        if observation.receipt.creator_claim_generation > generation:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_receipt_generation_conflict"
            )
        return (
            observation.receipt.acquired_ownership,
            observation.receipt.retry_consumption,
            observation.retry_budget,
            observation.receipt,
        )
    if observation.observation_digest != expected:
        raise WorkflowTransitionOwnershipReservationStale("workflow_transition_ownership_observation_stale")
    intent = observation.intent
    current = observation.current
    if timestamp < intent.planned_at:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_reserved_at_invalid")
    if current is not None:
        if current.status == "completed":
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_completed_conflict")
        if current.status == "active" and current.lease_expires_at > timestamp:
            raise WorkflowTransitionOwnershipReservationHeld("workflow_transition_ownership_lease_held")
        if current.status not in {"active", "failed", "orphaned", "dead_letter"}:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_takeover_invalid")
        if observation.current_history != current:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_current_history_conflict"
            )
        if (
            current.revision >= _OWNERSHIP_MAX_LEGACY_REVISION
            or current.fencing_token >= _OWNERSHIP_MAX_LEGACY_REVISION
        ):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_counter_exhausted")
    before = observation.retry_budget.used
    consumption = None
    after = before
    if current is not None:
        if before >= intent.maximum_retries:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_retry_budget_exhausted")
        consumption = WorkflowTransitionOwnershipRetryConsumption(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            retry_id=intent.retry_id,
        )
        after = before + 1
    acquired = ExecutionOwnership(
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        attempt_id=intent.attempt_id,
        owner_id=intent.owner_id,
        fencing_token=current.fencing_token + 1 if current is not None else 1,
        revision=current.revision + 1 if current is not None else 1,
        status="active",
        lease_expires_at=timestamp + intent.lease_seconds,
        last_heartbeat_at=timestamp,
    )
    if acquired.lease_expires_at <= timestamp:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_lease_expiry_invalid")
    acquired = ExecutionOwnership.from_exact_mapping(acquired.to_dict())
    receipt = WorkflowTransitionOwnershipReservationReceipt.build(
        intent=intent,
        creator_claim_generation=generation,
        prior_ownership=current,
        acquired_ownership=acquired,
        retry_consumption=consumption,
        retry_budget_used_before=before,
        retry_budget_used_after=after,
        reserved_at=timestamp,
    )
    budget = RetryBudgetSnapshot(
        intent.tenant_id,
        intent.run_id,
        used=after,
        maximum=intent.maximum_retries,
    )
    return acquired, consumption, budget, receipt


def workflow_transition_ownership_intent_digest(
    *,
    transition_id: str,
    runtime_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    effect_ordinal: int,
    lease_seconds: float,
    maximum_retries: int,
) -> str:
    values = {
        "transition_id": _ownership_identity(transition_id, "transition_id"),
        "runtime_id": _ownership_identity(runtime_id, "runtime_id"),
        "tenant_id": _ownership_identity(tenant_id, "tenant_id"),
        "workflow_id": _ownership_identity(workflow_id, "workflow_id"),
        "run_id": _ownership_identity(run_id, "run_id"),
        "step_id": _ownership_identity(step_id, "step_id"),
        "effect_ordinal": _ownership_positive_integer(effect_ordinal, "effect_ordinal"),
        "lease_seconds": _ownership_exact_positive_float(lease_seconds, "lease_seconds"),
        "maximum_retries": _ownership_retry_maximum(maximum_retries),
    }
    return _ownership_namespaced_digest(
        values,
        namespace="workflow-transition-ownership-reservation-intent",
    )


def workflow_transition_ownership_owner_id(*, ownership_intent_digest: str) -> str:
    return _ownership_opaque_id(
        "wfto",
        _ownership_sha256(ownership_intent_digest, "ownership_intent_digest"),
    )


def workflow_transition_ownership_operation_fence_id(*, ownership_intent_digest: str, owner_id: str) -> str:
    return _ownership_opaque_id(
        "wftof",
        _ownership_sha256(ownership_intent_digest, "ownership_intent_digest"),
        _ownership_identity(owner_id, "owner_id"),
    )


def workflow_transition_ownership_attempt_id(*, effect_id: str, operation_fence_id: str) -> str:
    return _ownership_opaque_id(
        "wfta",
        _ownership_identity(effect_id, "effect_id"),
        _ownership_identity(operation_fence_id, "operation_fence_id"),
    )


def workflow_transition_ownership_receipt_id(*, transition_id: str, effect_id: str) -> str:
    return _ownership_opaque_id(
        "wftor",
        _ownership_identity(transition_id, "transition_id"),
        _ownership_identity(effect_id, "effect_id"),
    )


def workflow_transition_ownership_record_digest(value: ExecutionOwnership | None) -> str:
    return _ownership_namespaced_digest(
        value.to_dict() if value is not None else {"absent": True},
        namespace="workflow-transition-ownership-record",
    )


def workflow_transition_ownership_receipt_digest(
    value: WorkflowTransitionOwnershipReservationReceipt,
) -> str:
    raw = value.to_dict()
    raw.pop("receipt_digest", None)
    return _ownership_namespaced_digest(
        raw,
        namespace="workflow-transition-ownership-reservation-receipt",
    )


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


def _ownership_observation_digest(
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
    claim_generation: int,
    current: ExecutionOwnership | None,
    current_history: ExecutionOwnership | None,
    acquired_history: ExecutionOwnership | None,
    retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None,
    retry_budget: RetryBudgetSnapshot,
    receipt: WorkflowTransitionOwnershipReservationReceipt | None,
    receipt_alias_digests: tuple[str, ...],
) -> str:
    def stable_record(value: ExecutionOwnership | None) -> dict[str, object] | None:
        if value is None:
            return None
        return value.to_dict()

    return _ownership_namespaced_digest(
        {
            "intent": intent.to_dict(),
            "claim_generation": claim_generation,
            "current": stable_record(current),
            "current_history": stable_record(current_history),
            "acquired_history": stable_record(acquired_history),
            "retry_consumption": (retry_consumption.to_dict() if retry_consumption is not None else None),
            "retry_budget": {
                "tenant_id": retry_budget.tenant_id,
                "run_id": retry_budget.run_id,
                "used": retry_budget.used,
                "maximum": retry_budget.maximum,
            },
            "receipt": receipt.to_dict() if receipt is not None else None,
            "receipt_alias_digests": list(receipt_alias_digests),
        },
        namespace="workflow-transition-ownership-reservation-observation",
    )


def _ownership_namespaced_digest(value: Mapping[str, Any], *, namespace: str) -> str:
    return hashlib.sha256(f"{namespace}\n{canonical_json(dict(value))}".encode("utf-8")).hexdigest()


def _ownership_opaque_id(prefix: str, *parts: object) -> str:
    payload = canonical_json({"namespace": prefix, "parts": list(parts)})
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _ownership_identity(value: object, reason: str) -> str:
    if not isinstance(value, str) or _OWNERSHIP_ID_RE.fullmatch(value) is None:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_legacy_text(value: object, reason: str, *, empty: bool) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid") from exc
    return value


def _exact_optional_ownership(value: object, reason: str) -> ExecutionOwnership | None:
    if value is None:
        return None
    if not isinstance(value, ExecutionOwnership):
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return ExecutionOwnership.from_exact_mapping(value.to_dict())


def _ownership_status(value: object) -> str:
    if not isinstance(value, str) or value not in OWNERSHIP_STATUSES:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_status_invalid")
    return value


def _ownership_exact_schema(value: object, expected: str) -> str:
    if value != expected or not isinstance(value, str):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_schema_unsupported")
    return value


def _ownership_sha256(value: object, reason: str) -> str:
    if not isinstance(value, str) or _OWNERSHIP_SHA256_RE.fullmatch(value) is None:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_positive_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > _OWNERSHIP_MAX_COUNTER:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_positive_legacy_counter(value: object, reason: str) -> int:
    result = _ownership_positive_integer(value, reason)
    if result > _OWNERSHIP_MAX_LEGACY_REVISION:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return result


def _ownership_non_negative_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _OWNERSHIP_MAX_COUNTER:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_retry_maximum(value: object) -> int:
    maximum = _ownership_non_negative_integer(value, "maximum_retries")
    if maximum > _OWNERSHIP_MAX_RETRIES:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_maximum_retries_invalid")
    return maximum


def _ownership_exact_positive_float(value: object, reason: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_exact_timestamp(value: object, reason: str) -> float:
    return _ownership_exact_positive_float(value, reason)


def _ownership_finite_timestamp(value: object, reason: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _validate_lease(lease_seconds: float, now: Any) -> float:
    if float(lease_seconds) <= 0:
        raise ValueError("lease_seconds_invalid")
    return float(now if now is not None else time.time())


def _timestamp(value: Any) -> float:
    return float(time.time() if value is None else value)


def _assert_owner(current: ExecutionOwnership, *, attempt_id: str, owner_id: str, fencing_token: int) -> None:
    if current.attempt_id != attempt_id or current.owner_id != owner_id or current.fencing_token != int(fencing_token):
        raise FencingTokenError("execution_owner_stale")


def _assert_owner_from_values(current: ExecutionOwnership, values: dict[str, Any]) -> None:
    _assert_owner(
        current,
        attempt_id=str(values["attempt_id"]),
        owner_id=str(values["owner_id"]),
        fencing_token=int(values["fencing_token"]),
    )


def _assert_expected_revision(current: ExecutionOwnership, expected_revision: int) -> None:
    if current.revision != int(expected_revision):
        raise OptimisticConcurrencyError(
            f"execution_ownership_revision_conflict:expected={expected_revision}:actual={current.revision}"
        )


def _heartbeat(current: ExecutionOwnership, *, values: dict[str, Any], timestamp: float) -> ExecutionOwnership:
    _assert_expected_revision(current, int(values["expected_revision"]))
    if current.status != "active":
        raise FencingTokenError("heartbeat_owner_no_longer_active")
    if current.lease_expires_at <= timestamp:
        raise FencingTokenError("ownership_lease_expired")
    return replace(
        current,
        revision=current.revision + 1,
        last_heartbeat_at=timestamp,
        lease_expires_at=timestamp + float(values["lease_seconds"]),
    )
