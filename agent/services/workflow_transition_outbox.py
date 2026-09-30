"""Immutable Hub-owned workflow transition and effect-ledger contracts.

The transition outbox is deliberately runtime-neutral within the two local
production runtimes.  It records immutable intent before infrastructure ports
are called; persistence adapters own leasing and compare-and-set transitions.
No worker execution or runtime composition belongs in this module.

The contract is split by responsibility: vocabulary constants, JSON and
identity primitives, digests, and the immutable value types live in sibling
``workflow_transition_outbox_*`` modules.  This module remains the public
entry point and owns the narrow persistence ports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent.services.workflow_transition_outbox_digests import (
    workflow_admitted_command_digest,
    workflow_transition_effect_id,
    workflow_transition_effect_result_digest,
    workflow_transition_effect_result_envelope,
    workflow_transition_effect_stage_attempt_count,
    workflow_transition_finalization_result_digest,
    workflow_transition_id,
    workflow_transition_request_fingerprint,
)
from agent.services.workflow_transition_outbox_model import (  # noqa: F401 - compatibility re-exports
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionSnapshot,
    _validated_effects,
    validate_transition_plan,
    workflow_transition_effect_fingerprint,
    workflow_transition_finalization_stage_attempt_count,
    workflow_transition_outcome_fingerprint,
)
from agent.services.workflow_transition_outbox_validation import (  # noqa: F401 - compatibility re-exports
    _EFFECT_RESULT_MODES,
    _IDENTITY_RE,
    _MAX_EFFECT_PAYLOAD_BYTES,
    _MAX_EFFECTS,
    _MAX_FINALIZATION_RESULT_PAYLOAD_BYTES,
    _MAX_IDEMPOTENCY_KEY_CHARS,
    _MAX_RESULT_PAYLOAD_BYTES,
    _MAX_STAGE_ATTEMPTS,
    _MAX_STATUS_BYTES,
    _REASON_RE,
    _SHA256_RE,
    FrozenJsonMapping,
    WorkflowTransitionError,
    _bounded_text,
    _digest,
    _freeze_json,
    _freeze_json_mapping,
    _identity,
    _non_negative_integer,
    _opaque_id,
    _reason_code,
    _sha256,
    _timestamp,
    _validate_json_value,
    _validated_mapping,
    thaw_json,
)
from agent.services.workflow_transition_outbox_vocabulary import (  # noqa: F401 - compatibility re-exports
    EFFECT_AUTHORIZATION_GRANT,
    EFFECT_BINDING_FINALIZE,
    EFFECT_CHECKPOINT_SAVE,
    EFFECT_EVENT_APPEND,
    EFFECT_OWNERSHIP_RESERVE,
    EFFECT_QUEUE_ACTIVATE,
    EFFECT_QUEUE_RESERVE,
    EFFECT_SIDE_EFFECT_AUTHORIZE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    EFFECT_STATE_REJECTED,
    EFFECT_STATES,
    TRANSITION_EFFECT_KINDS,
    TRANSITION_KIND_ADVANCE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_KIND_START,
    TRANSITION_KINDS,
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_RUNTIMES,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_QUARANTINED,
    TRANSITION_STATE_READY,
    TRANSITION_STATE_REJECTED,
    TRANSITION_STATES,
    TRANSITION_TERMINAL_STATES,
    WORKFLOW_TRANSITION_EFFECT_RESULT_SCHEMA,
    WORKFLOW_TRANSITION_EFFECT_SCHEMA,
    WORKFLOW_TRANSITION_SCHEMA,
)


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
