"""Pre-cutover admission of exact Hub command-transition intents.

This module deliberately stages or adopts immutable intent only.  It does not
claim transitions, execute Native/LangGraph effects, or reconcile terminal
receipts; those responsibilities belong to later cutover slices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandReceipt,
    admitted_receipt_command,
)
from agent.services.workflow_transition_outbox import (
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionSnapshot,
    WorkflowTransitionStagePort,
    workflow_admitted_command_digest,
    workflow_transition_id,
    workflow_transition_request_fingerprint,
)


class WorkflowCommandTransitionAdmissionError(RuntimeError):
    """Stable fail-closed admission or attribution conflict."""


@dataclass(frozen=True)
class WorkflowCommandTransitionIntent:
    """One deterministic transition candidate before persistence."""

    transition: WorkflowTransition
    effects: tuple[WorkflowTransitionEffect, ...]


class WorkflowCommandTransitionIntentFactory(Protocol):
    """Build one complete, runtime-specific immutable command plan.

    There is intentionally no default implementation: a partial generic plan
    could not later be extended after it was staged.  Slice 2 therefore only
    offers this injection seam for tests and later runtime-specific planners.
    """

    def build(
        self,
        *,
        receipt: WorkflowControlCommandReceipt,
        binding: WorkflowControlRunBinding,
    ) -> WorkflowCommandTransitionIntent: ...


class WorkflowCommandTransitionAdmissionPort(Protocol):
    """Stage or exactly adopt one already-admitted command transition."""

    def stage_or_adopt(
        self,
        *,
        receipt: WorkflowControlCommandReceipt,
        binding: WorkflowControlRunBinding,
    ) -> WorkflowTransitionSnapshot: ...


class WorkflowCommandTransitionAdmissionService:
    """Persist an injected complete intent through the narrow stage port."""

    def __init__(
        self,
        transitions: WorkflowTransitionStagePort,
        *,
        intent_factory: WorkflowCommandTransitionIntentFactory,
    ) -> None:
        self._transitions = transitions
        self._intent_factory = intent_factory

    def stage_or_adopt(
        self,
        *,
        receipt: WorkflowControlCommandReceipt,
        binding: WorkflowControlRunBinding,
    ) -> WorkflowTransitionSnapshot:
        _assert_receipt_binding(receipt, binding)
        intent = self._intent_factory.build(receipt=receipt, binding=binding)
        _assert_intent(receipt, binding, intent)
        return self._transitions.stage(
            intent.transition,
            intent.effects,
            receipt_id=receipt.command_id,
        )


def _assert_receipt_binding(
    receipt: WorkflowControlCommandReceipt,
    binding: WorkflowControlRunBinding,
) -> None:
    if (
        receipt.tenant_id != binding.tenant_id
        or receipt.workflow_id != binding.workflow_id
        or receipt.run_id != binding.run_id
    ):
        raise WorkflowCommandTransitionAdmissionError("workflow_command_transition_binding_mismatch")


def _assert_existing_attribution(
    receipt: WorkflowControlCommandReceipt,
    transition: WorkflowTransition,
) -> None:
    if receipt.request_fingerprint and receipt.request_fingerprint != transition.request_fingerprint:
        raise WorkflowCommandTransitionAdmissionError("workflow_command_transition_request_conflict")
    if receipt.transition_id and receipt.transition_id != transition.transition_id:
        raise WorkflowCommandTransitionAdmissionError("workflow_command_transition_id_conflict")
    if receipt.effect_fingerprint and receipt.effect_fingerprint != transition.effect_fingerprint:
        raise WorkflowCommandTransitionAdmissionError("workflow_command_transition_effect_conflict")


def _assert_intent(
    receipt: WorkflowControlCommandReceipt,
    binding: WorkflowControlRunBinding,
    intent: WorkflowCommandTransitionIntent,
) -> None:
    transition = intent.transition
    command = admitted_receipt_command(receipt)
    binding_runtime = (
        TRANSITION_RUNTIME_NATIVE
        if binding.runtime_id in {"local", TRANSITION_RUNTIME_NATIVE}
        else binding.runtime_id
    )
    expected_transition_id = workflow_transition_id(
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        runtime_id=transition.runtime_id,
        kind=TRANSITION_KIND_COMMAND,
        identity_key=receipt.command_id,
    )
    if (
        transition.kind != TRANSITION_KIND_COMMAND
        or binding_runtime not in {TRANSITION_RUNTIME_NATIVE, TRANSITION_RUNTIME_LANGGRAPH}
        or transition.runtime_id != binding_runtime
        or transition.transition_id != expected_transition_id
        or transition.tenant_id != binding.tenant_id
        or transition.workflow_id != binding.workflow_id
        or transition.run_id != binding.run_id
        or transition.command_id != receipt.command_id
        or transition.receipt_id != receipt.command_id
        or transition.expected_revision != receipt.expected_revision
        or transition.expected_checkpoint_ref != receipt.checkpoint_ref
        or transition.request_fingerprint
        != workflow_transition_request_fingerprint(receipt.request_payload)
        or transition.admitted_command_digest
        != workflow_admitted_command_digest(command.to_dict())
    ):
        raise WorkflowCommandTransitionAdmissionError("workflow_command_transition_intent_mismatch")
    _assert_existing_attribution(receipt, transition)


__all__ = [
    "WorkflowCommandTransitionAdmissionError",
    "WorkflowCommandTransitionAdmissionPort",
    "WorkflowCommandTransitionAdmissionService",
    "WorkflowCommandTransitionIntent",
    "WorkflowCommandTransitionIntentFactory",
]
