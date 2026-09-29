"""Side-effect claim/finish commands and gateway event emission of the workflow worker gateway."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.native_graph_event_appender import append_gateway_observation
from agent.services.workflow_runtime import CanonicalWorkflowEvent, side_effect_event
from agent.services.workflow_worker_gateway_ports import WorkflowWorkerGatewayError
from ananta_contracts.workflow_worker_gateway import SIDE_EFFECT_GATEWAY_RECEIPT_SCHEMA, WorkflowWorkerBinding


class WorkflowWorkerSideEffectCommandsMixin:
    """Claim and finish ledgered side effects and append gateway events; mixed into ``WorkflowWorkerGatewayService``."""

    def _claim_side_effect(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        tool_id, operation_id, side_effect_class = self._tool_binding(binding, raw)
        if side_effect_class == "read":
            raise WorkflowWorkerGatewayError(
                "side_effect_claim_requires_write_class",
                status_code=422,
            )
        envelope = self._verify_authority(binding, raw, tool=tool_id, writing=True)
        approval = self._tool_approval_decision(
            raw,
            tool_id=tool_id,
            side_effect_class=side_effect_class,
        )
        if not approval.allowed:
            raise WorkflowWorkerGatewayError(approval.reason_code, status_code=403)
        attempt_id, fencing_token = self._ownership_binding(binding, raw)
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
            self._append_side_effect_event(binding, record, causation_id=operation_id)
        if record.status != "authorized":
            reason = {
                "completed": "already_completed",
                "started": "already_claimed",
                "uncertain": "side_effect_reconciliation_required",
                "failed": "side_effect_reauthorization_required",
                "compensated": "side_effect_already_compensated",
            }.get(record.status, "side_effect_claim_denied")
            return self._side_effect_receipt(record, acquired=False, reason=reason)
        claim = self._ledger.claim(
            operation_id,
            expected_revision=record.revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
        )
        self._append_side_effect_event(binding, claim.record, causation_id=operation_id)
        return self._side_effect_receipt(
            claim.record,
            acquired=claim.acquired,
            reason=claim.reason,
        )

    def _claim_native_side_effect(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Claim an operation already planned and authorized by the Native Hub runtime."""

        operation_id = self._bounded_identifier(
            raw.get("operation_id"), "side_effect_operation_id_invalid"
        )
        envelope = self._verify_authority(binding, raw, writing=True)
        attempt_id, fencing_token = self._ownership_binding(binding, raw)
        self._native_side_effect_record(
            binding,
            operation_id=operation_id,
            envelope_id=envelope.envelope_id,
        )
        expected_revision = self._expected_revision(raw)
        claim = self._ledger.claim(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
        )
        self._append_side_effect_event(binding, claim.record, causation_id=operation_id)
        return self._side_effect_receipt(
            claim.record,
            acquired=claim.acquired,
            reason=claim.reason,
        )

    def _finish_native_side_effect(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
        *,
        command: str,
    ) -> dict[str, Any]:
        """Advance only the exact Hub-planned Native operation and active fence."""

        operation_id = self._bounded_identifier(
            raw.get("operation_id"), "side_effect_operation_id_invalid"
        )
        envelope = self._verify_authority(binding, raw, writing=True)
        attempt_id, fencing_token = self._ownership_binding(binding, raw)
        self._native_side_effect_record(
            binding,
            operation_id=operation_id,
            envelope_id=envelope.envelope_id,
        )
        expected_revision = self._expected_revision(raw)
        if command == "native_side_effect_complete":
            updated = self._ledger.complete(
                operation_id,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                attempt_id=attempt_id,
                result_ref=self._bounded_identifier(
                    raw.get("result_ref") or operation_id,
                    "side_effect_result_ref_invalid",
                ),
            )
        else:
            failure_code = self._bounded_identifier(
                raw.get("reason_code") or "native_node_execution_failed",
                "side_effect_failure_code_invalid",
            )
            transition = (
                self._ledger.mark_uncertain
                if command == "native_side_effect_uncertain"
                else self._ledger.fail
            )
            updated = transition(
                operation_id,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                attempt_id=attempt_id,
                failure_code=failure_code,
            )
        self._append_side_effect_event(binding, updated, causation_id=operation_id)
        return self._side_effect_receipt(updated, acquired=False, reason=updated.status)

    def _native_side_effect_record(
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
            raise WorkflowWorkerGatewayError(
                "side_effect_operation_not_found", status_code=404
            )
        if (
            record.workflow_id != binding.workflow_id
            or record.run_id != binding.run_id
            or record.step_id != binding.step_id
            or record.authorization_envelope_id != envelope_id
        ):
            raise WorkflowWorkerGatewayError("side_effect_operation_binding_mismatch")
        return record

    def _finish_side_effect(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
        *,
        command: str,
    ) -> dict[str, Any]:
        tool_id, operation_id, side_effect_class = self._tool_binding(binding, raw)
        if side_effect_class == "read":
            raise WorkflowWorkerGatewayError(
                "side_effect_finish_requires_write_class",
                status_code=422,
            )
        self._verify_authority(binding, raw, tool=tool_id, writing=True)
        approval = self._tool_approval_decision(
            raw,
            tool_id=tool_id,
            side_effect_class=side_effect_class,
        )
        if not approval.allowed:
            raise WorkflowWorkerGatewayError(approval.reason_code, status_code=403)
        attempt_id, fencing_token = self._ownership_binding(binding, raw)
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
        expected_revision = self._expected_revision(raw)

        if command == "side_effect_complete":
            updated = self._ledger.complete(
                operation_id,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                attempt_id=attempt_id,
                result_ref=self._bounded_identifier(
                    raw.get("result_ref") or operation_id,
                    "side_effect_result_ref_invalid",
                ),
            )
        else:
            failure_code = self._bounded_identifier(
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
        self._append_side_effect_event(binding, updated, causation_id=operation_id)
        approval_consumed = False
        if command == "side_effect_complete":
            try:
                approval_consumed = self._tool_approvals.consume(
                    approval.approval_id
                )
            except Exception:  # noqa: BLE001 - operation is already committed
                approval_consumed = False
            self._append_event(
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
        receipt = self._side_effect_receipt(
            updated,
            acquired=False,
            reason=updated.status,
        )
        receipt["approval_consumed"] = approval_consumed
        return receipt

    def _append_side_effect_event(
        self,
        binding: WorkflowWorkerBinding,
        record: Any,
        *,
        causation_id: str,
    ) -> None:
        event = side_effect_event(
            record,
            correlation_id=binding.correlation_id or binding.run_id,
            causation_id=causation_id,
        )
        append_gateway_observation(self._events, event)

    def _append_event(
        self,
        binding: WorkflowWorkerBinding,
        *,
        event_type: str,
        dedupe_key: str,
        causation_id: str,
        payload: dict[str, Any],
    ) -> None:
        event = CanonicalWorkflowEvent.build(
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
            event_type=event_type,
            correlation_id=binding.correlation_id or binding.run_id,
            causation_id=causation_id,
            dedupe_key=dedupe_key,
            actor="hub",
            payload=payload,
        )
        append_gateway_observation(self._events, event)

    @staticmethod
    def _side_effect_receipt(record: Any, *, acquired: bool, reason: str) -> dict[str, Any]:
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
