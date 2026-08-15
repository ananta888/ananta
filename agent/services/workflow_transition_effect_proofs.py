"""Closed structural proofs for future workflow-transition effect adapters.

These contracts prove only that caller-supplied evidence is well formed and
bound to one exact transition effect and one exact resource observation.  They
do not verify signatures, leases, authorization, resource existence, or any
domain-specific state.  A future adapter must re-read the resource through its
authoritative read port, verify its semantics, derive the resource digest from
that read, and only then call one of the explicit binding assertions below.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, final

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    TRANSITION_EFFECT_KINDS,
    TRANSITION_KINDS,
    TRANSITION_RUNTIMES,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_QUARANTINED,
    TRANSITION_STATE_READY,
    WorkflowTransition,
    WorkflowTransitionEffect,
)

WORKFLOW_TRANSITION_EFFECT_PROOF_CONTEXT_SCHEMA = "ananta.workflow_transition_effect_proof_context.v1"
WORKFLOW_TRANSITION_EFFECT_RESOURCE_PROOF_SCHEMA = "ananta.workflow_transition_effect_resource_proof.v1"
WORKFLOW_TRANSITION_EFFECT_ABSENCE_PROOF_SCHEMA = "ananta.workflow_transition_effect_absence_proof.v1"

_CONTEXT_FIELDS = frozenset(
    {
        "schema",
        "transition_id",
        "effect_id",
        "effect_kind",
        "runtime_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "transition_kind",
        "transition_request_fingerprint",
        "effect_ordinal",
        "effect_payload_digest",
        "idempotency_key",
        "claim_generation",
    }
)
_PROOF_FIELDS = frozenset({"schema", "context", "resource"})
_RESOURCE_FIELDS = frozenset({"kind", "id", "revision", "digest"})
_ABSENCE_PROOF_FIELDS = frozenset({"schema", "context", "resource", "head"})
_ABSENCE_RESOURCE_FIELDS = frozenset({"kind", "id"})
_ABSENCE_HEAD_FIELDS = frozenset({"revision", "digest"})
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_RESOURCE_KIND_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_IDEMPOTENCY_KEY_CHARS = 512
_MAX_RESOURCE_ID_CHARS = 512
_MAX_CONTEXT_BYTES = 4_096
_MAX_PROOF_BYTES = 8_192
_MAX_RESOURCE_BYTES = 524_288
_MAX_JSON_DEPTH = 32
_MAX_JSON_ITEMS = 10_000
_MAX_COUNTER = 2**63 - 1
_RESOURCE_DIGEST_NAMESPACE = "workflow-transition-effect-resource.v1"


class WorkflowTransitionEffectProofError(ValueError):
    """Stable fail-closed structural or exact-binding error."""


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionEffectScalars:
    """Shared fail-closed scalar validation for effect adapters.

    Every adapter validates identities, digests, counters and text the same
    way and differs only in the error class and message prefix it raises with,
    so those are the two things this binds.  Callers keep their own module
    bound for counters and text, which stays an explicit per-adapter decision.
    """

    error: type[Exception]
    prefix: str

    def _invalid(self, reason: str) -> Exception:
        return self.error(f"{self.prefix}_{reason}_invalid")

    def identity(self, value: object, reason: str) -> str:
        if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
            raise self._invalid(reason)
        return value

    def sha256(self, value: object, reason: str) -> str:
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise self._invalid(reason)
        return value

    def positive_integer(self, value: object, reason: str, *, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
            raise self._invalid(reason)
        return value

    def text(self, value: object, reason: str, *, maximum: int) -> str:
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum or "\x00" in value:
            raise self._invalid(reason)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise self._invalid(reason) from exc
        return value


@dataclass(slots=True)
class _JsonCopyBudget:
    remaining_items: int = _MAX_JSON_ITEMS
    remaining_string_bytes: int = _MAX_RESOURCE_BYTES

    def consume_item(self) -> None:
        self.remaining_items -= 1
        if self.remaining_items < 0:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_invalid")

    def consume_text(self, value: str) -> None:
        self.consume_item()
        try:
            self.remaining_string_bytes -= len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_invalid") from exc
        if self.remaining_string_bytes < 0:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_too_large")


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionEffectProofContext:
    """Immutable identity of one generation-fenced transition effect."""

    transition_id: str
    effect_id: str
    effect_kind: str
    runtime_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    transition_kind: str
    transition_request_fingerprint: str
    effect_ordinal: int
    effect_payload_digest: str
    idempotency_key: str
    claim_generation: int
    schema: str = WORKFLOW_TRANSITION_EFFECT_PROOF_CONTEXT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != WORKFLOW_TRANSITION_EFFECT_PROOF_CONTEXT_SCHEMA:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_proof_context_schema_unsupported")
        for value, reason in (
            (self.transition_id, "transition_id"),
            (self.effect_id, "effect_id"),
            (self.tenant_id, "tenant_id"),
            (self.workflow_id, "workflow_id"),
            (self.run_id, "run_id"),
        ):
            _identity(value, reason=reason)
        if not isinstance(self.runtime_id, str) or self.runtime_id not in TRANSITION_RUNTIMES:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_proof_runtime_invalid")
        if not isinstance(self.transition_kind, str) or self.transition_kind not in TRANSITION_KINDS:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_proof_transition_kind_invalid")
        if (
            not isinstance(self.effect_kind, str)
            or self.effect_kind not in TRANSITION_EFFECT_KINDS
            or self.effect_kind == EFFECT_BINDING_FINALIZE
        ):
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_proof_kind_invalid")
        _sha256(
            self.transition_request_fingerprint,
            reason="transition_request_fingerprint",
        )
        _positive_counter(self.effect_ordinal, reason="effect_ordinal")
        _sha256(self.effect_payload_digest, reason="effect_payload_digest")
        _bounded_text(
            self.idempotency_key,
            maximum=_MAX_IDEMPOTENCY_KEY_CHARS,
            reason="idempotency_key",
        )
        _positive_counter(self.claim_generation, reason="claim_generation")
        _bounded_json(
            self.to_dict(),
            maximum=_MAX_CONTEXT_BYTES,
            reason="proof_context",
        )

    @classmethod
    def from_active_claim(
        cls,
        *,
        transition: WorkflowTransition,
        effect: WorkflowTransitionEffect,
        claim_generation: int,
    ) -> WorkflowTransitionEffectProofContext:
        valid_generation = (
            not isinstance(claim_generation, bool)
            and isinstance(claim_generation, int)
            and 0 < claim_generation <= _MAX_COUNTER
        )
        active_effect = (
            valid_generation
            and isinstance(effect, WorkflowTransitionEffect)
            and (
                (effect.state == EFFECT_STATE_PLANNED and effect.applied_generation == 0)
                or (effect.state == EFFECT_STATE_APPLYING and 0 < effect.applied_generation <= claim_generation)
            )
        )
        if (
            not isinstance(transition, WorkflowTransition)
            or not isinstance(effect, WorkflowTransitionEffect)
            or transition.state != TRANSITION_STATE_APPLYING
            or effect.transition_id != transition.transition_id
            or effect.kind == EFFECT_BINDING_FINALIZE
            or not valid_generation
            or claim_generation != transition.claim_generation
            or not active_effect
        ):
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_active_proof_context_invalid")
        return cls(**_context_values(transition, effect, claim_generation))

    @classmethod
    def from_applied_effect(
        cls,
        *,
        transition: WorkflowTransition,
        effect: WorkflowTransitionEffect,
    ) -> WorkflowTransitionEffectProofContext:
        if (
            not isinstance(transition, WorkflowTransition)
            or not isinstance(effect, WorkflowTransitionEffect)
            or transition.state
            not in {
                TRANSITION_STATE_READY,
                TRANSITION_STATE_APPLYING,
                TRANSITION_STATE_COMPLETED,
                TRANSITION_STATE_QUARANTINED,
            }
            or effect.transition_id != transition.transition_id
            or effect.kind == EFFECT_BINDING_FINALIZE
            or effect.state != EFFECT_STATE_APPLIED
            or effect.applied_generation < 1
            or effect.applied_generation > transition.claim_generation
        ):
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_durable_proof_context_invalid")
        return cls(
            **_context_values(
                transition,
                effect,
                effect.applied_generation,
            )
        )

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
    ) -> WorkflowTransitionEffectProofContext:
        if not isinstance(raw, Mapping) or set(raw) != _CONTEXT_FIELDS:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_proof_context_invalid")
        return cls(
            transition_id=raw["transition_id"],
            effect_id=raw["effect_id"],
            effect_kind=raw["effect_kind"],
            runtime_id=raw["runtime_id"],
            tenant_id=raw["tenant_id"],
            workflow_id=raw["workflow_id"],
            run_id=raw["run_id"],
            transition_kind=raw["transition_kind"],
            transition_request_fingerprint=raw["transition_request_fingerprint"],
            effect_ordinal=raw["effect_ordinal"],
            effect_payload_digest=raw["effect_payload_digest"],
            idempotency_key=raw["idempotency_key"],
            claim_generation=raw["claim_generation"],
            schema=raw["schema"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "transition_id": self.transition_id,
            "effect_id": self.effect_id,
            "effect_kind": self.effect_kind,
            "runtime_id": self.runtime_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "transition_kind": self.transition_kind,
            "transition_request_fingerprint": self.transition_request_fingerprint,
            "effect_ordinal": self.effect_ordinal,
            "effect_payload_digest": self.effect_payload_digest,
            "idempotency_key": self.idempotency_key,
            "claim_generation": self.claim_generation,
        }


def _context_values(
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
) -> dict[str, Any]:
    return {
        "transition_id": transition.transition_id,
        "effect_id": effect.effect_id,
        "effect_kind": effect.kind,
        "runtime_id": transition.runtime_id,
        "tenant_id": transition.tenant_id,
        "workflow_id": transition.workflow_id,
        "run_id": transition.run_id,
        "transition_kind": transition.kind,
        "transition_request_fingerprint": transition.request_fingerprint,
        "effect_ordinal": effect.ordinal,
        "effect_payload_digest": effect.payload_digest,
        "idempotency_key": effect.idempotency_key,
        "claim_generation": claim_generation,
    }


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionEffectResourceProof:
    """Structural evidence binding one context to one resource revision."""

    context: WorkflowTransitionEffectProofContext
    resource_kind: str
    resource_id: str
    resource_revision: int
    resource_digest: str
    schema: str = WORKFLOW_TRANSITION_EFFECT_RESOURCE_PROOF_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != WORKFLOW_TRANSITION_EFFECT_RESOURCE_PROOF_SCHEMA:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_proof_schema_unsupported")
        if not isinstance(self.context, WorkflowTransitionEffectProofContext):
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_proof_context_invalid")
        _resource_kind(self.resource_kind)
        _bounded_text(
            self.resource_id,
            maximum=_MAX_RESOURCE_ID_CHARS,
            reason="resource_id",
        )
        _positive_counter(self.resource_revision, reason="resource_revision")
        _sha256(self.resource_digest, reason="resource_digest")
        _bounded_json(
            self.to_dict(),
            maximum=_MAX_PROOF_BYTES,
            reason="resource_proof",
        )

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
    ) -> WorkflowTransitionEffectResourceProof:
        if not isinstance(raw, Mapping) or set(raw) != _PROOF_FIELDS:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_proof_invalid")
        resource = raw["resource"]
        if not isinstance(resource, Mapping) or set(resource) != _RESOURCE_FIELDS:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_proof_invalid")
        return cls(
            context=WorkflowTransitionEffectProofContext.from_mapping(raw["context"]),
            resource_kind=resource["kind"],
            resource_id=resource["id"],
            resource_revision=resource["revision"],
            resource_digest=resource["digest"],
            schema=raw["schema"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "context": self.context.to_dict(),
            "resource": {
                "kind": self.resource_kind,
                "id": self.resource_id,
                "revision": self.resource_revision,
                "digest": self.resource_digest,
            },
        }


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionEffectAbsenceProof:
    """Structural evidence that one active effect observed no resource.

    ``head_revision`` and ``head_digest`` identify the bounded collection head
    against which absence was observed.  This DTO does not establish that the
    head is authoritative; a future adapter must derive both values from an
    authoritative read before asserting the binding.
    """

    context: WorkflowTransitionEffectProofContext
    resource_kind: str
    resource_id: str
    head_revision: int
    head_digest: str
    schema: str = WORKFLOW_TRANSITION_EFFECT_ABSENCE_PROOF_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != WORKFLOW_TRANSITION_EFFECT_ABSENCE_PROOF_SCHEMA:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_absence_proof_schema_unsupported")
        if not isinstance(self.context, WorkflowTransitionEffectProofContext):
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_absence_proof_context_invalid")
        _resource_kind(self.resource_kind)
        _bounded_text(
            self.resource_id,
            maximum=_MAX_RESOURCE_ID_CHARS,
            reason="resource_id",
        )
        _nonnegative_counter(self.head_revision, reason="head_revision")
        _sha256(self.head_digest, reason="head_digest")
        _bounded_json(
            self.to_dict(),
            maximum=_MAX_PROOF_BYTES,
            reason="absence_proof",
        )

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
    ) -> WorkflowTransitionEffectAbsenceProof:
        if not isinstance(raw, Mapping) or set(raw) != _ABSENCE_PROOF_FIELDS:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_absence_proof_invalid")
        resource = raw["resource"]
        head = raw["head"]
        if (
            not isinstance(resource, Mapping)
            or set(resource) != _ABSENCE_RESOURCE_FIELDS
            or not isinstance(head, Mapping)
            or set(head) != _ABSENCE_HEAD_FIELDS
        ):
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_absence_proof_invalid")
        return cls(
            context=WorkflowTransitionEffectProofContext.from_mapping(raw["context"]),
            resource_kind=resource["kind"],
            resource_id=resource["id"],
            head_revision=head["revision"],
            head_digest=head["digest"],
            schema=raw["schema"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "context": self.context.to_dict(),
            "resource": {
                "kind": self.resource_kind,
                "id": self.resource_id,
            },
            "head": {
                "revision": self.head_revision,
                "digest": self.head_digest,
            },
        }


def assert_active_workflow_transition_effect_proof_binding(
    proof: WorkflowTransitionEffectResourceProof | Mapping[str, Any],
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    resource_kind: str,
    resource_id: str,
    resource_revision: int,
    resource_digest: str,
) -> WorkflowTransitionEffectResourceProof:
    """Assert an active proof against the exact current APPLYING claim.

    This function intentionally performs no I/O and confers no authority.  A
    syntactically valid caller-provided digest is not evidence that a resource
    exists or is authorized.
    """

    expected_context = WorkflowTransitionEffectProofContext.from_active_claim(
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
    )
    return _assert_resource_proof_binding(
        proof,
        expected_context=expected_context,
        resource_kind=resource_kind,
        resource_id=resource_id,
        resource_revision=resource_revision,
        resource_digest=resource_digest,
        binding_reason="workflow_transition_effect_active_proof_binding_mismatch",
        resource_reason="workflow_transition_effect_active_proof_resource_mismatch",
    )


def assert_active_workflow_transition_effect_absence_proof_binding(
    proof: WorkflowTransitionEffectAbsenceProof | Mapping[str, Any],
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    resource_kind: str,
    resource_id: str,
    head_revision: int,
    head_digest: str,
) -> WorkflowTransitionEffectAbsenceProof:
    """Assert absence evidence against one exact active claim and head.

    This function intentionally performs no I/O and confers no authority.  A
    future adapter must first re-read the authoritative resource collection,
    establish absence, and derive the supplied head revision and digest.
    """

    expected_context = WorkflowTransitionEffectProofContext.from_active_claim(
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
    )
    candidate = (
        proof
        if isinstance(proof, WorkflowTransitionEffectAbsenceProof)
        else WorkflowTransitionEffectAbsenceProof.from_mapping(proof)
    )
    if candidate.context != expected_context:
        raise WorkflowTransitionEffectProofError("workflow_transition_effect_active_absence_proof_binding_mismatch")
    _resource_kind(resource_kind)
    _bounded_text(
        resource_id,
        maximum=_MAX_RESOURCE_ID_CHARS,
        reason="resource_id",
    )
    _nonnegative_counter(head_revision, reason="head_revision")
    _sha256(head_digest, reason="head_digest")
    if (
        candidate.resource_kind,
        candidate.resource_id,
        candidate.head_revision,
        candidate.head_digest,
    ) != (
        resource_kind,
        resource_id,
        head_revision,
        head_digest,
    ):
        raise WorkflowTransitionEffectProofError("workflow_transition_effect_active_absence_proof_resource_mismatch")
    return candidate


def assert_durable_workflow_transition_effect_proof_binding(
    proof: WorkflowTransitionEffectResourceProof | Mapping[str, Any],
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    resource_kind: str,
    resource_id: str,
    resource_revision: int,
    resource_digest: str,
) -> WorkflowTransitionEffectResourceProof:
    """Revalidate a persisted proof using only its applied effect generation."""

    expected_context = WorkflowTransitionEffectProofContext.from_applied_effect(
        transition=transition,
        effect=effect,
    )
    return _assert_resource_proof_binding(
        proof,
        expected_context=expected_context,
        resource_kind=resource_kind,
        resource_id=resource_id,
        resource_revision=resource_revision,
        resource_digest=resource_digest,
        binding_reason="workflow_transition_effect_durable_proof_binding_mismatch",
        resource_reason="workflow_transition_effect_durable_proof_resource_mismatch",
    )


def _assert_resource_proof_binding(
    proof: WorkflowTransitionEffectResourceProof | Mapping[str, Any],
    *,
    expected_context: WorkflowTransitionEffectProofContext,
    resource_kind: str,
    resource_id: str,
    resource_revision: int,
    resource_digest: str,
    binding_reason: str,
    resource_reason: str,
) -> WorkflowTransitionEffectResourceProof:
    candidate = (
        proof
        if isinstance(proof, WorkflowTransitionEffectResourceProof)
        else WorkflowTransitionEffectResourceProof.from_mapping(proof)
    )
    if candidate.context != expected_context:
        raise WorkflowTransitionEffectProofError(binding_reason)
    _resource_kind(resource_kind)
    _bounded_text(
        resource_id,
        maximum=_MAX_RESOURCE_ID_CHARS,
        reason="resource_id",
    )
    _positive_counter(resource_revision, reason="resource_revision")
    _sha256(resource_digest, reason="resource_digest")
    if (
        candidate.resource_kind,
        candidate.resource_id,
        candidate.resource_revision,
        candidate.resource_digest,
    ) != (
        resource_kind,
        resource_id,
        resource_revision,
        resource_digest,
    ):
        raise WorkflowTransitionEffectProofError(resource_reason)
    return candidate


def workflow_transition_effect_resource_digest(
    resource: Mapping[str, Any],
) -> str:
    """Digest a bounded canonical resource read without asserting its meaning."""

    if not isinstance(resource, Mapping) or not resource:
        raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_invalid")
    copied = _copy_json(resource, depth=0, budget=_JsonCopyBudget())
    rendered = _bounded_json(
        copied,
        maximum=_MAX_RESOURCE_BYTES,
        reason="resource_payload",
    )
    framed = f"{_RESOURCE_DIGEST_NAMESPACE}\x00{rendered}".encode()
    return hashlib.sha256(framed).hexdigest()


def _identity(value: Any, *, reason: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise WorkflowTransitionEffectProofError(f"workflow_transition_effect_proof_{reason}_invalid")
    return value


def _bounded_text(value: Any, *, maximum: int, reason: str) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise WorkflowTransitionEffectProofError(f"workflow_transition_effect_proof_{reason}_invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkflowTransitionEffectProofError(f"workflow_transition_effect_proof_{reason}_invalid") from exc
    return value


def _resource_kind(value: Any) -> str:
    if not isinstance(value, str) or _RESOURCE_KIND_RE.fullmatch(value) is None:
        raise WorkflowTransitionEffectProofError("workflow_transition_effect_proof_resource_kind_invalid")
    return value


def _sha256(value: Any, *, reason: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise WorkflowTransitionEffectProofError(f"workflow_transition_effect_proof_{reason}_invalid")
    return value


def _positive_counter(value: Any, *, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > _MAX_COUNTER:
        raise WorkflowTransitionEffectProofError(f"workflow_transition_effect_proof_{reason}_invalid")
    return value


def _nonnegative_counter(value: Any, *, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_COUNTER:
        raise WorkflowTransitionEffectProofError(f"workflow_transition_effect_proof_{reason}_invalid")
    return value


def _bounded_json(value: Any, *, maximum: int, reason: str) -> str:
    try:
        rendered = canonical_json(value)
        encoded = rendered.encode("utf-8")
    except (OverflowError, TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
        raise WorkflowTransitionEffectProofError(f"workflow_transition_effect_{reason}_invalid") from exc
    if len(encoded) > maximum:
        raise WorkflowTransitionEffectProofError(f"workflow_transition_effect_{reason}_too_large")
    return rendered


def _copy_json(value: Any, *, depth: int, budget: _JsonCopyBudget) -> Any:
    if depth > _MAX_JSON_DEPTH:
        raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_invalid")
    if isinstance(value, str):
        budget.consume_text(value)
        return value
    budget.consume_item()
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_invalid")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_JSON_ITEMS:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_invalid")
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256 or "\x00" in key:
                raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_invalid")
            budget.consume_text(key)
            copied[key] = _copy_json(item, depth=depth + 1, budget=budget)
        return copied
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_JSON_ITEMS:
            raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_invalid")
        return [_copy_json(item, depth=depth + 1, budget=budget) for item in value]
    raise WorkflowTransitionEffectProofError("workflow_transition_effect_resource_payload_invalid")


__all__ = [
    "WORKFLOW_TRANSITION_EFFECT_ABSENCE_PROOF_SCHEMA",
    "WORKFLOW_TRANSITION_EFFECT_PROOF_CONTEXT_SCHEMA",
    "WORKFLOW_TRANSITION_EFFECT_RESOURCE_PROOF_SCHEMA",
    "WorkflowTransitionEffectAbsenceProof",
    "WorkflowTransitionEffectProofContext",
    "WorkflowTransitionEffectProofError",
    "WorkflowTransitionEffectResourceProof",
    "WorkflowTransitionEffectScalars",
    "assert_active_workflow_transition_effect_absence_proof_binding",
    "assert_active_workflow_transition_effect_proof_binding",
    "assert_durable_workflow_transition_effect_proof_binding",
    "workflow_transition_effect_resource_digest",
]
