"""Opaque identifiers, digests and result envelopes of transition contracts.

These functions depend only on validated JSON values, never on the
transition dataclasses, so both the model and adapters can share them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_transition_outbox_validation import (
    _EFFECT_RESULT_MODES,
    _MAX_EFFECT_PAYLOAD_BYTES,
    _MAX_FINALIZATION_RESULT_PAYLOAD_BYTES,
    _MAX_IDEMPOTENCY_KEY_CHARS,
    _MAX_RESULT_PAYLOAD_BYTES,
    _MAX_STAGE_ATTEMPTS,
    WorkflowTransitionError,
    _bounded_text,
    _digest,
    _identity,
    _opaque_id,
    _validated_mapping,
    thaw_json,
)
from agent.services.workflow_transition_outbox_vocabulary import (
    TRANSITION_EFFECT_KINDS,
    TRANSITION_KINDS,
    TRANSITION_RUNTIMES,
    WORKFLOW_TRANSITION_EFFECT_RESULT_SCHEMA,
)
from ananta_contracts.temporal_workflow import TemporalContractError, WorkflowCommand


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
