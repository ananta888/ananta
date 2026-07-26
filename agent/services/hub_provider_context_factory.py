"""Hub-only factory for immutable provider invocation context payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ananta_contracts.provider_execution import (
    ProviderExecutionBinding,
    ProviderProfileExecutionBinding,
)


@dataclass(frozen=True)
class HubProviderContextSpec:
    """Execution scope and aggregate budget shared by authorized profiles."""

    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    plan_hash: str
    policy_version: str
    prompt_version: str
    correlation_id: str
    max_attempts: int
    max_total_tokens: int
    max_completion_tokens_per_call: int
    max_cost_micros: int
    combined_retry_maximum: int
    authorization_envelope: Mapping[str, Any]
    attempt_id: str
    fencing_token: int
    external_egress_allowed: bool = False
    require_separate_provider_attempt_budget: bool = False

    def build(
        self,
        binding: ProviderExecutionBinding | None,
        *,
        decision_reason: str,
        profile_id: str = "",
    ) -> dict[str, Any]:
        """Create one context without accepting provider data from a Worker."""

        requires_provider = binding is not None
        context: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "prompt_version": self.prompt_version,
            "correlation_id": self.correlation_id,
            "external_egress_allowed": self.external_egress_allowed,
            "max_attempts": self.max_attempts,
            "max_total_tokens": self.max_total_tokens,
            "max_completion_tokens_per_call": (
                self.max_completion_tokens_per_call
            ),
            "max_cost_micros": self.max_cost_micros,
            "require_hub_retry_budget": self.combined_retry_maximum > 0,
            "require_hub_provider_budget": requires_provider,
            "provider_transport_mode": (
                "hub_bound" if requires_provider else "none"
            ),
            "provider_decision_reason": decision_reason,
            "combined_retry_maximum": self.combined_retry_maximum,
            "authorization_envelope": dict(self.authorization_envelope),
            "attempt_id": self.attempt_id,
            "fencing_token": self.fencing_token,
        }
        if binding is not None:
            binding.validate()
            context.update(
                {
                    "provider_binding_id": binding.binding_id,
                    "selected_provider_id": binding.provider_id,
                    "selected_model_id": binding.model_id,
                }
            )
            if binding.endpoint_identity:
                context["provider_endpoint_identity"] = (
                    binding.endpoint_identity
                )
            if profile_id:
                context["provider_profile_id"] = str(profile_id).strip()
            if self.require_separate_provider_attempt_budget:
                context["require_hub_provider_attempt_budget"] = True
        return context

    def build_profile_contexts(
        self,
        profile_bindings: tuple[ProviderProfileExecutionBinding, ...],
        *,
        decision_reason: str,
    ) -> dict[str, dict[str, Any]]:
        """Create exact per-profile contexts from Hub-owned bindings."""

        contexts: dict[str, dict[str, Any]] = {}
        for item in profile_bindings:
            item.validate()
            if item.profile_id in contexts:
                raise ValueError("provider_profile_binding_duplicate")
            contexts[item.profile_id] = self.build(
                item.binding,
                decision_reason=decision_reason,
                profile_id=item.profile_id,
            )
        return contexts


__all__ = ["HubProviderContextSpec"]
