"""Serialization boundary for Hub-delegated workflow-adapter tasks."""

from __future__ import annotations

from typing import Any, Protocol

from agent.services.hub_provider_context_factory import HubProviderContextSpec
from agent.services.workflow_runtime import RuntimeAuthorizationEnvelope
from ananta_contracts.provider_execution import (
    ProviderExecutionBinding,
    ProviderProfileAttemptPlanEntry,
    ProviderProfileExecutionBinding,
)
from ananta_contracts.temporal_workflow import AuthorizationEnvelopeRef
from ananta_contracts.workflow_adapter_task import WorkflowAdapterTask


class WorkflowAdapterSubmissionView(Protocol):
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    plan_hash: str
    policy_version: str
    adapter_kind: str
    command: str
    task_type: str
    payload: dict[str, Any]
    allowed_tools: tuple[str, ...]
    correlation_id: str
    maximum_retries: int
    max_total_tokens: int
    max_cost_micros: int
    provider_binding: ProviderExecutionBinding | None
    provider_decision_reason: str
    primary_profile_id: str
    provider_profile_bindings: tuple[ProviderProfileExecutionBinding, ...]
    provider_attempt_plan: tuple[ProviderProfileAttemptPlanEntry, ...]
    provider_maximum_attempts: int
    model_routing: dict[str, Any]


def build_workflow_adapter_task_contract(
    submission: WorkflowAdapterSubmissionView,
    *,
    authorization: RuntimeAuthorizationEnvelope,
    attempt_id: str,
    fencing_token: int,
) -> WorkflowAdapterTask:
    contract = WorkflowAdapterTask(
        tenant_id=submission.tenant_id,
        workflow_id=submission.workflow_id,
        run_id=submission.run_id,
        step_id=submission.step_id,
        plan_hash=submission.plan_hash,
        policy_version=submission.policy_version,
        adapter_kind=submission.adapter_kind,
        command=submission.command,
        task_type=submission.task_type,
        attempt_id=attempt_id,
        fencing_token=fencing_token,
        authorization_envelope=AuthorizationEnvelopeRef.from_mapping(
            authorization.to_dict()
        ),
        payload=_bound_payload(
            submission,
            authorization=authorization,
            attempt_id=attempt_id,
            fencing_token=fencing_token,
        ),
        correlation_id=submission.correlation_id,
        provider_binding=submission.provider_binding,
        primary_profile_id=submission.primary_profile_id,
        provider_profile_bindings=submission.provider_profile_bindings,
        provider_attempt_plan=submission.provider_attempt_plan,
        provider_maximum_attempts=submission.provider_maximum_attempts,
    )
    contract.validate()
    return contract


def _bound_payload(
    submission: WorkflowAdapterSubmissionView,
    *,
    authorization: RuntimeAuthorizationEnvelope,
    attempt_id: str,
    fencing_token: int,
) -> dict[str, Any]:
    reserved = {
        "tenant_id",
        "subject_id",
        "workflow_id",
        "run_id",
        "step_id",
        "plan_hash",
        "policy_version",
        "correlation_id",
        "attempt_id",
        "fencing_token",
        "authorization_envelope",
        "provider_context",
        "provider_contexts_by_profile_id",
        "provider_profile_bindings",
        "provider_attempt_plan",
        "primary_profile_id",
        "model_routing",
        "provider_binding",
        "selected_provider_id",
        "selected_model_id",
        "allowed_policy_scopes",
    }
    safe = {
        key: value for key, value in submission.payload.items() if key not in reserved
    }
    provider_binding = submission.provider_binding
    has_profile_route = bool(submission.provider_profile_bindings)
    spec = HubProviderContextSpec(
        tenant_id=submission.tenant_id,
        workflow_id=submission.workflow_id,
        run_id=submission.run_id,
        step_id=submission.step_id,
        plan_hash=submission.plan_hash,
        policy_version=submission.policy_version,
        prompt_version=str(
            safe.get("prompt_version") or "workflow-adapter-prompt-v1"
        ),
        correlation_id=submission.correlation_id,
        max_attempts=(
            submission.provider_maximum_attempts
            if has_profile_route
            else max(1, submission.maximum_retries + 1)
        ),
        max_total_tokens=submission.max_total_tokens,
        max_completion_tokens_per_call=_completion_token_limit(
            safe,
            maximum=submission.max_total_tokens,
        ),
        max_cost_micros=submission.max_cost_micros,
        combined_retry_maximum=(
            0 if has_profile_route else submission.maximum_retries
        ),
        authorization_envelope=authorization.to_dict(),
        attempt_id=attempt_id,
        fencing_token=fencing_token,
        require_separate_provider_attempt_budget=has_profile_route,
    )
    provider_context = spec.build(
        provider_binding,
        decision_reason=submission.provider_decision_reason,
        profile_id=submission.primary_profile_id,
    )
    payload = {
        **safe,
        "allowed_policy_scopes": [f"tool:{tool}" for tool in submission.allowed_tools],
        "provider_context": provider_context,
    }
    if submission.provider_profile_bindings:
        payload["provider_contexts_by_profile_id"] = (
            spec.build_profile_contexts(
                submission.provider_profile_bindings,
                decision_reason=submission.provider_decision_reason,
            )
        )
    if submission.model_routing:
        payload["model_routing"] = dict(submission.model_routing)
    return payload


def _completion_token_limit(
    payload: dict[str, Any],
    *,
    maximum: int,
) -> int:
    if maximum < 1:
        return 0
    raw = payload.get("max_output_tokens")
    if raw is None:
        raw = payload.get("max_completion_tokens_per_call")
    if raw is None:
        return min(1_024, max(1, maximum // 2))
    if isinstance(raw, bool):
        raise ValueError("workflow_adapter_completion_budget_invalid")
    value = int(raw)
    if value < 1:
        raise ValueError("workflow_adapter_completion_budget_invalid")
    return min(value, maximum)


__all__ = [
    "WorkflowAdapterSubmissionView",
    "build_workflow_adapter_task_contract",
]
