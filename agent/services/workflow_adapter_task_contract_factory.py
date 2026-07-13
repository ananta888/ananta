"""Serialization boundary for Hub-delegated workflow-adapter tasks."""

from __future__ import annotations

from typing import Any, Protocol

from agent.services.workflow_runtime import RuntimeAuthorizationEnvelope
from ananta_contracts.provider_execution import ProviderExecutionBinding
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
        "provider_binding",
        "selected_provider_id",
        "selected_model_id",
        "allowed_policy_scopes",
    }
    safe = {
        key: value for key, value in submission.payload.items() if key not in reserved
    }
    provider_binding = submission.provider_binding
    requires_provider = provider_binding is not None
    provider_context = {
        "tenant_id": submission.tenant_id,
        "workflow_id": submission.workflow_id,
        "run_id": submission.run_id,
        "step_id": submission.step_id,
        "plan_hash": submission.plan_hash,
        "policy_version": submission.policy_version,
        "prompt_version": str(
            safe.get("prompt_version") or "workflow-adapter-prompt-v1"
        ),
        "correlation_id": submission.correlation_id,
        "external_egress_allowed": False,
        "max_attempts": max(1, submission.maximum_retries + 1),
        "max_total_tokens": submission.max_total_tokens,
        "max_cost_micros": submission.max_cost_micros,
        "require_hub_retry_budget": submission.maximum_retries > 0,
        "require_hub_provider_budget": requires_provider,
        "provider_transport_mode": "hub_bound" if requires_provider else "none",
        "provider_decision_reason": submission.provider_decision_reason,
        "combined_retry_maximum": submission.maximum_retries,
        "authorization_envelope": authorization.to_dict(),
        "attempt_id": attempt_id,
        "fencing_token": fencing_token,
    }
    if provider_binding is not None:
        provider_context.update(
            {
                "provider_binding_id": provider_binding.binding_id,
                "selected_provider_id": provider_binding.provider_id,
                "selected_model_id": provider_binding.model_id,
            }
        )
    return {
        **safe,
        "allowed_policy_scopes": [f"tool:{tool}" for tool in submission.allowed_tools],
        "provider_context": provider_context,
    }


__all__ = [
    "WorkflowAdapterSubmissionView",
    "build_workflow_adapter_task_contract",
]
