"""Execution/tool authorization commands and command-binding parsers of the workflow worker gateway."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent.services.workflow_runtime import RuntimeAuthorizationEnvelope
from agent.services.workflow_worker_gateway_ports import (
    WorkflowToolApprovalDecision,
    WorkflowToolDescriptor,
    WorkflowWorkerGatewayError,
)
from ananta_contracts.workflow_operation import operation_id_for
from ananta_contracts.workflow_worker_gateway import WORKFLOW_WORKER_DECISION_SCHEMA, WorkflowWorkerBinding


class WorkflowWorkerAuthorizationCommandsMixin:
    """Verify Hub authority, ownership bindings, and tool approvals; mixed into ``WorkflowWorkerGatewayService``."""

    def _authorize_execution(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        attempt_id, _fencing_token = self._ownership_binding(binding, raw)
        envelope = self._verify_authority(binding, raw)
        adapter_kind = self._bounded_identifier(
            raw.get("adapter_kind"), "workflow_adapter_kind_invalid"
        )
        if adapter_kind not in {"langgraph", "native"}:
            raise WorkflowWorkerGatewayError("workflow_adapter_kind_unsupported", status_code=422)
        self._append_event(
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

    def _authorize_tool(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        tool_id, operation_id, side_effect_class = self._tool_binding(binding, raw)
        envelope = self._verify_authority(binding, raw, tool=tool_id)
        approval = self._tool_approval_decision(
            raw,
            tool_id=tool_id,
            side_effect_class=side_effect_class,
        )
        self._append_event(
            binding,
            event_type="workflow.tool.authorization_checked",
            dedupe_key=f"tool-authorization:{operation_id}:{self._attempt_id(raw)}",
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
            "reason_code": (
                "hub_tool_authorized" if approval.allowed else approval.reason_code
            ),
            "operation_id": operation_id,
            "approval_id": approval.approval_id,
        }

    def _tool_approval_decision(
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
        approval_ref = self._bounded_identifier(
            raw.get("approval_ref"),
            "workflow_tool_approval_required",
        )
        hub_task_id = self._bounded_identifier(
            raw.get("hub_task_id"),
            "workflow_tool_hub_task_binding_required",
        )
        goal_id = self._optional_bounded_identifier(
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
            decision = self._tool_approvals.authorize(
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

    def _verify_authority(
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
            hub_revalidator=self._authorization_revalidator.revalidate,
        )
        self._ownership_binding(binding, raw)
        return envelope

    def _ownership_binding(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> tuple[str, int]:
        attempt_id = self._attempt_id(raw)
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

    def _tool_binding(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> tuple[str, str, str]:
        tool_id = self._bounded_identifier(
            raw.get("tool_id"), "workflow_tool_id_invalid"
        )
        requested_class = str(raw.get("side_effect_class") or "read")
        if requested_class not in {
            "none",
            "read",
            "idempotent_write",
            "non_idempotent_write",
        }:
            raise WorkflowWorkerGatewayError("side_effect_class_invalid", status_code=422)
        try:
            descriptor = self._tool_descriptors.resolve(tool_id)
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
            or descriptor.side_effect_class
            not in {"read", "idempotent_write", "non_idempotent_write"}
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

    @staticmethod
    def _attempt_id(raw: Mapping[str, Any]) -> str:
        return WorkflowWorkerAuthorizationCommandsMixin._bounded_identifier(
            raw.get("attempt_id"), "workflow_worker_attempt_id_invalid"
        )

    @staticmethod
    def _expected_revision(raw: Mapping[str, Any]) -> int:
        try:
            expected_revision = int(raw.get("expected_revision"))
        except (TypeError, ValueError) as exc:
            raise WorkflowWorkerGatewayError(
                "side_effect_revision_invalid", status_code=422
            ) from exc
        if expected_revision < 1:
            raise WorkflowWorkerGatewayError(
                "side_effect_revision_invalid", status_code=422
            )
        return expected_revision

    @staticmethod
    def _bounded_identifier(value: object, reason_code: str) -> str:
        text = str(value or "").strip()
        if not text or len(text) > 256 or "\x00" in text:
            raise WorkflowWorkerGatewayError(reason_code, status_code=422)
        return text

    @staticmethod
    def _optional_bounded_identifier(
        value: object,
        reason_code: str,
    ) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if len(text) > 256 or "\x00" in text:
            raise WorkflowWorkerGatewayError(reason_code, status_code=422)
        return text

    @staticmethod
    def _optional_bounded_text(
        value: object,
        reason_code: str,
        *,
        maximum: int,
    ) -> str:
        text = str(value or "").strip()
        if len(text) > maximum or "\x00" in text:
            raise WorkflowWorkerGatewayError(reason_code, status_code=422)
        return text
