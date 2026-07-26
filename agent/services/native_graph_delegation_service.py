"""Hub-owned delegation of one Native graph node to the task queue."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.services.hub_provider_context_factory import HubProviderContextSpec
from agent.services.native_graph_models import (
    NativeGraphRequest,
    NativeRunState,
    native_budget_mapping,
    safe_native_reason_code,
)
from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrantPort,
)
from agent.services.workflow_provider_selection_service import (
    WorkflowProviderDecisionPort,
    WorkflowProviderRequirement,
    trusted_model_routing_from_metadata,
)
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.native_graph_contracts import (
    NativeNodeCommand,
    native_node_requires_provider,
)
from agent.services.workflow_runtime.native_graph_ports import HubTaskQueuePort
from agent.services.workflow_runtime.ownership import ExecutionOwnershipStore
from agent.services.workflow_runtime.security import HmacKeyRing, RuntimeAuthorizationEnvelope
from agent.services.workflow_runtime.side_effects import SideEffectLedger
from ananta_contracts.provider_execution import ProviderBindingAuthorization


class NativeGraphDelegationService:
    """Claim, authorize, and enqueue work without executing it in the Hub."""

    def __init__(
        self,
        *,
        queue: HubTaskQueuePort,
        ownership: ExecutionOwnershipStore,
        ledger: SideEffectLedger,
        key_ring: HmacKeyRing,
        authorization_grants: WorkflowAuthorizationGrantPort,
        provider_decisions: WorkflowProviderDecisionPort,
        clock: Callable[[], float],
    ) -> None:
        self._queue = queue
        self._ownership = ownership
        self._ledger = ledger
        self._key_ring = key_ring
        self._authorization_grants = authorization_grants
        self._provider_decisions = provider_decisions
        self._clock = clock

    def submit(
        self,
        *,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        node: ExecutionNode,
        input_data: dict[str, Any],
        fail: Callable[[ExecutionPlan, NativeGraphRequest, NativeRunState, str], None],
        emit: Callable[..., Any],
    ) -> None:
        requires_provider = native_node_requires_provider(node)
        try:
            provider_decision = self._provider_decisions.decide(
                WorkflowProviderRequirement(
                    tenant_id=plan.tenant_id,
                    workflow_id=plan.workflow_id,
                    step_id=node.node_id,
                    task_type=str(node.metadata.get("task_type") or node.node_type),
                    runtime_kind="ananta-native",
                    requires_provider=requires_provider,
                    required_capabilities=tuple(node.required_capabilities),
                    model_routing=trusted_model_routing_from_metadata(
                        node.metadata
                    ),
                )
            )
        except Exception:
            fail(plan, request, state, "native_provider_selection_unavailable")
            return
        if requires_provider and provider_decision.binding is None:
            fail(
                plan,
                request,
                state,
                f"native_provider_selection_unavailable:{provider_decision.reason_code}",
            )
            return

        budget = node.budget or plan.budget
        owner_id = f"hub-native:{request.run_id}:{node.node_id}"
        claim = self._ownership.claim(
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=request.run_id,
            step_id=node.node_id,
            owner_id=owner_id,
            lease_seconds=max(30.0, budget.timeout_seconds + 30.0),
            maximum_retries=max(0, plan.budget.max_attempts - 1),
            now=float(self._clock()),
        )
        if not claim.acquired:
            fail(plan, request, state, f"native_ownership_{claim.reason}")
            return
        ownership = claim.ownership
        state.attempts[node.node_id] = state.attempts.get(node.node_id, 0) + 1
        authorization_budgets = native_budget_mapping(budget)
        if provider_decision.maximum_provider_attempts:
            authorization_budgets["provider_attempts"] = (
                provider_decision.maximum_provider_attempts
            )
        if provider_decision.binding is not None:
            if plan.budget.max_tokens is not None:
                authorization_budgets["provider_run_tokens"] = int(
                    plan.budget.max_tokens
                )
            if plan.budget.max_cost_micros is not None:
                authorization_budgets[
                    "provider_run_cost_micros"
                ] = int(plan.budget.max_cost_micros)
        authorization = RuntimeAuthorizationEnvelope.issue(
            key_ring=self._key_ring,
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=request.run_id,
            step_id=node.node_id,
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            allowed_tools=node.allowed_tools,
            allowed_artifacts=tuple(
                sorted(set(node.input_artifacts + node.output_artifacts))
            ),
            allowed_provider_bindings=tuple(
                ProviderBindingAuthorization.from_binding(item.binding)
                for item in provider_decision.profile_bindings
            )
            or (
                (
                    ProviderBindingAuthorization.from_binding(
                        provider_decision.binding
                    ),
                )
                if provider_decision.binding is not None
                else ()
            ),
            provider_attempt_plan=provider_decision.profile_attempt_plan,
            budgets=authorization_budgets,
            ttl_seconds=max(30.0, min(3600.0, budget.timeout_seconds + 30.0)),
            now=float(self._clock()),
        )
        self._authorization_grants.grant(authorization)
        operation_id, side_effect_revision = self._authorize_side_effect(
            plan=plan,
            request=request,
            state=state,
            node=node,
            fencing_token=ownership.fencing_token,
            envelope_id=authorization.envelope_id,
            fail=fail,
        )
        if operation_id is None:
            return
        provider_context: dict[str, Any] = {}
        provider_contexts_by_profile_id: dict[str, dict[str, Any]] = {}
        if provider_decision.profile_bindings:
            total_tokens = int(budget.max_tokens or 0)
            context_spec = HubProviderContextSpec(
                tenant_id=plan.tenant_id,
                workflow_id=plan.workflow_id,
                run_id=request.run_id,
                step_id=node.node_id,
                plan_hash=plan.plan_hash,
                policy_version=plan.policy_version,
                prompt_version="native-node-prompt-v1",
                correlation_id=request.run_id,
                max_attempts=(
                    provider_decision.maximum_provider_attempts
                ),
                max_total_tokens=total_tokens,
                max_completion_tokens_per_call=(
                    min(1_024, max(1, total_tokens // 2))
                    if total_tokens > 0
                    else 0
                ),
                max_cost_micros=int(budget.max_cost_micros or 0),
                combined_retry_maximum=0,
                authorization_envelope=authorization.to_dict(),
                attempt_id=ownership.attempt_id,
                fencing_token=ownership.fencing_token,
                require_separate_provider_attempt_budget=True,
            )
            provider_context = context_spec.build(
                provider_decision.binding,
                decision_reason=provider_decision.reason_code,
                profile_id=provider_decision.primary_profile_id,
            )
            provider_contexts_by_profile_id = (
                context_spec.build_profile_contexts(
                    provider_decision.profile_bindings,
                    decision_reason=provider_decision.reason_code,
                )
            )
        command = NativeNodeCommand(
            command_id=f"ncmd:{request.run_id}:{node.node_id}:{ownership.attempt_id}",
            control_task_id=request.control_task_id,
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=request.run_id,
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            node=node,
            authorization=authorization,
            attempt_id=ownership.attempt_id,
            fencing_token=ownership.fencing_token,
            input_data=input_data,
            artifact_refs={
                key: state.artifact_refs[key]
                for key in node.input_artifacts
                if key in state.artifact_refs
            },
            operation_id=operation_id,
            side_effect_revision=side_effect_revision,
            provider_binding=provider_decision.binding,
            primary_profile_id=provider_decision.primary_profile_id,
            provider_profile_bindings=provider_decision.profile_bindings,
            provider_attempt_plan=provider_decision.profile_attempt_plan,
            provider_maximum_attempts=(
                provider_decision.maximum_provider_attempts
            ),
            provider_context=provider_context,
            provider_contexts_by_profile_id=(
                provider_contexts_by_profile_id
            ),
        )
        receipt = self._queue.submit(command)
        if (
            not receipt.accepted
            or receipt.command_id != command.command_id
            or not receipt.hub_task_id
        ):
            fail(
                plan,
                request,
                state,
                receipt.reason_code or "native_hub_task_rejected",
            )
            return
        state.running[node.node_id] = {
            "hub_task_id": receipt.hub_task_id,
            "command_id": command.command_id,
            "attempt_id": ownership.attempt_id,
            "fencing_token": ownership.fencing_token,
            "owner_id": owner_id,
            "ownership_revision": ownership.revision,
            "operation_id": operation_id,
            # Opaque reference only.  The signed authorization contract stays
            # outside checkpoint business state and is persisted in Hub grants.
            "grant_ref": authorization.envelope_id,
        }
        emit(
            state,
            plan=plan,
            request=request,
            step_id=node.node_id,
            attempt=ownership.fencing_token,
            event_type="workflow.step.delegated",
            dedupe_key=(
                f"native:{request.run_id}:{node.node_id}:"
                f"{ownership.attempt_id}:delegated"
            ),
            payload={
                "hub_task_id": receipt.hub_task_id,
                "attempt_id": ownership.attempt_id,
            },
        )

    def cancel_running(
        self,
        *,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        reason: str,
    ) -> None:
        """Fence all active delegations and revoke their persisted grants."""

        running = tuple(state.running.items())
        task_ids = tuple(
            sorted(item["hub_task_id"] for _node_id, item in running)
        )
        if task_ids:
            self._queue.cancel(
                tenant_id=plan.tenant_id,
                run_id=request.run_id,
                hub_task_ids=task_ids,
                reason=reason,
            )
        for node_id, item in running:
            grant_ref = str(item.get("grant_ref") or "")
            if not grant_ref:
                legacy_ref = str(item.get("authorization_envelope_id") or "")
                if legacy_ref and legacy_ref != "[REDACTED]":
                    grant_ref = legacy_ref
            if grant_ref:
                try:
                    self._authorization_grants.revoke(
                        grant_ref,
                        reason_code=safe_native_reason_code(reason),
                    )
                except KeyError:
                    pass
            try:
                self._ownership.fail_attempt(
                    tenant_id=plan.tenant_id,
                    run_id=request.run_id,
                    step_id=node_id,
                    attempt_id=str(item["attempt_id"]),
                    owner_id=str(item["owner_id"]),
                    fencing_token=int(item["fencing_token"]),
                    expected_revision=int(item["ownership_revision"]),
                    failure_code=safe_native_reason_code(reason),
                    dead_letter=False,
                    now=float(self._clock()),
                )
            except (KeyError, RuntimeError, ValueError):
                pass
        state.running.clear()

    def _authorize_side_effect(
        self,
        *,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        node: ExecutionNode,
        fencing_token: int,
        envelope_id: str,
        fail: Callable[[ExecutionPlan, NativeGraphRequest, NativeRunState, str], None],
    ) -> tuple[str | None, int]:
        if node.side_effect_class not in {
            "idempotent_write",
            "non_idempotent_write",
        }:
            return "", 0
        operation = str(
            node.metadata.get("operation_name")
            or node.metadata.get("declared_operation")
            or ""
        ).strip()
        record = self._ledger.plan(
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=request.run_id,
            step_id=node.node_id,
            declared_operation=operation,
            side_effect_class=node.side_effect_class,
        )
        if record.status in {"completed", "uncertain", "started"}:
            fail(
                plan,
                request,
                state,
                f"native_side_effect_recovery_required:{record.status}",
            )
            return None, 0
        if record.status in {"planned", "failed"}:
            record = self._ledger.authorize(
                record.operation_id,
                expected_revision=record.revision,
                fencing_token=fencing_token,
                authorization_envelope_id=envelope_id,
            )
        elif record.status != "authorized" or record.fencing_token != fencing_token:
            fail(
                plan,
                request,
                state,
                "native_side_effect_authorization_conflict",
            )
            return None, 0
        return record.operation_id, record.revision


__all__ = ["NativeGraphDelegationService"]
