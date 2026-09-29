"""Side-effect claim/finish commands of the workflow worker gateway.

``WorkflowWorkerSideEffectCommands`` is a collaborator composed by
``WorkflowWorkerGatewayService``; it owns the side-effect ledger transitions
and depends on authority, tool guard and event recorder ports only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.workflow_runtime import SideEffectLedger
from agent.services.workflow_worker_gateway_ports import (
    WorkflowToolGuardPort,
    WorkflowWorkerAuthorityPort,
    WorkflowWorkerEventRecorderPort,
    WorkflowWorkerGatewayError,
)
from agent.services.workflow_worker_gateway_support import bounded_identifier, command_expected_revision
from ananta_contracts.workflow_worker_gateway import SIDE_EFFECT_GATEWAY_RECEIPT_SCHEMA, WorkflowWorkerBinding


def side_effect_receipt(record: Any, *, acquired: bool, reason: str) -> dict[str, Any]:
    return {
        "schema": SIDE_EFFECT_GATEWAY_RECEIPT_SCHEMA,
        "acquired": bool(acquired),
        "reason": str(reason),
        "record": {
            "operation_id": record.operation_id,
            "status": record.status,
            "revision": record.revision,
            "fencing_token": record.fencing_token,
            "attempt_id": record.attempt_id,
        },
    }


class WorkflowWorkerSideEffectCommands:
    """Claim and finish ledgered side effects for tool and Native operations."""

    def __init__(
        self,
        *,
        ledger: SideEffectLedger,
        authority: WorkflowWorkerAuthorityPort,
        tools: WorkflowToolGuardPort,
        events: WorkflowWorkerEventRecorderPort,
    ) -> None:
        self._ledger = ledger
        self._authority = authority
        self._tools = tools
        self._events = events

    def claim(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        tool_id, operation_id, side_effect_class = self._tools.tool_binding(binding, raw)
        if side_effect_class == "read":
            raise WorkflowWorkerGatewayError(
                "side_effect_claim_requires_write_class",
                status_code=422,
            )
        envelope = self._authority.verify(binding, raw, tool=tool_id, writing=True)
        approval = self._tools.approval_decision(
            raw,
            tool_id=tool_id,
            side_effect_class=side_effect_class,
        )
        if not approval.allowed:
            raise WorkflowWorkerGatewayError(approval.reason_code, status_code=403)
        attempt_id, fencing_token = self._authority.ownership_binding(binding, raw)
        record = self._ledger.plan(
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
            declared_operation=f"tool:{tool_id}",
            side_effect_class=side_effect_class,
        )
        if record.operation_id != operation_id:
            raise WorkflowWorkerGatewayError("side_effect_operation_binding_mismatch")
        if record.status == "planned":
            record = self._ledger.authorize(
                operation_id,
                expected_revision=record.revision,
                fencing_token=fencing_token,
                authorization_envelope_id=envelope.envelope_id,
            )
            self._events.append_side_effect(binding, record, causation_id=operation_id)
        if record.status != "authorized":
            reason = {
                "completed": "already_completed",
                "started": "already_claimed",
                "uncertain": "side_effect_reconciliation_required",
                "failed": "side_effect_reauthorization_required",
                "compensated": "side_effect_already_compensated",
            }.get(record.status, "side_effect_claim_denied")
            return side_effect_receipt(record, acquired=False, reason=reason)
        claim = self._ledger.claim(
            operation_id,
            expected_revision=record.revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
        )
        self._events.append_side_effect(binding, claim.record, causation_id=operation_id)
        return side_effect_receipt(
            claim.record,
            acquired=claim.acquired,
            reason=claim.reason,
        )

    def claim_native(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Claim an operation already planned and authorized by the Native Hub runtime."""

        operation_id = bounded_identifier(raw.get("operation_id"), "side_effect_operation_id_invalid")
        envelope = self._authority.verify(binding, raw, writing=True)
        attempt_id, fencing_token = self._authority.ownership_binding(binding, raw)
        self._native_record(
            binding,
            operation_id=operation_id,
            envelope_id=envelope.envelope_id,
        )
        expected_revision = command_expected_revision(raw)
        claim = self._ledger.claim(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
        )
        self._events.append_side_effect(binding, claim.record, causation_id=operation_id)
        return side_effect_receipt(
            claim.record,
            acquired=claim.acquired,
            reason=claim.reason,
        )

    def finish_native(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
        *,
        command: str,
    ) -> dict[str, Any]:
        """Advance only the exact Hub-planned Native operation and active fence."""

        operation_id = bounded_identifier(raw.get("operation_id"), "side_effect_operation_id_invalid")
        envelope = self._authority.verify(binding, raw, writing=True)
        attempt_id, fencing_token = self._authority.ownership_binding(binding, raw)
        self._native_record(
            binding,
            operation_id=operation_id,
            envelope_id=envelope.envelope_id,
        )
        expected_revision = command_expected_revision(raw)
        if command == "native_side_effect_complete":
            updated = self._ledger.complete(
                operation_id,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                attempt_id=attempt_id,
                result_ref=bounded_identifier(
                    raw.get("result_ref") or operation_id,
                    "side_effect_result_ref_invalid",
                ),
            )
        else:
            failure_code = bounded_identifier(
                raw.get("reason_code") or "native_node_execution_failed",
                "side_effect_failure_code_invalid",
            )
            transition = (
                self._ledger.mark_uncertain if command == "native_side_effect_uncertain" else self._ledger.fail
            )
            updated = transition(
                operation_id,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                attempt_id=attempt_id,
                failure_code=failure_code,
            )
        self._events.append_side_effect(binding, updated, causation_id=operation_id)
        return side_effect_receipt(updated, acquired=False, reason=updated.status)

    def _native_record(
        self,
        binding: WorkflowWorkerBinding,
        *,
        operation_id: str,
        envelope_id: str,
    ) -> Any:
        record = self._ledger.get(
            tenant_id=binding.tenant_id,
            operation_id=operation_id,
        )
        if record is None:
            raise WorkflowWorkerGatewayError("side_effect_operation_not_found", status_code=404)
        if (
            record.workflow_id != binding.workflow_id
            or record.run_id != binding.run_id
            or record.step_id != binding.step_id
            or record.authorization_envelope_id != envelope_id
        ):
            raise WorkflowWorkerGatewayError("side_effect_operation_binding_mismatch")
        return record

    def finish(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
        *,
        command: str,
    ) -> dict[str, Any]:
        tool_id, operation_id, side_effect_class = self._tools.tool_binding(binding, raw)
        if side_effect_class == "read":
            raise WorkflowWorkerGatewayError(
                "side_effect_finish_requires_write_class",
                status_code=422,
            )
        self._authority.verify(binding, raw, tool=tool_id, writing=True)
        approval = self._tools.approval_decision(
            raw,
            tool_id=tool_id,
            side_effect_class=side_effect_class,
        )
        if not approval.allowed:
            raise WorkflowWorkerGatewayError(approval.reason_code, status_code=403)
        attempt_id, fencing_token = self._authority.ownership_binding(binding, raw)
        record = self._ledger.get(tenant_id=binding.tenant_id, operation_id=operation_id)
        if record is None:
            raise WorkflowWorkerGatewayError("side_effect_operation_not_found", status_code=404)
        if (
            record.workflow_id != binding.workflow_id
            or record.run_id != binding.run_id
            or record.step_id != binding.step_id
            or record.declared_operation != f"tool:{tool_id}"
        ):
            raise WorkflowWorkerGatewayError("side_effect_operation_binding_mismatch")
        expected_revision = command_expected_revision(raw)

        if command == "side_effect_complete":
            updated = self._ledger.complete(
                operation_id,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                attempt_id=attempt_id,
                result_ref=bounded_identifier(
                    raw.get("result_ref") or operation_id,
                    "side_effect_result_ref_invalid",
                ),
            )
        else:
            failure_code = bounded_identifier(
                raw.get("reason_code") or "tool_execution_failed",
                "side_effect_failure_code_invalid",
            )
            transition = self._ledger.mark_uncertain if command == "side_effect_uncertain" else self._ledger.fail
            updated = transition(
                operation_id,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                attempt_id=attempt_id,
                failure_code=failure_code,
            )
        self._events.append_side_effect(binding, updated, causation_id=operation_id)
        approval_consumed = False
        if command == "side_effect_complete":
            approval_consumed = self._tools.consume_approval(approval.approval_id)
            self._events.append(
                binding,
                event_type=(
                    "workflow.tool.approval_consumed"
                    if approval_consumed
                    else "workflow.tool.approval_consumption_pending"
                ),
                dedupe_key=f"tool-approval-consume:{operation_id}",
                causation_id=operation_id,
                payload={
                    "operation_id": operation_id,
                    "tool_id": tool_id,
                    "approval_id": approval.approval_id,
                    "consumed": approval_consumed,
                },
            )
        receipt = side_effect_receipt(
            updated,
            acquired=False,
            reason=updated.status,
        )
        receipt["approval_consumed"] = approval_consumed
        return receipt
