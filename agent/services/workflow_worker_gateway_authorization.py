"""Hub authority verification, tool guarding, and execution/tool authorization commands of the workflow worker gateway.

Each class is a collaborator composed by ``WorkflowWorkerGatewayService`` and
receives only the stores and ports it needs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from agent.services.workflow_authorization_grant_service import HubAuthorizationRevalidationPort
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    ExecutionOwnershipStore,
    RuntimeAuthorizationEnvelope,
)
from agent.services.workflow_worker_gateway_ports import (
    WorkflowToolApprovalDecision,
    WorkflowToolApprovalPort,
    WorkflowToolDescriptor,
    WorkflowToolDescriptorPort,
    WorkflowToolGuardPort,
    WorkflowWorkerAuthorityPort,
    WorkflowWorkerEventRecorderPort,
    WorkflowWorkerGatewayError,
)
from agent.services.workflow_worker_gateway_support import (
    bounded_identifier,
    command_attempt_id,
    optional_bounded_identifier,
)
from ananta_contracts.workflow_operation import operation_id_for
from ananta_contracts.workflow_worker_gateway import WORKFLOW_WORKER_DECISION_SCHEMA, WorkflowWorkerBinding


class WorkflowWorkerAuthorityVerifier:
    """Verify the signed envelope against Hub revalidation and the active ownership fence."""

    def __init__(
        self,
        *,
        authorization: AuthorizationVerifier,
        ownership: ExecutionOwnershipStore,
        revalidator: HubAuthorizationRevalidationPort,
        clock: Callable[[], float],
    ) -> None:
        self._authorization = authorization
        self._ownership = ownership
        self._revalidator = revalidator
        self._clock = clock

    def verify(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
        *,
        tool: str = "",
        requested_budget: dict[str, int | float] | None = None,
        writing: bool = False,
    ) -> RuntimeAuthorizationEnvelope:
        envelope = RuntimeAuthorizationEnvelope.from_mapping(binding.authorization_envelope)
        self._authorization.authorize(
            envelope,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
            plan_hash=binding.plan_hash,
            policy_version=binding.policy_version,
            tool=tool,
            requested_budget=requested_budget,
            consume_nonce=False,
            writing=writing,
            hub_revalidator=self._revalidator.revalidate,
        )
        self.ownership_binding(binding, raw)
        return envelope

    def ownership_binding(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> tuple[str, int]:
        attempt_id = command_attempt_id(raw)
        try:
            fencing_token = int(raw.get("fencing_token"))
        except (TypeError, ValueError) as exc:
            raise WorkflowWorkerGatewayError("workflow_worker_fencing_invalid", status_code=422) from exc
        ownership = self._ownership.get(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
        )
        if ownership is None:
            raise WorkflowWorkerGatewayError("execution_ownership_not_found", status_code=404)
        if ownership.workflow_id != binding.workflow_id:
            raise WorkflowWorkerGatewayError("execution_ownership_workflow_mismatch")
        if (
            ownership.status != "active"
            or ownership.attempt_id != attempt_id
            or ownership.fencing_token != fencing_token
            or ownership.lease_expires_at <= float(self._clock())
        ):
            raise WorkflowWorkerGatewayError("workflow_worker_fencing_mismatch")
        return attempt_id, fencing_token


class WorkflowToolGuard:
    """Resolve Hub tool descriptors and digest-bound approvals; fail closed on any port error."""

    def __init__(
        self,
        *,
        descriptors: WorkflowToolDescriptorPort,
        approvals: WorkflowToolApprovalPort,
    ) -> None:
        self._descriptors = descriptors
        self._approvals = approvals

    def tool_binding(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> tuple[str, str, str]:
        tool_id = bounded_identifier(raw.get("tool_id"), "workflow_tool_id_invalid")
        requested_class = str(raw.get("side_effect_class") or "read")
        if requested_class not in {
            "none",
            "read",
            "idempotent_write",
            "non_idempotent_write",
        }:
            raise WorkflowWorkerGatewayError("side_effect_class_invalid", status_code=422)
        try:
            descriptor = self._descriptors.resolve(tool_id)
        except Exception as exc:  # noqa: BLE001 - registry lookup is fail-closed
            raise WorkflowWorkerGatewayError(
                "workflow_tool_descriptor_unavailable",
                status_code=503,
            ) from exc
        if descriptor is None:
            raise WorkflowWorkerGatewayError(
                "workflow_tool_descriptor_unknown",
                status_code=422,
            )
        if (
            not isinstance(descriptor, WorkflowToolDescriptor)
            or descriptor.tool_id != tool_id
            or descriptor.side_effect_class not in {"read", "idempotent_write", "non_idempotent_write"}
        ):
            raise WorkflowWorkerGatewayError(
                "workflow_tool_descriptor_invalid",
                status_code=503,
            )
        if requested_class != descriptor.side_effect_class:
            raise WorkflowWorkerGatewayError(
                "workflow_tool_side_effect_class_mismatch",
                status_code=403,
            )
        expected = operation_id_for(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
            declared_operation=f"tool:{tool_id}",
        )
        operation_id = str(raw.get("operation_id") or "")
        if operation_id != expected:
            raise WorkflowWorkerGatewayError("tool_operation_id_mismatch", status_code=422)
        return tool_id, operation_id, descriptor.side_effect_class

    def approval_decision(
        self,
        raw: Mapping[str, Any],
        *,
        tool_id: str,
        side_effect_class: str,
    ) -> WorkflowToolApprovalDecision:
        if side_effect_class == "read":
            return WorkflowToolApprovalDecision(
                True,
                "workflow_read_tool_approval_not_required",
            )
        approval_ref = bounded_identifier(
            raw.get("approval_ref"),
            "workflow_tool_approval_required",
        )
        hub_task_id = bounded_identifier(
            raw.get("hub_task_id"),
            "workflow_tool_hub_task_binding_required",
        )
        goal_id = optional_bounded_identifier(
            raw.get("goal_id"),
            "workflow_tool_goal_binding_invalid",
        )
        arguments = raw.get("arguments")
        if not isinstance(arguments, Mapping):
            raise WorkflowWorkerGatewayError(
                "workflow_tool_approval_arguments_required",
                status_code=422,
            )
        normalized_arguments = {str(key): value for key, value in arguments.items()}
        try:
            encoded = json.dumps(
                normalized_arguments,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WorkflowWorkerGatewayError(
                "workflow_tool_approval_arguments_invalid",
                status_code=422,
            ) from exc
        if len(encoded) > 196_608:
            raise WorkflowWorkerGatewayError(
                "workflow_tool_approval_arguments_too_large",
                status_code=413,
            )
        try:
            decision = self._approvals.authorize(
                approval_ref=approval_ref,
                tool_id=tool_id,
                arguments=normalized_arguments,
                hub_task_id=hub_task_id,
                goal_id=goal_id,
            )
        except Exception:  # noqa: BLE001 - approval persistence is fail-closed
            return WorkflowToolApprovalDecision(
                False,
                "workflow_tool_approval_unavailable",
            )
        if not isinstance(decision, WorkflowToolApprovalDecision):
            return WorkflowToolApprovalDecision(
                False,
                "workflow_tool_approval_decision_invalid",
            )
        if decision.allowed and decision.approval_id != approval_ref:
            return WorkflowToolApprovalDecision(
                False,
                "workflow_tool_approval_binding_mismatch",
            )
        return decision

    def consume_approval(self, approval_id: str) -> bool:
        try:
            return self._approvals.consume(approval_id)
        except Exception:  # noqa: BLE001 - the guarded operation is already committed
            return False


class WorkflowWorkerAuthorizationCommands:
    """Answer ``authorize_execution`` and ``authorize_tool`` worker commands."""

    def __init__(
        self,
        *,
        authority: WorkflowWorkerAuthorityPort,
        tools: WorkflowToolGuardPort,
        events: WorkflowWorkerEventRecorderPort,
    ) -> None:
        self._authority = authority
        self._tools = tools
        self._events = events

    def authorize_execution(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        attempt_id, _fencing_token = self._authority.ownership_binding(binding, raw)
        envelope = self._authority.verify(binding, raw)
        adapter_kind = bounded_identifier(raw.get("adapter_kind"), "workflow_adapter_kind_invalid")
        if adapter_kind not in {"langgraph", "native"}:
            raise WorkflowWorkerGatewayError("workflow_adapter_kind_unsupported", status_code=422)
        self._events.append(
            binding,
            event_type="workflow.step.authorization_checked",
            dedupe_key=f"execution-authorization:{binding.step_id}:{attempt_id}:{adapter_kind}",
            causation_id=attempt_id,
            payload={
                "adapter_kind": adapter_kind,
                "attempt_id": attempt_id,
                "authorization_envelope_id": envelope.envelope_id,
                "decision": "allow",
            },
        )
        return {
            "schema": WORKFLOW_WORKER_DECISION_SCHEMA,
            "allowed": True,
            "reason_code": "hub_execution_authorized",
            "operation_id": "",
        }

    def authorize_tool(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        tool_id, operation_id, side_effect_class = self._tools.tool_binding(binding, raw)
        envelope = self._authority.verify(binding, raw, tool=tool_id)
        approval = self._tools.approval_decision(
            raw,
            tool_id=tool_id,
            side_effect_class=side_effect_class,
        )
        self._events.append(
            binding,
            event_type="workflow.tool.authorization_checked",
            dedupe_key=f"tool-authorization:{operation_id}:{command_attempt_id(raw)}",
            causation_id=operation_id,
            payload={
                "operation_id": operation_id,
                "tool_id": tool_id,
                "authorization_envelope_id": envelope.envelope_id,
                "approval_id": approval.approval_id,
                "decision": "allow" if approval.allowed else "deny",
                "reason_code": approval.reason_code,
            },
        )
        return {
            "schema": WORKFLOW_WORKER_DECISION_SCHEMA,
            "allowed": approval.allowed,
            "reason_code": ("hub_tool_authorized" if approval.allowed else approval.reason_code),
            "operation_id": operation_id,
            "approval_id": approval.approval_id,
        }
