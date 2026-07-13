"""Hub-owned provider/model decisions shared by workflow runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from ananta_contracts.provider_execution import (
    ProviderExecutionBinding,
    ProviderExecutionBindingError,
)


@dataclass(frozen=True)
class WorkflowProviderRequirement:
    tenant_id: str
    workflow_id: str
    step_id: str
    task_type: str
    runtime_kind: str
    requires_provider: bool
    required_capabilities: tuple[str, ...] = ("text_generation",)


@dataclass(frozen=True)
class WorkflowProviderDecision:
    status: str
    reason_code: str
    binding: ProviderExecutionBinding | None = None


class WorkflowProviderDecisionPort(Protocol):
    """Small Hub-side seam; implementations return data, never runtimes."""

    def decide(
        self, requirement: WorkflowProviderRequirement
    ) -> WorkflowProviderDecision: ...


class HubConfiguredWorkflowProviderDecisionService:
    """Resolve the immutable choice from Hub configuration/profile data.

    The runtime kind is deliberately not part of provider selection.  Native,
    LangChain, and LangGraph therefore receive the same decision for the same
    Hub configuration instead of consulting container-local registries.
    """

    def __init__(self, config_loader: Callable[[], Mapping[str, Any]]) -> None:
        self._config_loader = config_loader

    def decide(
        self, requirement: WorkflowProviderRequirement
    ) -> WorkflowProviderDecision:
        if not requirement.requires_provider:
            return WorkflowProviderDecision(
                status="not_required",
                reason_code="provider_transport_not_required",
            )
        if (
            not requirement.tenant_id
            or not requirement.workflow_id
            or not requirement.step_id
            or not requirement.task_type
        ):
            return WorkflowProviderDecision(
                status="denied",
                reason_code="provider_requirement_binding_missing",
            )
        config = dict(self._config_loader() or {})
        workflow_runtime = config.get("workflow_runtime")
        workflow_runtime = (
            dict(workflow_runtime) if isinstance(workflow_runtime, Mapping) else {}
        )
        configured = workflow_runtime.get("provider_selection")
        configured = dict(configured) if isinstance(configured, Mapping) else {}
        llm_config = config.get("llm_config")
        llm_config = dict(llm_config) if isinstance(llm_config, Mapping) else {}
        provider_id = str(
            configured.get("provider_id")
            or configured.get("provider")
            or llm_config.get("provider")
            or config.get("default_provider")
            or ""
        ).strip().lower()
        model_id = str(
            configured.get("model_id")
            or configured.get("model")
            or llm_config.get("model")
            or config.get("default_model")
            or ""
        ).strip()
        source = (
            "hub_profile.workflow_runtime.provider_selection"
            if configured
            else "hub_config.llm_config"
            if llm_config.get("provider") or llm_config.get("model")
            else "hub_config.defaults"
        )
        try:
            binding = ProviderExecutionBinding(
                provider_id=provider_id,
                model_id=model_id,
                source=source,
                reason_code="hub_provider_policy_selected",
            )
            binding.validate()
        except ProviderExecutionBindingError as exc:
            return WorkflowProviderDecision(
                status="denied",
                reason_code=exc.reason_code,
            )
        return WorkflowProviderDecision(
            status="selected",
            reason_code="hub_provider_policy_selected",
            binding=binding,
        )


def load_current_hub_provider_config() -> Mapping[str, Any]:
    """Read request-local Hub config, falling back to typed process settings."""

    from flask import current_app, has_app_context

    from agent.config import settings

    if has_app_context():
        configured = current_app.config.get("AGENT_CONFIG")
        if isinstance(configured, Mapping):
            return configured
    return {
        "default_provider": settings.default_provider,
        "default_model": settings.default_model,
    }


def build_workflow_provider_decision_service() -> WorkflowProviderDecisionPort:
    return HubConfiguredWorkflowProviderDecisionService(load_current_hub_provider_config)


__all__ = [
    "HubConfiguredWorkflowProviderDecisionService",
    "WorkflowProviderDecision",
    "WorkflowProviderDecisionPort",
    "WorkflowProviderRequirement",
    "build_workflow_provider_decision_service",
    "load_current_hub_provider_config",
]
