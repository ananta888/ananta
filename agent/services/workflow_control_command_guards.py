"""Pure command binding, snapshot, step, and start-status guards shared by the workflow control boundary."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from agent.services.workflow_backend import WORKFLOW_STATUS_SCHEMA
from agent.services.workflow_control_bindings import WorkflowControlBindingStore, WorkflowControlRunBinding
from agent.services.workflow_control_command_receipts import WorkflowControlCommandRejectedError
from agent.services.workflow_runtime_selection_composition import configured_runtime_id
from agent.services.workflow_runtime_status_projection import authoritative_runtime_status
from agent.services.workflow_transition_public_projection import canonical_workflow_public_status

_FAILED_START_STATUSES = frozenset({"degraded", "unavailable", "not_found"})
# One attributed command plans three effects, so four bounded drive attempts
# leave room for a single retry without ever becoming an unbounded wait.
_TRANSITION_DRIVE_ATTEMPTS = 4


def _assert_client_command_bindings(
    binding: WorkflowControlRunBinding,
    *,
    command_type: str,
    command_id: str,
    expected_revision: int | None,
    plan_hash: str | None,
    step_id: str | None,
    run_id: str | None,
) -> None:
    """Validate caller bindings before signing, including required message scope."""
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        raise WorkflowControlCommandRejectedError("workflow_control_revision_invalid")
    if plan_hash is not None and plan_hash != binding.plan_hash:
        raise WorkflowControlCommandRejectedError("workflow_control_plan_binding_mismatch")
    if run_id is not None and run_id != binding.run_id:
        raise WorkflowControlCommandRejectedError("workflow_control_run_binding_mismatch")
    if command_type == "bpmn_message":
        if not command_id or expected_revision is None or plan_hash is None:
            raise WorkflowControlCommandRejectedError("bpmn_message_command_binding_required")
        if not isinstance(step_id, str) or not step_id or step_id != step_id.strip():
            raise WorkflowControlCommandRejectedError("bpmn_message_target_required")


def _assert_client_command_snapshot(
    binding: WorkflowControlRunBinding,
    status: Mapping[str, Any],
    *,
    expected_revision: int | None,
    checkpoint_ref: str | None,
) -> None:
    if checkpoint_ref is not None and checkpoint_ref != str(status.get("checkpoint_ref") or binding.checkpoint_id):
        raise WorkflowControlCommandRejectedError("workflow_control_checkpoint_binding_mismatch")
    if expected_revision is not None and expected_revision != status.get("revision", 0):
        raise WorkflowControlCommandRejectedError("stale_workflow_revision")


def _resolve_command_step_id(
    binding: WorkflowControlRunBinding,
    *,
    status: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> str:
    """Resolve public gate identities back to their owning execution node."""

    explicit = str(payload.get("step_id") or "").strip()
    if explicit:
        return explicit
    current = str(status.get("current_step_id") or "").strip()
    if current:
        return current

    open_gates = status.get("open_gates")
    open_gate = str(open_gates[0] or "").strip() if isinstance(open_gates, list) and open_gates else ""
    if open_gate:
        request_step_ids = {step.step_id for step in binding.request.steps}
        if open_gate in request_step_ids:
            return open_gate
        execution_plan = binding.execution_plan
        nodes = execution_plan.get("nodes") if isinstance(execution_plan, Mapping) else None
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, Mapping):
                    continue
                if str(node.get("gate_id") or "").strip() == open_gate:
                    node_id = str(node.get("node_id") or "").strip()
                    if node_id:
                        return node_id

    return binding.request.steps[0].step_id


def _canonical_public_status(
    binding: WorkflowControlRunBinding,
    status: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    if configured_runtime_id(binding.runtime_id) == "temporal":
        return dict(status)
    return canonical_workflow_public_status(binding, status, previous=previous)


def _start_request_id(raw: Any, *, workflow_id: str) -> str:
    explicit = str(raw or "").strip()
    if explicit:
        return explicit
    return f"start-{uuid.uuid5(uuid.NAMESPACE_URL, f'ananta:{workflow_id}').hex}"


def _authoritative_projection_binding(
    bindings: WorkflowControlBindingStore,
    candidate: WorkflowControlRunBinding,
) -> WorkflowControlRunBinding:
    authoritative = bindings.get(candidate.workflow_id)
    if authoritative is None:
        raise RuntimeError("workflow_control_binding_not_found")
    if replace(candidate, runtime_id=authoritative.runtime_id) != authoritative or candidate.runtime_id not in {
        "pending",
        authoritative.runtime_id,
    }:
        raise ValueError("workflow_control_public_status_binding_mismatch")
    if authoritative.runtime_id == "pending":
        raise ValueError("workflow_control_public_status_runtime_unbound")
    return authoritative


def _initial_start_pending_status(
    *,
    binding: WorkflowControlRunBinding,
    runtime_id: str,
) -> dict[str, Any]:
    """Create a source-grounded Hub snapshot before Temporal dispatch.

    The snapshot asserts no Worker activity.  It only records the immutable
    binding already owned by the Hub and keeps every requested step pending.
    """

    return authoritative_runtime_status(
        {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": runtime_id,
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
            "plan_hash": binding.plan_hash,
            "status": "pending",
        },
        binding=binding,
        previous=None,
        runtime_id=runtime_id,
        allow_initial_ack=True,
    )


def _assert_restart_safe_start_adoption(
    existing: WorkflowControlRunBinding,
    requested: WorkflowControlRunBinding,
) -> None:
    if any(
        (
            existing.tenant_id != requested.tenant_id,
            existing.subject_id != requested.subject_id,
            existing.workflow_id != requested.workflow_id,
            existing.run_id != requested.run_id,
            existing.plan_hash != requested.plan_hash,
            existing.policy_version != requested.policy_version,
            existing.request.to_dict() != requested.request.to_dict(),
            existing.execution_plan != requested.execution_plan,
        )
    ):
        raise RuntimeError("workflow_control_binding_already_exists")
