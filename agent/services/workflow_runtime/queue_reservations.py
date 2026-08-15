"""Hub-owned reservation of exactly one task-queue slot per transition effect.

The reservation is a proof, not a task.  Tasks are created and owned by the
queue service, including outside any transition, so this contract deliberately
does not write task state: it records which transition effect was permitted to
claim exactly one task, under the same fencing the rest of the transition runs
under.  A transition that crashes mid-flight therefore re-adopts its own
reservation instead of claiming a second task.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_runtime._serialization import canonical_json

QUEUE_RESERVATION_RECEIPT_SCHEMA = "ananta.workflow_transition_queue_reservation_receipt.v1"

_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_COUNTER = 2_147_483_647


class WorkflowTransitionQueueReservationError(ValueError):
    """Stable fail-closed queue reservation contract error."""


class WorkflowTransitionQueueReservationConflict(RuntimeError):
    """Another reservation already owns this fence, attempt or task."""


class WorkflowTransitionQueueReservationUnavailable(RuntimeError):
    """The authority could not be reached; the effect stays retryable."""


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionQueueReservationIntent:
    """The exact, byte-deterministic reservation a transition effect plans."""

    transition_id: str
    effect_id: str
    runtime_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    task_id: str
    effect_ordinal: int
    queue_intent_digest: str
    operation_fence_id: str
    attempt_id: str
    receipt_id: str
    maximum_retries: int
    planned_at: float

    def __post_init__(self) -> None:
        for name in (
            "transition_id",
            "effect_id",
            "runtime_id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "step_id",
            "task_id",
            "operation_fence_id",
            "attempt_id",
            "receipt_id",
        ):
            _identity(getattr(self, name), name)
        _sha256(self.queue_intent_digest, "queue_intent_digest")
        _positive_integer(self.effect_ordinal, "effect_ordinal")
        _retry_maximum(self.maximum_retries)
        _positive_float(self.planned_at, "planned_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "effect_id": self.effect_id,
            "effect_ordinal": self.effect_ordinal,
            "maximum_retries": self.maximum_retries,
            "operation_fence_id": self.operation_fence_id,
            "planned_at": self.planned_at,
            "queue_intent_digest": self.queue_intent_digest,
            "receipt_id": self.receipt_id,
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "step_id": self.step_id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "transition_id": self.transition_id,
            "workflow_id": self.workflow_id,
        }


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionQueueReservationReceipt:
    """Immutable historical evidence that one slot was reserved once."""

    schema: str
    receipt_id: str
    transition_id: str
    effect_id: str
    operation_fence_id: str
    attempt_id: str
    task_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    runtime_id: str
    step_id: str
    queue_intent_digest: str
    reservation_record_digest: str
    creator_claim_generation: int
    reserved_revision: int
    maximum_retries: int
    retry_consumed: bool
    planned_at: float
    reserved_at: float
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema != QUEUE_RESERVATION_RECEIPT_SCHEMA:
            raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_schema_invalid")
        for name in (
            "receipt_id",
            "transition_id",
            "effect_id",
            "operation_fence_id",
            "attempt_id",
            "task_id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "runtime_id",
            "step_id",
        ):
            _identity(getattr(self, name), name)
        _sha256(self.queue_intent_digest, "queue_intent_digest")
        _sha256(self.reservation_record_digest, "reservation_record_digest")
        _positive_integer(self.creator_claim_generation, "creator_claim_generation")
        _positive_integer(self.reserved_revision, "reserved_revision")
        _retry_maximum(self.maximum_retries)
        if not isinstance(self.retry_consumed, bool):
            raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_retry_invalid")
        _positive_float(self.planned_at, "planned_at")
        _positive_float(self.reserved_at, "reserved_at")
        if self.reserved_at < self.planned_at:
            raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_clock_invalid")
        if self.receipt_digest:
            _sha256(self.receipt_digest, "receipt_digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "creator_claim_generation": self.creator_claim_generation,
            "effect_id": self.effect_id,
            "maximum_retries": self.maximum_retries,
            "operation_fence_id": self.operation_fence_id,
            "planned_at": self.planned_at,
            "queue_intent_digest": self.queue_intent_digest,
            "receipt_digest": self.receipt_digest,
            "receipt_id": self.receipt_id,
            "reservation_record_digest": self.reservation_record_digest,
            "reserved_at": self.reserved_at,
            "reserved_revision": self.reserved_revision,
            "retry_consumed": self.retry_consumed,
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "schema": self.schema,
            "step_id": self.step_id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "transition_id": self.transition_id,
            "workflow_id": self.workflow_id,
        }

    def with_digest(self) -> "WorkflowTransitionQueueReservationReceipt":
        raw = self.to_dict()
        raw.pop("receipt_digest", None)
        digest = _namespaced_digest(raw, namespace="workflow-transition-queue-reservation-receipt")
        return WorkflowTransitionQueueReservationReceipt(
            **{**raw, "receipt_digest": digest},
        )


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionQueueReservationObservation:
    """Point-in-time view of whether this exact reservation already exists."""

    receipt: WorkflowTransitionQueueReservationReceipt | None
    head_revision: int

    def __post_init__(self) -> None:
        if self.receipt is not None and not isinstance(self.receipt, WorkflowTransitionQueueReservationReceipt):
            raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_observation_invalid")
        if isinstance(self.head_revision, bool) or not isinstance(self.head_revision, int):
            raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_head_invalid")
        if self.head_revision < 0:
            raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_head_invalid")


@runtime_checkable
class WorkflowTransitionQueueReservationReadPort(Protocol):
    """Read whether this exact effect already reserved its slot."""

    def observe_transition_queue_reservation(
        self,
        *,
        tenant_id: str,
        run_id: str,
        effect_id: str,
    ) -> WorkflowTransitionQueueReservationObservation: ...


@runtime_checkable
class WorkflowTransitionQueueReservationCommitPort(Protocol):
    """Commit exactly one reservation in a single authority transaction."""

    def reserve_transition_queue_slot(
        self,
        intent: WorkflowTransitionQueueReservationIntent,
        *,
        claim_generation: int,
        reserved_at: float,
    ) -> WorkflowTransitionQueueReservationReceipt: ...


@runtime_checkable
class WorkflowTransitionQueueReservationAuthority(
    WorkflowTransitionQueueReservationReadPort,
    WorkflowTransitionQueueReservationCommitPort,
    Protocol,
):
    """The single aggregate authority the mutating executor requires."""


def workflow_transition_queue_intent_digest(
    *,
    transition_id: str,
    runtime_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    task_id: str,
    effect_ordinal: int,
    maximum_retries: int,
) -> str:
    """Derive the digest that names one exact queue reservation intent."""

    values = {
        "effect_ordinal": _positive_integer(effect_ordinal, "effect_ordinal"),
        "maximum_retries": _retry_maximum(maximum_retries),
        "run_id": _identity(run_id, "run_id"),
        "runtime_id": _identity(runtime_id, "runtime_id"),
        "step_id": _identity(step_id, "step_id"),
        "task_id": _identity(task_id, "task_id"),
        "tenant_id": _identity(tenant_id, "tenant_id"),
        "transition_id": _identity(transition_id, "transition_id"),
        "workflow_id": _identity(workflow_id, "workflow_id"),
    }
    return _namespaced_digest(values, namespace="workflow-transition-queue-reservation-intent")


def workflow_transition_queue_operation_fence_id(*, queue_intent_digest: str) -> str:
    return _opaque_id("wftqf", _sha256(queue_intent_digest, "queue_intent_digest"))


def workflow_transition_queue_attempt_id(*, effect_id: str, operation_fence_id: str) -> str:
    return _opaque_id(
        "wftqa",
        _identity(effect_id, "effect_id"),
        _identity(operation_fence_id, "operation_fence_id"),
    )


def workflow_transition_queue_receipt_id(*, transition_id: str, effect_id: str) -> str:
    return _opaque_id(
        "wftqr",
        _identity(transition_id, "transition_id"),
        _identity(effect_id, "effect_id"),
    )


def workflow_transition_queue_record_digest(value: Mapping[str, Any] | None) -> str:
    """Digest the authoritative task record a reservation was granted against."""

    return _namespaced_digest(
        dict(value) if value is not None else {"absent": True},
        namespace="workflow-transition-queue-record",
    )


@final
class InMemoryWorkflowTransitionQueueReservationStore:
    """Substitutable authority for tests and explicit local use."""

    __slots__ = ("_by_effect", "_by_fence", "_by_task", "_revision")

    def __init__(self) -> None:
        self._by_effect: dict[tuple[str, str, str], WorkflowTransitionQueueReservationReceipt] = {}
        self._by_fence: dict[str, str] = {}
        self._by_task: dict[tuple[str, str, str], str] = {}
        self._revision = 0

    def observe_transition_queue_reservation(
        self,
        *,
        tenant_id: str,
        run_id: str,
        effect_id: str,
    ) -> WorkflowTransitionQueueReservationObservation:
        key = (
            _identity(tenant_id, "tenant_id"),
            _identity(run_id, "run_id"),
            _identity(effect_id, "effect_id"),
        )
        return WorkflowTransitionQueueReservationObservation(self._by_effect.get(key), self._revision)

    def reserve_transition_queue_slot(
        self,
        intent: WorkflowTransitionQueueReservationIntent,
        *,
        claim_generation: int,
        reserved_at: float,
    ) -> WorkflowTransitionQueueReservationReceipt:
        if not isinstance(intent, WorkflowTransitionQueueReservationIntent):
            raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_intent_invalid")
        generation = _positive_integer(claim_generation, "claim_generation")
        moment = _positive_float(reserved_at, "reserved_at")
        key = (intent.tenant_id, intent.run_id, intent.effect_id)
        existing = self._by_effect.get(key)
        if existing is not None:
            if existing.operation_fence_id != intent.operation_fence_id:
                raise WorkflowTransitionQueueReservationConflict("workflow_transition_queue_reservation_fence_conflict")
            return existing
        fence_owner = self._by_fence.get(intent.operation_fence_id)
        if fence_owner is not None and fence_owner != intent.effect_id:
            raise WorkflowTransitionQueueReservationConflict("workflow_transition_queue_reservation_fence_conflict")
        task_key = (intent.tenant_id, intent.run_id, intent.task_id)
        task_owner = self._by_task.get(task_key)
        if task_owner is not None and task_owner != intent.effect_id:
            raise WorkflowTransitionQueueReservationConflict("workflow_transition_queue_reservation_task_conflict")
        self._revision += 1
        receipt = WorkflowTransitionQueueReservationReceipt(
            schema=QUEUE_RESERVATION_RECEIPT_SCHEMA,
            receipt_id=intent.receipt_id,
            transition_id=intent.transition_id,
            effect_id=intent.effect_id,
            operation_fence_id=intent.operation_fence_id,
            attempt_id=intent.attempt_id,
            task_id=intent.task_id,
            tenant_id=intent.tenant_id,
            workflow_id=intent.workflow_id,
            run_id=intent.run_id,
            runtime_id=intent.runtime_id,
            step_id=intent.step_id,
            queue_intent_digest=intent.queue_intent_digest,
            reservation_record_digest=workflow_transition_queue_record_digest(
                {"task_id": intent.task_id, "revision": self._revision}
            ),
            creator_claim_generation=generation,
            reserved_revision=self._revision,
            maximum_retries=intent.maximum_retries,
            retry_consumed=False,
            planned_at=intent.planned_at,
            reserved_at=moment,
        ).with_digest()
        self._by_effect[key] = receipt
        self._by_fence[intent.operation_fence_id] = intent.effect_id
        self._by_task[task_key] = intent.effect_id
        return receipt


def _identity(value: object, reason: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise WorkflowTransitionQueueReservationError(f"workflow_transition_queue_reservation_{reason}_invalid")
    return value


def _sha256(value: object, reason: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise WorkflowTransitionQueueReservationError(f"workflow_transition_queue_reservation_{reason}_invalid")
    return value


def _positive_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _MAX_COUNTER:
        raise WorkflowTransitionQueueReservationError(f"workflow_transition_queue_reservation_{reason}_invalid")
    return value


def _retry_maximum(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_COUNTER:
        raise WorkflowTransitionQueueReservationError("workflow_transition_queue_reservation_maximum_retries_invalid")
    return value


def _positive_float(value: object, reason: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise WorkflowTransitionQueueReservationError(f"workflow_transition_queue_reservation_{reason}_invalid")
    return value


def _namespaced_digest(values: Mapping[str, Any], *, namespace: str) -> str:
    framed = canonical_json({"namespace": namespace, "values": dict(values)}).encode("utf-8")
    return hashlib.sha256(framed).hexdigest()


def _opaque_id(prefix: str, *parts: str) -> str:
    framed = canonical_json({"parts": list(parts), "prefix": prefix}).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(framed).hexdigest()[:40]}"


__all__ = [
    "QUEUE_RESERVATION_RECEIPT_SCHEMA",
    "InMemoryWorkflowTransitionQueueReservationStore",
    "WorkflowTransitionQueueReservationAuthority",
    "WorkflowTransitionQueueReservationCommitPort",
    "WorkflowTransitionQueueReservationConflict",
    "WorkflowTransitionQueueReservationError",
    "WorkflowTransitionQueueReservationIntent",
    "WorkflowTransitionQueueReservationObservation",
    "WorkflowTransitionQueueReservationReadPort",
    "WorkflowTransitionQueueReservationReceipt",
    "WorkflowTransitionQueueReservationUnavailable",
    "workflow_transition_queue_attempt_id",
    "workflow_transition_queue_intent_digest",
    "workflow_transition_queue_operation_fence_id",
    "workflow_transition_queue_receipt_id",
    "workflow_transition_queue_record_digest",
]
