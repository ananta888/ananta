"""Hub-owned decisions used by delegated workflow workers.

The service deliberately exposes decisions, not orchestration.  A worker can
revalidate one signed tool invocation, reserve one retry, or advance one
already-bound side-effect operation.  It cannot create plans, owners, tasks,
or authorization envelopes.

The service composes narrow collaborators (authority verifier, tool guard,
event recorder, and the provider-budget, authorization and side-effect command
groups from ``workflow_worker_gateway_*``).  Each is built in ``__init__`` from
exactly the stores it needs and can be replaced through keyword-only
constructor parameters.  Ports live in ``workflow_worker_gateway_ports`` and
are re-exported here.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from agent.services.native_context_bundle_service import NativeContextBundleReadPort
from agent.services.workflow_authorization_grant_service import (
    HubAuthorizationRevalidationPort,
    UnavailableHubAuthorizationRevalidator,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    EventStore,
    ExecutionOwnershipStore,
    ProviderBudgetStore,
    SideEffectLedger,
)
from agent.services.workflow_runtime.errors import WorkflowRuntimeError
from agent.services.workflow_worker_assignment_service import (
    WorkflowWorkerAssignmentStore,
)
from agent.services.workflow_worker_gateway_authorization import (
    WorkflowToolGuard,
    WorkflowWorkerAuthorityVerifier,
    WorkflowWorkerAuthorizationCommands,
)
from agent.services.workflow_worker_gateway_ports import (  # noqa: F401 - public re-export
    UnavailableWorkflowToolApprovalService,
    UnavailableWorkflowToolDescriptorService,
    WorkflowToolApprovalDecision,
    WorkflowToolApprovalPort,
    WorkflowToolDescriptor,
    WorkflowToolDescriptorPort,
    WorkflowToolGuardPort,
    WorkflowWorkerAuthorityPort,
    WorkflowWorkerEventRecorderPort,
    WorkflowWorkerGatewayError,
)
from agent.services.workflow_worker_gateway_provider_budgets import WorkflowWorkerProviderBudgetCommands
from agent.services.workflow_worker_gateway_side_effects import WorkflowWorkerSideEffectCommands
from agent.services.workflow_worker_gateway_support import (
    WorkflowWorkerEventRecorder,
    bounded_identifier,
    command_attempt_id,
)
from ananta_contracts.hub_task_gateway import RETRY_BUDGET_RECEIPT_SCHEMA
from ananta_contracts.workflow_worker_gateway import (
    WORKFLOW_WORKER_COMMAND_SCHEMA,
    WORKFLOW_WORKER_COMMANDS,
    WorkflowWorkerBinding,
    WorkflowWorkerContractError,
    validate_retry_category,
)


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
        tool_descriptors: WorkflowToolDescriptorPort | None = None,
        assignments: WorkflowWorkerAssignmentStore | None = None,
        context_bundles: NativeContextBundleReadPort | None = None,
        clock=time.time,
        authority: WorkflowWorkerAuthorityPort | None = None,
        tool_guard: WorkflowToolGuardPort | None = None,
        event_recorder: WorkflowWorkerEventRecorderPort | None = None,
    ) -> None:
        self._ownership = ownership
        self._assignments = assignments
        self._context_bundles = context_bundles
        self._clock = clock
        self._authority: WorkflowWorkerAuthorityPort = authority or WorkflowWorkerAuthorityVerifier(
            authorization=authorization,
            ownership=ownership,
            revalidator=authorization_revalidator or UnavailableHubAuthorizationRevalidator(),
            clock=clock,
        )
        tools: WorkflowToolGuardPort = tool_guard or WorkflowToolGuard(
            descriptors=tool_descriptors or UnavailableWorkflowToolDescriptorService(),
            approvals=tool_approvals or UnavailableWorkflowToolApprovalService(),
        )
        self._events: WorkflowWorkerEventRecorderPort = event_recorder or WorkflowWorkerEventRecorder(events)
        self._authorization_commands = WorkflowWorkerAuthorizationCommands(
            authority=self._authority,
            tools=tools,
            events=self._events,
        )
        self._provider_budget_commands = WorkflowWorkerProviderBudgetCommands(
            store=provider_budgets,
            authority=self._authority,
            events=self._events,
        )
        self._side_effect_commands = WorkflowWorkerSideEffectCommands(
            ledger=ledger,
            authority=self._authority,
            tools=tools,
            events=self._events,
        )

    def execute(
        self,
        raw: Mapping[str, Any],
        *,
        authenticated_worker_id: str = "",
        authenticated_worker_url: str = "",
    ) -> dict[str, Any]:
        if str(raw.get("schema") or "") != WORKFLOW_WORKER_COMMAND_SCHEMA:
            raise WorkflowWorkerGatewayError("workflow_worker_command_invalid", status_code=400)
        command = str(raw.get("command") or "")
        if command not in WORKFLOW_WORKER_COMMANDS:
            raise WorkflowWorkerGatewayError("workflow_worker_command_unsupported", status_code=422)
        if command == "native_context_read" and not (authenticated_worker_id and authenticated_worker_url):
            raise WorkflowWorkerGatewayError("native_context_registered_worker_required", status_code=403)
        try:
            binding = WorkflowWorkerBinding.from_mapping(raw.get("binding"))
        except WorkflowWorkerContractError as exc:
            raise WorkflowWorkerGatewayError(exc.reason_code, status_code=422) from exc

        self._assert_authenticated_worker_owns_lease(
            binding,
            raw,
            authenticated_worker_id=authenticated_worker_id,
            authenticated_worker_url=authenticated_worker_url,
        )

        try:
            if command == "native_context_read":
                return self._read_native_context(binding, raw, worker_id=authenticated_worker_id)
            if command == "consume_retry":
                return self._consume_retry(binding, raw)
            if command == "authorize_execution":
                return self._authorization_commands.authorize_execution(binding, raw)
            if command == "authorize_tool":
                return self._authorization_commands.authorize_tool(binding, raw)
            if command == "provider_budget_reserve":
                return self._provider_budget_commands.reserve(binding, raw)
            if command == "provider_budget_reconcile":
                return self._provider_budget_commands.reconcile(binding, raw)
            if command == "native_side_effect_claim":
                return self._side_effect_commands.claim_native(binding, raw)
            if command.startswith("native_side_effect_"):
                return self._side_effect_commands.finish_native(binding, raw, command=command)
            if command == "side_effect_claim":
                return self._side_effect_commands.claim(binding, raw)
            return self._side_effect_commands.finish(binding, raw, command=command)
        except WorkflowWorkerGatewayError:
            raise
        except (KeyError, ValueError, WorkflowRuntimeError) as exc:
            reason = str(exc) or "workflow_worker_command_denied"
            status = 422 if isinstance(exc, ValueError) and not isinstance(exc, WorkflowRuntimeError) else 409
            raise WorkflowWorkerGatewayError(reason, status_code=status) from exc

    def _read_native_context(
        self, binding: WorkflowWorkerBinding, raw: Mapping[str, Any], *, worker_id: str,
    ) -> dict[str, Any]:
        attempt_id, fencing_token = self._authority.ownership_binding(binding, raw)
        self._authority.verify(binding, raw)
        if self._context_bundles is None:
            raise WorkflowWorkerGatewayError("native_context_service_unavailable", status_code=503)
        projection = self._context_bundles.read(
            binding=binding, hub_task_id=raw.get("hub_task_id"), command_id=raw.get("command_id"),
            attempt_id=attempt_id, fencing_token=fencing_token, worker_id=worker_id,
        )
        result = projection.to_dict()
        self._events.append(
            binding, event_type="workflow.context.bundle_read",
            dedupe_key=f"context:{projection.command_digest}:{projection.content_digest}:{projection.policy_digest}",
            causation_id=attempt_id,
            payload={
                "hub_task_id": projection.hub_task_id, "bundle_id": projection.bundle_id,
                "command_digest": projection.command_digest, "content_digest": projection.content_digest,
                "policy_digest": projection.policy_digest,
            },
        )
        return result

    def _assert_authenticated_worker_owns_lease(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
        *,
        authenticated_worker_id: str,
        authenticated_worker_url: str,
    ) -> None:
        """Bind a scoped bearer to the Hub lease before any command decision."""

        worker_id = str(authenticated_worker_id or "").strip()
        worker_url = str(authenticated_worker_url or "").strip()
        if not worker_id and not worker_url:
            # Backward-compatible direct composition outside strict Worker auth.
            return
        if (
            not worker_id
            or not worker_url
            or len(worker_id) > 256
            or len(worker_url) > 2_048
            or "\x00" in worker_id
            or "\x00" in worker_url
        ):
            raise WorkflowWorkerGatewayError(
                "workflow_worker_authenticated_identity_invalid",
                status_code=403,
            )
        ownership = self._ownership.get(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
        )
        if ownership is None:
            raise WorkflowWorkerGatewayError(
                "execution_ownership_not_found",
                status_code=404,
            )
        try:
            fencing_token = int(raw.get("fencing_token"))
        except (TypeError, ValueError) as exc:
            raise WorkflowWorkerGatewayError(
                "workflow_worker_fencing_invalid",
                status_code=422,
            ) from exc
        attempt_id = command_attempt_id(raw)
        if (
            ownership.workflow_id != binding.workflow_id
            or ownership.attempt_id != attempt_id
            or ownership.fencing_token != fencing_token
            or ownership.status != "active"
            or ownership.lease_expires_at <= float(self._clock())
        ):
            raise WorkflowWorkerGatewayError(
                "workflow_worker_fencing_mismatch"
            )
        if self._assignments is None:
            raise WorkflowWorkerGatewayError(
                "workflow_worker_assignment_store_unavailable",
                status_code=503,
            )
        assignment = self._assignments.get(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
        )
        if (
            assignment is None
            or assignment.workflow_id != binding.workflow_id
            or assignment.attempt_id != ownership.attempt_id
            or assignment.fencing_token != ownership.fencing_token
            or assignment.worker_id != worker_id
            or assignment.worker_url != worker_url
            or (raw.get("command") == "native_context_read" and assignment.hub_task_id != raw.get("hub_task_id"))
        ):
            raise WorkflowWorkerGatewayError(
                "workflow_worker_authenticated_owner_mismatch",
                status_code=403,
            )

    def _consume_retry(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        retry_id = bounded_identifier(raw.get("retry_id"), "workflow_retry_id_invalid")
        try:
            category = validate_retry_category(str(raw.get("retry_category") or ""))
            maximum = int(raw.get("maximum"))
        except (TypeError, ValueError, WorkflowWorkerContractError) as exc:
            reason = getattr(exc, "reason_code", "workflow_retry_budget_invalid")
            raise WorkflowWorkerGatewayError(str(reason), status_code=422) from exc
        if maximum < 0:
            raise WorkflowWorkerGatewayError("workflow_retry_budget_invalid", status_code=422)
        envelope = self._authority.verify(
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
        self._events.append(
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


__all__ = [
    "UnavailableWorkflowToolDescriptorService",
    "UnavailableWorkflowToolApprovalService",
    "WorkflowToolApprovalDecision",
    "WorkflowToolApprovalPort",
    "WorkflowToolDescriptor",
    "WorkflowToolDescriptorPort",
    "WorkflowWorkerGatewayError",
    "WorkflowWorkerGatewayService",
]
