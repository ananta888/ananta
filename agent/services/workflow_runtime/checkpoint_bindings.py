"""Hub-owned binding of one transition effect to one exact checkpoint.

A transition does not author checkpoint state — the runtime does.  What a
transition needs is proof of *which* checkpoint revision it advanced against,
so that a restart re-adopts the same binding rather than silently binding the
run to whatever revision is current by then.

The binding is therefore a read-then-prove contract: the executor re-reads the
authoritative checkpoint, derives its digest from that read, and records the
binding.  A checkpoint that is not there yet is a retry, not a failure: the
runtime simply has not written it.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_runtime._serialization import canonical_json

CHECKPOINT_BINDING_RECEIPT_SCHEMA = "ananta.workflow_transition_checkpoint_binding_receipt.v1"

_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_COUNTER = 2_147_483_647


class WorkflowTransitionCheckpointBindingError(ValueError):
    """Stable fail-closed checkpoint binding contract error."""


class WorkflowTransitionCheckpointBindingConflict(RuntimeError):
    """Another binding already owns this fence, attempt or revision."""


class WorkflowTransitionCheckpointBindingUnavailable(RuntimeError):
    """The authority could not be reached; the effect stays retryable."""


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionCheckpointBindingIntent:
    """The exact checkpoint binding one transition effect plans."""

    transition_id: str
    effect_id: str
    runtime_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    task_id: str
    effect_ordinal: int
    checkpoint_intent_digest: str
    operation_fence_id: str
    attempt_id: str
    receipt_id: str
    expected_revision: int
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
        _sha256(self.checkpoint_intent_digest, "checkpoint_intent_digest")
        _positive_integer(self.effect_ordinal, "effect_ordinal")
        _positive_integer(self.expected_revision, "expected_revision")
        _positive_float(self.planned_at, "planned_at")


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionCheckpointBindingReceipt:
    """Immutable evidence that one effect bound one checkpoint revision."""

    schema: str
    receipt_id: str
    transition_id: str
    effect_id: str
    operation_fence_id: str
    attempt_id: str
    checkpoint_id: str
    task_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    runtime_id: str
    step_id: str
    checkpoint_intent_digest: str
    checkpoint_digest: str
    creator_claim_generation: int
    bound_revision: int
    bound_fencing_token: int
    planned_at: float
    bound_at: float
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema != CHECKPOINT_BINDING_RECEIPT_SCHEMA:
            raise WorkflowTransitionCheckpointBindingError("workflow_transition_checkpoint_binding_schema_invalid")
        for name in (
            "receipt_id",
            "transition_id",
            "effect_id",
            "operation_fence_id",
            "attempt_id",
            "checkpoint_id",
            "task_id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "runtime_id",
            "step_id",
        ):
            _identity(getattr(self, name), name)
        _sha256(self.checkpoint_intent_digest, "checkpoint_intent_digest")
        _sha256(self.checkpoint_digest, "checkpoint_digest")
        _positive_integer(self.creator_claim_generation, "creator_claim_generation")
        _positive_integer(self.bound_revision, "bound_revision")
        _positive_integer(self.bound_fencing_token, "bound_fencing_token")
        _positive_float(self.planned_at, "planned_at")
        _positive_float(self.bound_at, "bound_at")
        if self.bound_at < self.planned_at:
            raise WorkflowTransitionCheckpointBindingError("workflow_transition_checkpoint_binding_clock_invalid")
        if self.receipt_digest:
            _sha256(self.receipt_digest, "receipt_digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "bound_at": self.bound_at,
            "bound_fencing_token": self.bound_fencing_token,
            "bound_revision": self.bound_revision,
            "checkpoint_digest": self.checkpoint_digest,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_intent_digest": self.checkpoint_intent_digest,
            "creator_claim_generation": self.creator_claim_generation,
            "effect_id": self.effect_id,
            "operation_fence_id": self.operation_fence_id,
            "planned_at": self.planned_at,
            "receipt_digest": self.receipt_digest,
            "receipt_id": self.receipt_id,
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "schema": self.schema,
            "step_id": self.step_id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "transition_id": self.transition_id,
            "workflow_id": self.workflow_id,
        }

    def with_digest(self) -> "WorkflowTransitionCheckpointBindingReceipt":
        raw = self.to_dict()
        raw.pop("receipt_digest", None)
        digest = _namespaced_digest(raw, namespace="workflow-transition-checkpoint-binding-receipt")
        return WorkflowTransitionCheckpointBindingReceipt(**{**raw, "receipt_digest": digest})


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionCheckpointBindingObservation:
    """Whether this exact effect already bound a checkpoint."""

    receipt: WorkflowTransitionCheckpointBindingReceipt | None
    head_revision: int

    def __post_init__(self) -> None:
        if self.receipt is not None and not isinstance(self.receipt, WorkflowTransitionCheckpointBindingReceipt):
            raise WorkflowTransitionCheckpointBindingError("workflow_transition_checkpoint_binding_observation_invalid")
        if isinstance(self.head_revision, bool) or not isinstance(self.head_revision, int):
            raise WorkflowTransitionCheckpointBindingError("workflow_transition_checkpoint_binding_head_invalid")
        if self.head_revision < 0:
            raise WorkflowTransitionCheckpointBindingError("workflow_transition_checkpoint_binding_head_invalid")


@runtime_checkable
class WorkflowTransitionCheckpointBindingReadPort(Protocol):
    def observe_transition_checkpoint_binding(
        self,
        *,
        tenant_id: str,
        run_id: str,
        effect_id: str,
    ) -> WorkflowTransitionCheckpointBindingObservation: ...


@runtime_checkable
class WorkflowTransitionCheckpointBindingCommitPort(Protocol):
    def bind_transition_checkpoint(
        self,
        intent: WorkflowTransitionCheckpointBindingIntent,
        *,
        checkpoint_id: str,
        checkpoint_digest: str,
        bound_revision: int,
        bound_fencing_token: int,
        claim_generation: int,
        bound_at: float,
    ) -> WorkflowTransitionCheckpointBindingReceipt: ...


@runtime_checkable
class WorkflowTransitionCheckpointBindingAuthority(
    WorkflowTransitionCheckpointBindingReadPort,
    WorkflowTransitionCheckpointBindingCommitPort,
    Protocol,
):
    """The single aggregate authority the mutating executor requires."""


def workflow_transition_checkpoint_intent_digest(
    *,
    transition_id: str,
    runtime_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    task_id: str,
    effect_ordinal: int,
    expected_revision: int,
) -> str:
    values = {
        "effect_ordinal": _positive_integer(effect_ordinal, "effect_ordinal"),
        "expected_revision": _positive_integer(expected_revision, "expected_revision"),
        "run_id": _identity(run_id, "run_id"),
        "runtime_id": _identity(runtime_id, "runtime_id"),
        "step_id": _identity(step_id, "step_id"),
        "task_id": _identity(task_id, "task_id"),
        "tenant_id": _identity(tenant_id, "tenant_id"),
        "transition_id": _identity(transition_id, "transition_id"),
        "workflow_id": _identity(workflow_id, "workflow_id"),
    }
    return _namespaced_digest(values, namespace="workflow-transition-checkpoint-binding-intent")


def workflow_transition_checkpoint_operation_fence_id(*, checkpoint_intent_digest: str) -> str:
    return _opaque_id("wftcf", _sha256(checkpoint_intent_digest, "checkpoint_intent_digest"))


def workflow_transition_checkpoint_attempt_id(*, effect_id: str, operation_fence_id: str) -> str:
    return _opaque_id(
        "wftca",
        _identity(effect_id, "effect_id"),
        _identity(operation_fence_id, "operation_fence_id"),
    )


def workflow_transition_checkpoint_receipt_id(*, transition_id: str, effect_id: str) -> str:
    return _opaque_id(
        "wftcr",
        _identity(transition_id, "transition_id"),
        _identity(effect_id, "effect_id"),
    )


def workflow_transition_checkpoint_digest(value: Mapping[str, Any] | None) -> str:
    """Digest the authoritative checkpoint read, never a caller's claim."""

    return _namespaced_digest(
        dict(value) if value is not None else {"absent": True},
        namespace="workflow-transition-checkpoint-record",
    )


@final
class InMemoryWorkflowTransitionCheckpointBindingStore:
    """Substitutable authority for tests and explicit local use."""

    __slots__ = ("_by_effect", "_by_fence", "_by_revision", "_revision")

    def __init__(self) -> None:
        self._by_effect: dict[tuple[str, str, str], WorkflowTransitionCheckpointBindingReceipt] = {}
        self._by_fence: dict[str, str] = {}
        self._by_revision: dict[tuple[str, str, str, int], str] = {}
        self._revision = 0

    def observe_transition_checkpoint_binding(
        self,
        *,
        tenant_id: str,
        run_id: str,
        effect_id: str,
    ) -> WorkflowTransitionCheckpointBindingObservation:
        key = (
            _identity(tenant_id, "tenant_id"),
            _identity(run_id, "run_id"),
            _identity(effect_id, "effect_id"),
        )
        return WorkflowTransitionCheckpointBindingObservation(self._by_effect.get(key), self._revision)

    def bind_transition_checkpoint(
        self,
        intent: WorkflowTransitionCheckpointBindingIntent,
        *,
        checkpoint_id: str,
        checkpoint_digest: str,
        bound_revision: int,
        bound_fencing_token: int,
        claim_generation: int,
        bound_at: float,
    ) -> WorkflowTransitionCheckpointBindingReceipt:
        if not isinstance(intent, WorkflowTransitionCheckpointBindingIntent):
            raise WorkflowTransitionCheckpointBindingError("workflow_transition_checkpoint_binding_intent_invalid")
        key = (intent.tenant_id, intent.run_id, intent.effect_id)
        existing = self._by_effect.get(key)
        if existing is not None:
            if existing.operation_fence_id != intent.operation_fence_id:
                raise WorkflowTransitionCheckpointBindingConflict(
                    "workflow_transition_checkpoint_binding_fence_conflict"
                )
            return existing
        owner = self._by_fence.get(intent.operation_fence_id)
        if owner is not None and owner != intent.effect_id:
            raise WorkflowTransitionCheckpointBindingConflict("workflow_transition_checkpoint_binding_fence_conflict")
        revision_key = (intent.tenant_id, intent.run_id, intent.task_id, int(bound_revision))
        revision_owner = self._by_revision.get(revision_key)
        if revision_owner is not None and revision_owner != intent.effect_id:
            raise WorkflowTransitionCheckpointBindingConflict(
                "workflow_transition_checkpoint_binding_revision_conflict"
            )
        self._revision += 1
        receipt = WorkflowTransitionCheckpointBindingReceipt(
            schema=CHECKPOINT_BINDING_RECEIPT_SCHEMA,
            receipt_id=intent.receipt_id,
            transition_id=intent.transition_id,
            effect_id=intent.effect_id,
            operation_fence_id=intent.operation_fence_id,
            attempt_id=intent.attempt_id,
            checkpoint_id=_identity(checkpoint_id, "checkpoint_id"),
            task_id=intent.task_id,
            tenant_id=intent.tenant_id,
            workflow_id=intent.workflow_id,
            run_id=intent.run_id,
            runtime_id=intent.runtime_id,
            step_id=intent.step_id,
            checkpoint_intent_digest=intent.checkpoint_intent_digest,
            checkpoint_digest=_sha256(checkpoint_digest, "checkpoint_digest"),
            creator_claim_generation=_positive_integer(claim_generation, "claim_generation"),
            bound_revision=_positive_integer(bound_revision, "bound_revision"),
            bound_fencing_token=_positive_integer(bound_fencing_token, "bound_fencing_token"),
            planned_at=intent.planned_at,
            bound_at=_positive_float(bound_at, "bound_at"),
        ).with_digest()
        self._by_effect[key] = receipt
        self._by_fence[intent.operation_fence_id] = intent.effect_id
        self._by_revision[revision_key] = intent.effect_id
        return receipt


def _identity(value: object, reason: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise WorkflowTransitionCheckpointBindingError(f"workflow_transition_checkpoint_binding_{reason}_invalid")
    return value


def _sha256(value: object, reason: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise WorkflowTransitionCheckpointBindingError(f"workflow_transition_checkpoint_binding_{reason}_invalid")
    return value


def _positive_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _MAX_COUNTER:
        raise WorkflowTransitionCheckpointBindingError(f"workflow_transition_checkpoint_binding_{reason}_invalid")
    return value


def _positive_float(value: object, reason: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise WorkflowTransitionCheckpointBindingError(f"workflow_transition_checkpoint_binding_{reason}_invalid")
    return value


def _namespaced_digest(values: Mapping[str, Any], *, namespace: str) -> str:
    framed = canonical_json({"namespace": namespace, "values": dict(values)}).encode("utf-8")
    return hashlib.sha256(framed).hexdigest()


def _opaque_id(prefix: str, *parts: str) -> str:
    framed = canonical_json({"parts": list(parts), "prefix": prefix}).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(framed).hexdigest()[:40]}"


__all__ = [
    "CHECKPOINT_BINDING_RECEIPT_SCHEMA",
    "InMemoryWorkflowTransitionCheckpointBindingStore",
    "WorkflowTransitionCheckpointBindingAuthority",
    "WorkflowTransitionCheckpointBindingCommitPort",
    "WorkflowTransitionCheckpointBindingConflict",
    "WorkflowTransitionCheckpointBindingError",
    "WorkflowTransitionCheckpointBindingIntent",
    "WorkflowTransitionCheckpointBindingObservation",
    "WorkflowTransitionCheckpointBindingReadPort",
    "WorkflowTransitionCheckpointBindingReceipt",
    "WorkflowTransitionCheckpointBindingUnavailable",
    "workflow_transition_checkpoint_attempt_id",
    "workflow_transition_checkpoint_digest",
    "workflow_transition_checkpoint_intent_digest",
    "workflow_transition_checkpoint_operation_fence_id",
    "workflow_transition_checkpoint_receipt_id",
]
