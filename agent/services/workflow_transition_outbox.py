"""Immutable Hub-owned workflow transition and effect-ledger contracts.

The transition outbox is deliberately runtime-neutral within the two local
production runtimes.  It records immutable intent before infrastructure ports
are called; persistence adapters own leasing and compare-and-set transitions.
No worker execution or runtime composition belongs in this module.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from agent.services.workflow_runtime._serialization import canonical_json
from ananta_contracts.temporal_workflow import TemporalContractError, WorkflowCommand

WORKFLOW_TRANSITION_SCHEMA = "ananta.workflow-transition.v1"
WORKFLOW_TRANSITION_EFFECT_SCHEMA = "ananta.workflow-transition-effect.v1"
WORKFLOW_TRANSITION_EFFECT_RESULT_SCHEMA = "ananta.workflow-transition-effect-result.v1"

TRANSITION_KIND_START = "start"
TRANSITION_KIND_ADVANCE = "advance"
TRANSITION_KIND_COMMAND = "command"
TRANSITION_KINDS = frozenset(
    {
        TRANSITION_KIND_START,
        TRANSITION_KIND_ADVANCE,
        TRANSITION_KIND_COMMAND,
    }
)

TRANSITION_STATE_READY = "ready"
TRANSITION_STATE_APPLYING = "applying"
TRANSITION_STATE_COMPLETED = "completed"
TRANSITION_STATE_QUARANTINED = "quarantined"
TRANSITION_STATE_REJECTED = "rejected"
TRANSITION_STATES = frozenset(
    {
        TRANSITION_STATE_READY,
        TRANSITION_STATE_APPLYING,
        TRANSITION_STATE_COMPLETED,
        TRANSITION_STATE_QUARANTINED,
        TRANSITION_STATE_REJECTED,
    }
)
TRANSITION_TERMINAL_STATES = frozenset(
    {
        TRANSITION_STATE_COMPLETED,
        TRANSITION_STATE_QUARANTINED,
        TRANSITION_STATE_REJECTED,
    }
)

EFFECT_STATE_PLANNED = "planned"
EFFECT_STATE_APPLYING = "applying"
EFFECT_STATE_APPLIED = "applied"
EFFECT_STATE_REJECTED = "rejected"
EFFECT_STATES = frozenset(
    {
        EFFECT_STATE_PLANNED,
        EFFECT_STATE_APPLYING,
        EFFECT_STATE_APPLIED,
        EFFECT_STATE_REJECTED,
    }
)

EFFECT_EVENT_APPEND = "event_append"
EFFECT_OWNERSHIP_RESERVE = "ownership_reserve"
EFFECT_AUTHORIZATION_GRANT = "authorization_grant"
EFFECT_SIDE_EFFECT_AUTHORIZE = "side_effect_authorize"
EFFECT_QUEUE_RESERVE = "queue_reserve"
EFFECT_CHECKPOINT_SAVE = "checkpoint_save"
EFFECT_QUEUE_ACTIVATE = "queue_activate"
EFFECT_BINDING_FINALIZE = "binding_finalize"
TRANSITION_EFFECT_KINDS = frozenset(
    {
        EFFECT_EVENT_APPEND,
        EFFECT_OWNERSHIP_RESERVE,
        EFFECT_AUTHORIZATION_GRANT,
        EFFECT_SIDE_EFFECT_AUTHORIZE,
        EFFECT_QUEUE_RESERVE,
        EFFECT_CHECKPOINT_SAVE,
        EFFECT_QUEUE_ACTIVATE,
        EFFECT_BINDING_FINALIZE,
    }
)

TRANSITION_RUNTIME_NATIVE = "ananta-native"
TRANSITION_RUNTIME_LANGGRAPH = "langgraph"
TRANSITION_RUNTIMES = frozenset({TRANSITION_RUNTIME_NATIVE, TRANSITION_RUNTIME_LANGGRAPH})

_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,159}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_EFFECTS = 64
_MAX_EFFECT_PAYLOAD_BYTES = 524_288
_MAX_RESULT_PAYLOAD_BYTES = 524_288
_MAX_FINALIZATION_RESULT_PAYLOAD_BYTES = 1_100_000
_MAX_STATUS_BYTES = 524_288
_MAX_IDEMPOTENCY_KEY_CHARS = 512
_MAX_STAGE_ATTEMPTS = 1_000
_EFFECT_RESULT_MODES = frozenset({"adopt", "execute"})

FrozenJsonMapping = Mapping[str, Any]


class WorkflowTransitionError(RuntimeError):
    """Stable fail-closed transition validation or persistence failure."""


@dataclass(frozen=True)
class WorkflowTransitionEffect:
    """One immutable infrastructure intent plus its durable application proof."""

    effect_id: str
    transition_id: str
    ordinal: int
    kind: str
    idempotency_key: str
    payload: FrozenJsonMapping
    payload_digest: str
    state: str = EFFECT_STATE_PLANNED
    applied_generation: int = 0
    result_payload: FrozenJsonMapping = field(default_factory=dict)
    result_digest: str = ""
    revision: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    schema: str = WORKFLOW_TRANSITION_EFFECT_SCHEMA

    def __post_init__(self) -> None:
        _identity(self.effect_id, "effect_id")
        _identity(self.transition_id, "transition_id")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise WorkflowTransitionError("workflow_transition_effect_ordinal_invalid")
        if self.kind not in TRANSITION_EFFECT_KINDS:
            raise WorkflowTransitionError("workflow_transition_effect_kind_invalid")
        _bounded_text(
            self.idempotency_key,
            _MAX_IDEMPOTENCY_KEY_CHARS,
            "effect_idempotency_key",
        )
        if self.state not in EFFECT_STATES:
            raise WorkflowTransitionError("workflow_transition_effect_state_invalid")
        if self.schema != WORKFLOW_TRANSITION_EFFECT_SCHEMA:
            raise WorkflowTransitionError("workflow_transition_effect_schema_unsupported")
        _non_negative_integer(self.applied_generation, "effect_applied_generation")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise WorkflowTransitionError("workflow_transition_effect_revision_invalid")
        _timestamp(self.created_at, "effect_created_at", positive=True)
        _timestamp(self.updated_at, "effect_updated_at", positive=True)
        if self.updated_at < self.created_at:
            raise WorkflowTransitionError("workflow_transition_effect_timestamp_regressed")

        safe_payload = _validated_mapping(
            self.payload,
            maximum=_MAX_EFFECT_PAYLOAD_BYTES,
            reason="effect_payload",
        )
        if _digest(safe_payload, namespace="workflow-transition-effect-payload") != self.payload_digest:
            raise WorkflowTransitionError("workflow_transition_effect_payload_digest_mismatch")
        _sha256(self.payload_digest, "effect_payload_digest")

        safe_result = _validated_mapping(
            self.result_payload,
            maximum=(
                _MAX_FINALIZATION_RESULT_PAYLOAD_BYTES
                if self.kind == EFFECT_BINDING_FINALIZE
                else _MAX_RESULT_PAYLOAD_BYTES
            ),
            reason="effect_result",
        )
        if self.state == EFFECT_STATE_APPLIED:
            if self.applied_generation < 1 or not safe_result or not self.result_digest:
                raise WorkflowTransitionError("workflow_transition_effect_result_missing")
            _sha256(self.result_digest, "effect_result_digest")
            result_digest = (
                workflow_transition_finalization_result_digest(safe_result)
                if self.kind == EFFECT_BINDING_FINALIZE
                else workflow_transition_effect_result_digest(safe_result)
            )
            if result_digest != self.result_digest:
                raise WorkflowTransitionError("workflow_transition_effect_result_digest_mismatch")
        elif safe_result or self.result_digest:
            raise WorkflowTransitionError("workflow_transition_effect_result_unexpected")
        if self.state == EFFECT_STATE_PLANNED and self.applied_generation != 0:
            raise WorkflowTransitionError("workflow_transition_effect_generation_invalid")
        if self.state == EFFECT_STATE_APPLYING and self.applied_generation < 1:
            raise WorkflowTransitionError("workflow_transition_effect_generation_invalid")

        expected_id = workflow_transition_effect_id(
            transition_id=self.transition_id,
            ordinal=self.ordinal,
            kind=self.kind,
            idempotency_key=self.idempotency_key,
        )
        if self.effect_id != expected_id:
            raise WorkflowTransitionError("workflow_transition_effect_id_mismatch")
        object.__setattr__(self, "payload", _freeze_json_mapping(safe_payload))
        object.__setattr__(self, "result_payload", _freeze_json_mapping(safe_result))

    @classmethod
    def build(
        cls,
        *,
        transition_id: str,
        ordinal: int,
        kind: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        created_at: float,
    ) -> "WorkflowTransitionEffect":
        safe_payload = _validated_mapping(
            payload,
            maximum=_MAX_EFFECT_PAYLOAD_BYTES,
            reason="effect_payload",
        )
        return cls(
            effect_id=workflow_transition_effect_id(
                transition_id=transition_id,
                ordinal=ordinal,
                kind=kind,
                idempotency_key=idempotency_key,
            ),
            transition_id=transition_id,
            ordinal=ordinal,
            kind=kind,
            idempotency_key=idempotency_key,
            payload=safe_payload,
            payload_digest=_digest(
                safe_payload,
                namespace="workflow-transition-effect-payload",
            ),
            created_at=created_at,
            updated_at=created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "effect_id": self.effect_id,
            "transition_id": self.transition_id,
            "ordinal": self.ordinal,
            "kind": self.kind,
            "idempotency_key": self.idempotency_key,
            "payload": thaw_json(self.payload),
            "payload_digest": self.payload_digest,
            "state": self.state,
            "applied_generation": self.applied_generation,
            "result_payload": thaw_json(self.result_payload),
            "result_digest": self.result_digest,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class WorkflowTransition:
    """One Hub-owned recoverable start, advance, or command transition."""

    transition_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    runtime_id: str
    kind: str
    request_payload: FrozenJsonMapping
    request_fingerprint: str
    effect_fingerprint: str
    expected_revision: int
    expected_checkpoint_ref: str
    command_id: str = ""
    receipt_id: str = ""
    admitted_command_digest: str = ""
    state: str = TRANSITION_STATE_READY
    result_status: FrozenJsonMapping = field(default_factory=dict)
    result_checkpoint_ref: str = ""
    outcome_fingerprint: str = ""
    claim_owner: str = ""
    claim_generation: int = 0
    claim_expires_at: float = 0.0
    last_heartbeat_at: float = 0.0
    attempt_count: int = 0
    available_at: float = 0.0
    last_error: str = ""
    revision: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float = 0.0
    schema: str = WORKFLOW_TRANSITION_SCHEMA

    def __post_init__(self) -> None:
        for name in ("transition_id", "tenant_id", "workflow_id", "run_id"):
            _identity(getattr(self, name), name)
        if self.runtime_id not in TRANSITION_RUNTIMES:
            raise WorkflowTransitionError("workflow_transition_runtime_invalid")
        if self.kind not in TRANSITION_KINDS:
            raise WorkflowTransitionError("workflow_transition_kind_invalid")
        if self.schema != WORKFLOW_TRANSITION_SCHEMA:
            raise WorkflowTransitionError("workflow_transition_schema_unsupported")
        safe_request = _validated_mapping(
            self.request_payload,
            maximum=_MAX_EFFECT_PAYLOAD_BYTES,
            reason="request_payload",
            empty=False,
        )
        _sha256(self.request_fingerprint, "request_fingerprint")
        if workflow_transition_request_fingerprint(safe_request) != self.request_fingerprint:
            raise WorkflowTransitionError("workflow_transition_request_fingerprint_mismatch")
        _sha256(self.effect_fingerprint, "effect_fingerprint")
        _non_negative_integer(self.expected_revision, "expected_revision")
        _bounded_text(self.expected_checkpoint_ref, 512, "expected_checkpoint_ref")
        _non_negative_integer(self.claim_generation, "claim_generation")
        _non_negative_integer(self.attempt_count, "attempt_count")
        if self.attempt_count != self.claim_generation:
            raise WorkflowTransitionError("workflow_transition_header_attempt_conflict")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise WorkflowTransitionError("workflow_transition_revision_invalid")

        if self.kind == TRANSITION_KIND_COMMAND:
            _identity(self.command_id, "command_id")
            _sha256(self.admitted_command_digest, "admitted_command_digest")
        elif self.command_id or self.admitted_command_digest or self.receipt_id:
            raise WorkflowTransitionError("workflow_transition_command_fields_unexpected")
        if self.receipt_id:
            _identity(self.receipt_id, "receipt_id")
            if self.kind != TRANSITION_KIND_COMMAND or self.receipt_id != self.command_id:
                raise WorkflowTransitionError("workflow_transition_receipt_binding_invalid")

        if self.state not in TRANSITION_STATES:
            raise WorkflowTransitionError("workflow_transition_state_invalid")
        if self.state == TRANSITION_STATE_APPLYING:
            _identity(self.claim_owner, "claim_owner")
            if self.claim_generation < 1 or self.claim_expires_at <= 0 or self.last_heartbeat_at <= 0:
                raise WorkflowTransitionError("workflow_transition_claim_invalid")
        elif self.claim_owner or self.claim_expires_at != 0:
            raise WorkflowTransitionError("workflow_transition_claim_invalid")

        for name in (
            "claim_expires_at",
            "last_heartbeat_at",
            "available_at",
            "created_at",
            "updated_at",
            "completed_at",
        ):
            _timestamp(
                getattr(self, name),
                name,
                positive=name in {"created_at", "updated_at"},
            )
        if self.updated_at < self.created_at:
            raise WorkflowTransitionError("workflow_transition_timestamp_regressed")
        if self.available_at < self.created_at:
            raise WorkflowTransitionError("workflow_transition_available_at_invalid")

        safe_result = _validated_mapping(
            self.result_status,
            maximum=_MAX_STATUS_BYTES,
            reason="result_status",
        )
        if self.state == TRANSITION_STATE_COMPLETED:
            if not safe_result or not self.result_checkpoint_ref or not self.outcome_fingerprint:
                raise WorkflowTransitionError("workflow_transition_completion_proof_missing")
            _bounded_text(self.result_checkpoint_ref, 512, "result_checkpoint_ref")
            _sha256(self.outcome_fingerprint, "outcome_fingerprint")
            if self.completed_at <= 0:
                raise WorkflowTransitionError("workflow_transition_completed_at_invalid")
        elif safe_result or self.result_checkpoint_ref or self.outcome_fingerprint or self.completed_at:
            raise WorkflowTransitionError("workflow_transition_completion_proof_unexpected")
        if self.state in {TRANSITION_STATE_QUARANTINED, TRANSITION_STATE_REJECTED}:
            _reason_code(self.last_error)
        elif self.last_error:
            _reason_code(self.last_error)

        object.__setattr__(self, "request_payload", _freeze_json_mapping(safe_request))
        object.__setattr__(self, "result_status", _freeze_json_mapping(safe_result))

    @classmethod
    def build(
        cls,
        *,
        transition_id: str,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        runtime_id: str,
        kind: str,
        request_payload: Mapping[str, Any],
        effects: Sequence[WorkflowTransitionEffect],
        expected_revision: int,
        expected_checkpoint_ref: str,
        created_at: float,
        command_id: str = "",
        receipt_id: str = "",
        admitted_command: Mapping[str, Any] | None = None,
    ) -> "WorkflowTransition":
        admitted_digest = workflow_admitted_command_digest(admitted_command) if admitted_command is not None else ""
        return cls(
            transition_id=transition_id,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            runtime_id=runtime_id,
            kind=kind,
            request_payload=request_payload,
            command_id=command_id,
            receipt_id=receipt_id,
            request_fingerprint=workflow_transition_request_fingerprint(request_payload),
            admitted_command_digest=admitted_digest,
            effect_fingerprint=workflow_transition_effect_fingerprint(effects),
            expected_revision=expected_revision,
            expected_checkpoint_ref=expected_checkpoint_ref,
            available_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "transition_id": self.transition_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "kind": self.kind,
            "request_payload": thaw_json(self.request_payload),
            "command_id": self.command_id,
            "receipt_id": self.receipt_id,
            "request_fingerprint": self.request_fingerprint,
            "admitted_command_digest": self.admitted_command_digest,
            "effect_fingerprint": self.effect_fingerprint,
            "expected_revision": self.expected_revision,
            "expected_checkpoint_ref": self.expected_checkpoint_ref,
            "state": self.state,
            "result_status": thaw_json(self.result_status),
            "result_checkpoint_ref": self.result_checkpoint_ref,
            "outcome_fingerprint": self.outcome_fingerprint,
            "claim_owner": self.claim_owner,
            "claim_generation": self.claim_generation,
            "claim_expires_at": self.claim_expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "attempt_count": self.attempt_count,
            "available_at": self.available_at,
            "last_error": self.last_error,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class WorkflowTransitionSnapshot:
    """Immutable transition aggregate returned by every store adapter."""

    transition: WorkflowTransition
    effects: tuple[WorkflowTransitionEffect, ...]

    def __post_init__(self) -> None:
        normalized = _validated_effects(
            self.effects,
            transition_id=self.transition.transition_id,
        )
        final_effects = [effect for effect in normalized if effect.kind == EFFECT_BINDING_FINALIZE]
        if len(final_effects) != 1 or final_effects[0].ordinal != len(normalized):
            raise WorkflowTransitionError("workflow_transition_binding_finalize_effect_invalid")
        if workflow_transition_effect_fingerprint(normalized) != self.transition.effect_fingerprint:
            raise WorkflowTransitionError("workflow_transition_effect_fingerprint_mismatch")
        if self.transition.state == TRANSITION_STATE_COMPLETED and any(
            effect.state != EFFECT_STATE_APPLIED for effect in normalized
        ):
            raise WorkflowTransitionError("workflow_transition_completion_effect_proof_missing")
        if self.transition.state == TRANSITION_STATE_REJECTED and any(
            effect.state != EFFECT_STATE_REJECTED for effect in normalized
        ):
            raise WorkflowTransitionError("workflow_transition_rejection_effect_proof_missing")
        object.__setattr__(self, "effects", normalized)


class WorkflowTransitionStagePort(Protocol):
    """Stage one immutable aggregate before infrastructure effects."""

    def stage(
        self,
        transition: WorkflowTransition,
        effects: Sequence[WorkflowTransitionEffect],
        *,
        receipt_id: str = "",
    ) -> WorkflowTransitionSnapshot: ...


class WorkflowTransitionReadPort(Protocol):
    """Read transition aggregates without acquiring execution authority."""

    def get(self, transition_id: str) -> WorkflowTransitionSnapshot | None: ...

    def get_active(self, workflow_id: str) -> WorkflowTransitionSnapshot | None: ...


class WorkflowTransitionLeasePort(Protocol):
    """Acquire, renew, or release generation-fenced transition leases."""

    def claim(
        self,
        transition_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot | None: ...

    def claim_due(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[WorkflowTransitionSnapshot, ...]: ...

    def heartbeat(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot: ...

    def release(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
        retry_at: float,
    ) -> WorkflowTransitionSnapshot: ...

    def yield_ready(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        available_at: float,
    ) -> WorkflowTransitionSnapshot: ...


class WorkflowTransitionEffectPort(Protocol):
    """Record exact effect begin/result proofs under a transition lease."""

    def begin_effect(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
    ) -> WorkflowTransitionEffect: ...

    def finish_effect(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        result_payload: Mapping[str, Any],
        result_digest: str,
    ) -> WorkflowTransitionEffect: ...


class WorkflowTransitionCompletionPort(Protocol):
    """Atomically reject or publish a transition's terminal proof."""

    def reject(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot: ...

    def finalize(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        binding_status: Mapping[str, Any],
        checkpoint_ref: str,
        finalization_proof: Mapping[str, Any],
        outcome_fingerprint: str = "",
        receipt_result: Mapping[str, Any] | None = None,
    ) -> WorkflowTransitionSnapshot: ...


class WorkflowTransitionQuarantinePort(Protocol):
    """Hold an ambiguous transition for explicit, separately authorized recovery."""

    def quarantine(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot: ...


class WorkflowTransitionPublicProjectionPort(Protocol):
    """Derive canonical public state from raw runtime state under the binding lock."""

    def project(
        self,
        *,
        transition: WorkflowTransition,
        binding: Mapping[str, Any],
        binding_status: Mapping[str, Any],
        previous_public_status: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]: ...


WorkflowTransitionReceiptProjectionPort = WorkflowTransitionPublicProjectionPort


class WorkflowTransitionStore(
    WorkflowTransitionStagePort,
    WorkflowTransitionReadPort,
    WorkflowTransitionLeasePort,
    WorkflowTransitionEffectPort,
    WorkflowTransitionCompletionPort,
    WorkflowTransitionQuarantinePort,
    Protocol,
):
    """Convenience aggregate; consumers should depend on the smallest port."""


def workflow_transition_id(
    *,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    runtime_id: str,
    kind: str,
    identity_key: str,
) -> str:
    """Return an opaque stable transition ID for one semantic transition."""

    for value, name in (
        (tenant_id, "tenant_id"),
        (workflow_id, "workflow_id"),
        (run_id, "run_id"),
        (identity_key, "identity_key"),
    ):
        _identity(value, name)
    if runtime_id not in TRANSITION_RUNTIMES:
        raise WorkflowTransitionError("workflow_transition_runtime_invalid")
    if kind not in TRANSITION_KINDS:
        raise WorkflowTransitionError("workflow_transition_kind_invalid")
    return _opaque_id(
        "wft",
        tenant_id,
        workflow_id,
        run_id,
        runtime_id,
        kind,
        identity_key,
    )


def workflow_transition_effect_id(
    *,
    transition_id: str,
    ordinal: int,
    kind: str,
    idempotency_key: str,
) -> str:
    _identity(transition_id, "transition_id")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise WorkflowTransitionError("workflow_transition_effect_ordinal_invalid")
    if kind not in TRANSITION_EFFECT_KINDS:
        raise WorkflowTransitionError("workflow_transition_effect_kind_invalid")
    _bounded_text(
        idempotency_key,
        _MAX_IDEMPOTENCY_KEY_CHARS,
        "effect_idempotency_key",
    )
    return _opaque_id("wfx", transition_id, ordinal, kind, idempotency_key)


def workflow_transition_request_fingerprint(payload: Mapping[str, Any]) -> str:
    safe = _validated_mapping(
        payload,
        maximum=_MAX_EFFECT_PAYLOAD_BYTES,
        reason="request_payload",
    )
    return _digest(safe, namespace="workflow-transition-request")


def workflow_admitted_command_digest(command: Mapping[str, Any]) -> str:
    """Compare command bodies without treating renewable authority as semantics.

    The Receipt ledger may recognize a v2/v3 envelope with renewed signature,
    key, nonce, or validity window as the same semantic command.  It must still
    retain the originally admitted envelope: a transition request fingerprint
    binds that exact persisted Receipt and cannot be renewed after staging.  The
    neutral contract's semantic payload is the version-independent comparison
    boundary.  Non-command mappings retain their complete JSON identity.
    """

    safe = _validated_mapping(
        command,
        maximum=_MAX_EFFECT_PAYLOAD_BYTES,
        reason="admitted_command",
        empty=False,
    )
    semantic: Mapping[str, Any] = safe
    if "command_type" in safe:
        try:
            semantic = WorkflowCommand.semantic_payload_for_mapping(safe)
        except (TemporalContractError, TypeError, ValueError) as exc:
            raise WorkflowTransitionError("workflow_transition_admitted_command_invalid") from exc
    return _digest(semantic, namespace="workflow-transition-admitted-command")


def workflow_transition_effect_result_digest(payload: Mapping[str, Any]) -> str:
    safe = _validated_mapping(
        payload,
        maximum=_MAX_RESULT_PAYLOAD_BYTES,
        reason="effect_result",
        empty=False,
    )
    return _digest(safe, namespace="workflow-transition-effect-result")


def workflow_transition_finalization_result_digest(payload: Mapping[str, Any]) -> str:
    """Hash the bounded, store-owned binding-finalization proof."""

    safe = _validated_mapping(
        payload,
        maximum=_MAX_FINALIZATION_RESULT_PAYLOAD_BYTES,
        reason="finalization_result",
        empty=False,
    )
    return _digest(safe, namespace="workflow-transition-effect-result")


def workflow_transition_effect_result_envelope(
    *,
    mode: str,
    result_payload: Mapping[str, Any],
    proof_payload: Mapping[str, Any],
    stage_attempt_count: int,
) -> dict[str, Any]:
    """Build the canonical durable proof for one non-final effect result."""

    if not isinstance(mode, str) or mode not in _EFFECT_RESULT_MODES:
        raise WorkflowTransitionError("workflow_transition_effect_result_mode_invalid")
    if (
        isinstance(stage_attempt_count, bool)
        or not isinstance(stage_attempt_count, int)
        or not 1 <= stage_attempt_count <= _MAX_STAGE_ATTEMPTS
    ):
        raise WorkflowTransitionError("workflow_transition_effect_stage_attempt_invalid")
    result = _validated_mapping(
        result_payload,
        maximum=_MAX_RESULT_PAYLOAD_BYTES,
        reason="effect_result_component",
        empty=False,
    )
    proof = _validated_mapping(
        proof_payload,
        maximum=_MAX_RESULT_PAYLOAD_BYTES,
        reason="effect_proof_component",
        empty=False,
    )
    envelope = {
        "schema": WORKFLOW_TRANSITION_EFFECT_RESULT_SCHEMA,
        "mode": mode,
        "effect_result": result,
        "effect_proof": proof,
        "stage_attempt_count": stage_attempt_count,
    }
    return _validated_mapping(
        envelope,
        maximum=_MAX_RESULT_PAYLOAD_BYTES,
        reason="effect_result_envelope",
        empty=False,
    )


def workflow_transition_effect_stage_attempt_count(
    result_payload: Mapping[str, Any],
) -> int:
    """Validate a persisted canonical result envelope and return its stage count."""

    if not isinstance(result_payload, Mapping) or set(result_payload) != {
        "schema",
        "mode",
        "effect_result",
        "effect_proof",
        "stage_attempt_count",
    }:
        raise WorkflowTransitionError("workflow_transition_effect_result_envelope_invalid")
    if result_payload.get("schema") != WORKFLOW_TRANSITION_EFFECT_RESULT_SCHEMA:
        raise WorkflowTransitionError("workflow_transition_effect_result_envelope_invalid")
    expected = workflow_transition_effect_result_envelope(
        mode=result_payload.get("mode"),
        result_payload=result_payload.get("effect_result"),
        proof_payload=result_payload.get("effect_proof"),
        stage_attempt_count=result_payload.get("stage_attempt_count"),
    )
    if canonical_json(expected) != canonical_json(thaw_json(result_payload)):
        raise WorkflowTransitionError("workflow_transition_effect_result_envelope_invalid")
    return int(expected["stage_attempt_count"])


def workflow_transition_finalization_stage_attempt_count(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
) -> int:
    """Validate the applied prefix and return the finalization-stage attempts."""

    values = _validated_effects(effects, transition_id=transition.transition_id)
    if transition.attempt_count != transition.claim_generation:
        raise WorkflowTransitionError("workflow_transition_header_attempt_conflict")
    non_final = [effect for effect in values if effect.kind != EFFECT_BINDING_FINALIZE]
    if any(effect.state != EFFECT_STATE_APPLIED for effect in non_final):
        raise WorkflowTransitionError("workflow_transition_effects_incomplete")
    previous_generation = 0
    for effect in non_final:
        if effect.applied_generation <= previous_generation or effect.applied_generation >= transition.claim_generation:
            raise WorkflowTransitionError("workflow_transition_effect_application_generation_invalid")
        stage_attempts = workflow_transition_effect_stage_attempt_count(effect.result_payload)
        if stage_attempts != effect.applied_generation - previous_generation:
            raise WorkflowTransitionError("workflow_transition_effect_stage_attempt_invalid")
        previous_generation = effect.applied_generation
    finalization_attempts = transition.claim_generation - previous_generation
    if finalization_attempts < 1:
        raise WorkflowTransitionError("workflow_transition_effect_stage_attempt_invalid")
    return finalization_attempts


def workflow_transition_effect_fingerprint(
    effects: Sequence[WorkflowTransitionEffect],
) -> str:
    values = tuple(effects)
    if not values or len(values) > _MAX_EFFECTS:
        raise WorkflowTransitionError("workflow_transition_effect_count_invalid")
    descriptor = [
        {
            "effect_id": effect.effect_id,
            "ordinal": effect.ordinal,
            "kind": effect.kind,
            "idempotency_key": effect.idempotency_key,
            "payload_digest": effect.payload_digest,
        }
        for effect in values
    ]
    return _digest(descriptor, namespace="workflow-transition-effect-plan")


def workflow_transition_outcome_fingerprint(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
    *,
    binding_status: Mapping[str, Any],
    checkpoint_ref: str,
    finalization_proof: Mapping[str, Any],
    public_status: Mapping[str, Any] | None = None,
    receipt_result: Mapping[str, Any] | None = None,
) -> str:
    """Bind effects, raw binding state, and its canonical public projection."""

    values = _validated_effects(effects, transition_id=transition.transition_id)
    non_final = [effect for effect in values if effect.kind != EFFECT_BINDING_FINALIZE]
    finalization_attempts = workflow_transition_finalization_stage_attempt_count(
        transition,
        values,
    )
    status = _validated_mapping(
        binding_status,
        maximum=_MAX_STATUS_BYTES,
        reason="binding_status",
        empty=False,
    )
    _bounded_text(checkpoint_ref, 512, "checkpoint_ref")
    if public_status is not None and receipt_result is not None:
        raise WorkflowTransitionError("workflow_transition_public_status_ambiguous")
    projected = public_status if public_status is not None else receipt_result
    if projected is None:
        raise WorkflowTransitionError("workflow_transition_public_status_missing")
    canonical_status = _validated_mapping(
        projected,
        maximum=_MAX_STATUS_BYTES,
        reason="public_status",
        empty=False,
    )
    proof = _validated_mapping(
        finalization_proof,
        maximum=_MAX_EFFECT_PAYLOAD_BYTES,
        reason="finalization_proof",
        empty=False,
    )
    return _digest(
        {
            "transition_id": transition.transition_id,
            "command_id": transition.command_id,
            "request_fingerprint": transition.request_fingerprint,
            "effect_fingerprint": transition.effect_fingerprint,
            "effect_results": [
                {
                    "effect_id": effect.effect_id,
                    "result_digest": effect.result_digest,
                }
                for effect in non_final
            ],
            "binding_status": status,
            "checkpoint_ref": checkpoint_ref,
            "finalization_stage_attempt_count": finalization_attempts,
            "finalization_proof": proof,
            "public_status": canonical_status,
        },
        namespace="workflow-transition-outcome",
    )


def validate_transition_plan(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
) -> tuple[WorkflowTransitionEffect, ...]:
    """Validate the immutable stage candidate and return its effect tuple."""

    if transition.state != TRANSITION_STATE_READY or any(
        (
            transition.claim_owner,
            transition.claim_generation,
            transition.claim_expires_at,
            transition.last_heartbeat_at,
            transition.attempt_count,
            transition.last_error,
        )
    ):
        raise WorkflowTransitionError("workflow_transition_stage_state_invalid")
    if transition.revision != 1 or transition.updated_at != transition.created_at:
        raise WorkflowTransitionError("workflow_transition_stage_revision_invalid")
    values = _validated_effects(effects, transition_id=transition.transition_id)
    if any(
        effect.state != EFFECT_STATE_PLANNED
        or effect.revision != 1
        or effect.created_at != transition.created_at
        or effect.updated_at != transition.created_at
        for effect in values
    ):
        raise WorkflowTransitionError("workflow_transition_effect_stage_state_invalid")
    final_effects = [effect for effect in values if effect.kind == EFFECT_BINDING_FINALIZE]
    if len(final_effects) != 1 or final_effects[0].ordinal != len(values):
        raise WorkflowTransitionError("workflow_transition_binding_finalize_effect_invalid")
    if workflow_transition_effect_fingerprint(values) != transition.effect_fingerprint:
        raise WorkflowTransitionError("workflow_transition_effect_fingerprint_mismatch")
    return values


def thaw_json(value: Any) -> Any:
    """Return a detached JSON-compatible copy of a frozen contract value."""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value


def _validated_effects(
    effects: Sequence[WorkflowTransitionEffect],
    *,
    transition_id: str,
) -> tuple[WorkflowTransitionEffect, ...]:
    values = tuple(effects)
    if not values or len(values) > _MAX_EFFECTS:
        raise WorkflowTransitionError("workflow_transition_effect_count_invalid")
    if any(not isinstance(effect, WorkflowTransitionEffect) for effect in values):
        raise WorkflowTransitionError("workflow_transition_effect_invalid")
    if [effect.ordinal for effect in values] != list(range(1, len(values) + 1)):
        raise WorkflowTransitionError("workflow_transition_effect_order_invalid")
    if any(effect.transition_id != transition_id for effect in values):
        raise WorkflowTransitionError("workflow_transition_effect_binding_mismatch")
    if len({effect.effect_id for effect in values}) != len(values):
        raise WorkflowTransitionError("workflow_transition_effect_id_duplicate")
    if len({effect.idempotency_key for effect in values}) != len(values):
        raise WorkflowTransitionError("workflow_transition_effect_idempotency_duplicate")
    return values


def _validated_mapping(
    value: Any,
    *,
    maximum: int,
    reason: str,
    empty: bool = True,
) -> dict[str, Any]:
    try:
        thawed = thaw_json(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid") from exc
    if not isinstance(thawed, dict) or (not empty and not thawed):
        raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid")
    _validate_json_value(thawed, reason=reason)
    try:
        size = len(canonical_json(thawed).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid") from exc
    if size > maximum:
        raise WorkflowTransitionError(f"workflow_transition_{reason}_too_large")
    return thawed


def _validate_json_value(value: Any, *, reason: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, reason=reason)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256 or "\x00" in key:
                raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid")
            _validate_json_value(item, reason=reason)
        return
    raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid")


def _freeze_json_mapping(value: Mapping[str, Any]) -> FrozenJsonMapping:
    frozen = _freeze_json(dict(value))
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded by caller
        raise WorkflowTransitionError("workflow_transition_mapping_invalid")
    return frozen


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _digest(value: Any, *, namespace: str) -> str:
    framed = {
        "namespace": namespace,
        "value": thaw_json(value),
    }
    return hashlib.sha256(canonical_json(framed).encode("utf-8")).hexdigest()


def _opaque_id(namespace: str, *parts: object) -> str:
    framed = "\x1f".join([namespace, *(str(part) for part in parts)])
    return f"{namespace}-{hashlib.sha256(framed.encode('utf-8')).hexdigest()}"


def _identity(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return value


def _bounded_text(value: Any, maximum: int, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(not character.isprintable() or character in {"\x00", "\x7f"} for character in value)
    ):
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return value


def _sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return value


def _non_negative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return value


def _timestamp(value: Any, field_name: str, *, positive: bool) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < (1e-12 if positive else 0.0)
    ):
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return float(value)


def _reason_code(value: Any) -> str:
    if not isinstance(value, str) or _REASON_RE.fullmatch(value) is None:
        raise WorkflowTransitionError("workflow_transition_reason_code_invalid")
    return value


__all__ = [
    "EFFECT_AUTHORIZATION_GRANT",
    "EFFECT_BINDING_FINALIZE",
    "EFFECT_CHECKPOINT_SAVE",
    "EFFECT_EVENT_APPEND",
    "EFFECT_OWNERSHIP_RESERVE",
    "EFFECT_QUEUE_ACTIVATE",
    "EFFECT_QUEUE_RESERVE",
    "EFFECT_SIDE_EFFECT_AUTHORIZE",
    "EFFECT_STATE_APPLIED",
    "EFFECT_STATE_APPLYING",
    "EFFECT_STATE_PLANNED",
    "EFFECT_STATE_REJECTED",
    "TRANSITION_KIND_ADVANCE",
    "TRANSITION_KIND_COMMAND",
    "TRANSITION_KIND_START",
    "TRANSITION_RUNTIME_LANGGRAPH",
    "TRANSITION_RUNTIME_NATIVE",
    "TRANSITION_STATE_APPLYING",
    "TRANSITION_STATE_COMPLETED",
    "TRANSITION_STATE_QUARANTINED",
    "TRANSITION_STATE_READY",
    "TRANSITION_STATE_REJECTED",
    "WorkflowTransition",
    "WorkflowTransitionEffect",
    "WorkflowTransitionError",
    "WorkflowTransitionCompletionPort",
    "WorkflowTransitionEffectPort",
    "WorkflowTransitionLeasePort",
    "WorkflowTransitionPublicProjectionPort",
    "WorkflowTransitionQuarantinePort",
    "WorkflowTransitionReadPort",
    "WorkflowTransitionReceiptProjectionPort",
    "WorkflowTransitionSnapshot",
    "WorkflowTransitionStagePort",
    "WorkflowTransitionStore",
    "WORKFLOW_TRANSITION_EFFECT_RESULT_SCHEMA",
    "thaw_json",
    "validate_transition_plan",
    "workflow_admitted_command_digest",
    "workflow_transition_effect_fingerprint",
    "workflow_transition_effect_id",
    "workflow_transition_effect_result_digest",
    "workflow_transition_effect_result_envelope",
    "workflow_transition_effect_stage_attempt_count",
    "workflow_transition_finalization_result_digest",
    "workflow_transition_finalization_stage_attempt_count",
    "workflow_transition_id",
    "workflow_transition_outcome_fingerprint",
    "workflow_transition_request_fingerprint",
]
