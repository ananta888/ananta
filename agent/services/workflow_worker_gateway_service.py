"""Hub-owned decisions used by delegated workflow workers.

The service deliberately exposes decisions, not orchestration.  A worker can
revalidate one signed tool invocation, reserve one retry, or advance one
already-bound side-effect operation.  It cannot create plans, owners, tasks,
or authorization envelopes.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.workflow_authorization_grant_service import (
    HubAuthorizationRevalidationPort,
    UnavailableHubAuthorizationRevalidator,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    CanonicalWorkflowEvent,
    EventStore,
    ExecutionOwnershipStore,
    ProviderBudgetError,
    ProviderBudgetLimits,
    ProviderBudgetStore,
    RuntimeAuthorizationEnvelope,
    SideEffectLedger,
    side_effect_event,
)
from agent.services.workflow_runtime.errors import WorkflowRuntimeError
from ananta_contracts.hub_task_gateway import RETRY_BUDGET_RECEIPT_SCHEMA
from ananta_contracts.workflow_operation import operation_id_for
from ananta_contracts.workflow_worker_gateway import (
    SIDE_EFFECT_GATEWAY_RECEIPT_SCHEMA,
    WORKFLOW_WORKER_COMMAND_SCHEMA,
    WORKFLOW_WORKER_COMMANDS,
    WORKFLOW_WORKER_DECISION_SCHEMA,
    WorkflowWorkerBinding,
    WorkflowWorkerContractError,
    validate_retry_category,
)


class WorkflowWorkerGatewayError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = str(reason_code or "workflow_worker_gateway_failed")
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class WorkflowToolApprovalDecision:
    """Hub decision for one exact, digest-bound Worker tool call."""

    allowed: bool
    reason_code: str
    approval_id: str = ""


class WorkflowToolApprovalPort(Protocol):
    """Small Hub-owned port; Workers never access approval persistence."""

    def authorize(
        self,
        *,
        approval_ref: str,
        tool_id: str,
        arguments: dict[str, Any],
        hub_task_id: str,
        goal_id: str | None,
    ) -> WorkflowToolApprovalDecision: ...

    def consume(self, approval_ref: str) -> bool: ...


class UnavailableWorkflowToolApprovalService:
    """Fail-closed default for compositions without approval persistence."""

    def authorize(
        self,
        *,
        approval_ref: str,
        tool_id: str,
        arguments: dict[str, Any],
        hub_task_id: str,
        goal_id: str | None,
    ) -> WorkflowToolApprovalDecision:
        del approval_ref, tool_id, arguments, hub_task_id, goal_id
        return WorkflowToolApprovalDecision(
            False,
            "workflow_tool_approval_unavailable",
        )

    def consume(self, approval_ref: str) -> bool:
        del approval_ref
        return False


class WorkflowWorkerGatewayService:
    """Validate worker commands against Hub authority, ownership and ledgers."""

    def __init__(
        self,
        *,
        authorization: AuthorizationVerifier,
        ownership: ExecutionOwnershipStore,
        ledger: SideEffectLedger,
        events: EventStore,
        provider_budgets: ProviderBudgetStore | None = None,
        authorization_revalidator: HubAuthorizationRevalidationPort | None = None,
        tool_approvals: WorkflowToolApprovalPort | None = None,
        clock=time.time,
    ) -> None:
        self._authorization = authorization
        self._ownership = ownership
        self._ledger = ledger
        self._events = events
        self._provider_budgets = provider_budgets
        self._authorization_revalidator = (
            authorization_revalidator or UnavailableHubAuthorizationRevalidator()
        )
        self._tool_approvals = (
            tool_approvals or UnavailableWorkflowToolApprovalService()
        )
        self._clock = clock

    def execute(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if str(raw.get("schema") or "") != WORKFLOW_WORKER_COMMAND_SCHEMA:
            raise WorkflowWorkerGatewayError("workflow_worker_command_invalid", status_code=400)
        command = str(raw.get("command") or "")
        if command not in WORKFLOW_WORKER_COMMANDS:
            raise WorkflowWorkerGatewayError("workflow_worker_command_unsupported", status_code=422)
        try:
            binding = WorkflowWorkerBinding.from_mapping(raw.get("binding"))
        except WorkflowWorkerContractError as exc:
            raise WorkflowWorkerGatewayError(exc.reason_code, status_code=422) from exc

        try:
            if command == "consume_retry":
                return self._consume_retry(binding, raw)
            if command == "authorize_execution":
                return self._authorize_execution(binding, raw)
            if command == "authorize_tool":
                return self._authorize_tool(binding, raw)
            if command == "provider_budget_reserve":
                return self._reserve_provider_budget(binding, raw)
            if command == "provider_budget_reconcile":
                return self._reconcile_provider_budget(binding, raw)
            if command == "native_side_effect_claim":
                return self._claim_native_side_effect(binding, raw)
            if command.startswith("native_side_effect_"):
                return self._finish_native_side_effect(binding, raw, command=command)
            if command == "side_effect_claim":
                return self._claim_side_effect(binding, raw)
            return self._finish_side_effect(binding, raw, command=command)
        except WorkflowWorkerGatewayError:
            raise
        except (KeyError, ValueError, WorkflowRuntimeError) as exc:
            reason = str(exc) or "workflow_worker_command_denied"
            status = 422 if isinstance(exc, ValueError) and not isinstance(exc, WorkflowRuntimeError) else 409
            raise WorkflowWorkerGatewayError(reason, status_code=status) from exc

    def _reserve_provider_budget(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        store = self._require_provider_budget_store()
        reservation_id = self._bounded_identifier(
            raw.get("reservation_id"),
            "provider_budget_reservation_id_invalid",
        )
        try:
            requested = ProviderBudgetLimits(
                maximum_attempts=int(str(raw.get("maximum_attempts"))),
                maximum_tokens=int(str(raw.get("maximum_tokens"))),
                maximum_cost_micros=int(str(raw.get("maximum_cost_micros"))),
            )
            reserved_tokens = int(str(raw.get("reserved_tokens")))
            reserved_cost = int(str(raw.get("reserved_cost_micros")))
            requested.assert_valid()
        except (TypeError, ValueError, ProviderBudgetError) as exc:
            raise WorkflowWorkerGatewayError(
                "provider_budget_reservation_invalid",
                status_code=422,
            ) from exc
        attempt_id, fencing_token = self._ownership_binding(binding, raw)
        envelope = self._verify_authority(
            binding,
            raw,
            requested_budget={
                "attempts": requested.maximum_attempts,
                **(
                    {"tokens": requested.maximum_tokens}
                    if requested.maximum_tokens
                    else {}
                ),
                **(
                    {"cost_micros": requested.maximum_cost_micros}
                    if requested.maximum_cost_micros
                    else {}
                ),
            },
        )
        limits = ProviderBudgetLimits(
            maximum_attempts=int(
                envelope.budgets.get("attempts", requested.maximum_attempts)
            ),
            maximum_tokens=int(envelope.budgets.get("tokens", 0)),
            maximum_cost_micros=int(envelope.budgets.get("cost_micros", 0)),
        )
        try:
            snapshot = store.reserve(
                tenant_id=binding.tenant_id,
                run_id=binding.run_id,
                policy_version=binding.policy_version,
                reservation_id=reservation_id,
                limits=limits,
                reserved_tokens=reserved_tokens,
                reserved_cost_micros=reserved_cost,
            )
        except ProviderBudgetError as exc:
            raise WorkflowWorkerGatewayError(exc.reason_code, status_code=409) from exc
        self._append_event(
            binding,
            event_type="workflow.budget.provider_reserved",
            dedupe_key=f"provider-budget-reserve:{reservation_id}",
            causation_id=reservation_id,
            payload={
                "reservation_id": reservation_id,
                "attempt_id": attempt_id,
                "fencing_token": fencing_token,
                "attempts": snapshot.attempts,
                "tokens": snapshot.tokens,
                "cost_micros": snapshot.cost_micros,
            },
        )
        return dict(snapshot.to_dict())

    def _reconcile_provider_budget(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        store = self._require_provider_budget_store()
        reservation_id = self._bounded_identifier(
            raw.get("reservation_id"),
            "provider_budget_reservation_id_invalid",
        )
        try:
            actual_total_tokens = int(str(raw.get("actual_total_tokens")))
        except (TypeError, ValueError) as exc:
            raise WorkflowWorkerGatewayError(
                "provider_budget_actual_tokens_invalid",
                status_code=422,
            ) from exc
        attempt_id, fencing_token = self._ownership_binding(binding, raw)
        self._verify_authority(binding, raw)
        try:
            snapshot = store.reconcile(
                tenant_id=binding.tenant_id,
                run_id=binding.run_id,
                policy_version=binding.policy_version,
                reservation_id=reservation_id,
                actual_total_tokens=actual_total_tokens,
            )
        except ProviderBudgetError as exc:
            raise WorkflowWorkerGatewayError(exc.reason_code, status_code=409) from exc
        self._append_event(
            binding,
            event_type="workflow.budget.provider_reconciled",
            dedupe_key=f"provider-budget-reconcile:{reservation_id}",
            causation_id=reservation_id,
            payload={
                "reservation_id": reservation_id,
                "attempt_id": attempt_id,
                "fencing_token": fencing_token,
                "tokens": snapshot.tokens,
                "cost_micros": snapshot.cost_micros,
                "reason_code": snapshot.reason_code,
            },
        )
        return dict(snapshot.to_dict())

    def _require_provider_budget_store(self) -> ProviderBudgetStore:
        if self._provider_budgets is None:
            raise WorkflowWorkerGatewayError(
                "provider_budget_store_unavailable",
                status_code=503,
            )
        return self._provider_budgets

    def _consume_retry(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        retry_id = self._bounded_identifier(raw.get("retry_id"), "workflow_retry_id_invalid")
        try:
            category = validate_retry_category(str(raw.get("retry_category") or ""))
            maximum = int(raw.get("maximum"))
        except (TypeError, ValueError, WorkflowWorkerContractError) as exc:
            reason = getattr(exc, "reason_code", "workflow_retry_budget_invalid")
            raise WorkflowWorkerGatewayError(str(reason), status_code=422) from exc
        if maximum < 0:
            raise WorkflowWorkerGatewayError("workflow_retry_budget_invalid", status_code=422)
        envelope = self._verify_authority(
            binding,
            raw,
            requested_budget={"retries": maximum},
        )
        del envelope
        snapshot = self._ownership.consume_retry(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            retry_id=retry_id,
            category=category,
            maximum=maximum,
        )
        self._append_event(
            binding,
            event_type="workflow.budget.retry_consumed",
            dedupe_key=f"retry-budget:{retry_id}",
            causation_id=retry_id,
            payload={
                "retry_id": retry_id,
                "category": category,
                "used": snapshot.used,
                "maximum": snapshot.maximum,
                "remaining": snapshot.remaining,
            },
        )
        return {
            "schema": RETRY_BUDGET_RECEIPT_SCHEMA,
            "retry_id": retry_id,
            "category": category,
            "used": snapshot.used,
            "maximum": snapshot.maximum,
            "remaining": snapshot.remaining,
        }

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

    @staticmethod
    def _tool_binding(
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> tuple[str, str, str]:
        tool_id = WorkflowWorkerGatewayService._bounded_identifier(
            raw.get("tool_id"), "workflow_tool_id_invalid"
        )
        side_effect_class = str(raw.get("side_effect_class") or "read")
        if side_effect_class not in {"read", "idempotent_write", "non_idempotent_write"}:
            raise WorkflowWorkerGatewayError("side_effect_class_invalid", status_code=422)
        expected = operation_id_for(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
            declared_operation=f"tool:{tool_id}",
        )
        operation_id = str(raw.get("operation_id") or "")
        if operation_id != expected:
            raise WorkflowWorkerGatewayError("tool_operation_id_mismatch", status_code=422)
        return tool_id, operation_id, side_effect_class

    @staticmethod
    def _attempt_id(raw: Mapping[str, Any]) -> str:
        return WorkflowWorkerGatewayService._bounded_identifier(
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
        current = self._events.list_events(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
        )
        if any(item.dedupe_key == event.dedupe_key for item in current):
            return
        self._events.append(event, expected_sequence=len(current))

    def _append_event(
        self,
        binding: WorkflowWorkerBinding,
        *,
        event_type: str,
        dedupe_key: str,
        causation_id: str,
        payload: dict[str, Any],
    ) -> None:
        current = self._events.list_events(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
        )
        if any(item.dedupe_key == dedupe_key for item in current):
            return
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
        self._events.append(event, expected_sequence=len(current))

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


__all__ = [
    "UnavailableWorkflowToolApprovalService",
    "WorkflowToolApprovalDecision",
    "WorkflowToolApprovalPort",
    "WorkflowWorkerGatewayError",
    "WorkflowWorkerGatewayService",
]
