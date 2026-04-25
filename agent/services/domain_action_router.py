from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.bridge_adapter_registry import BridgeAdapterRegistry
from agent.services.capability_registry import CapabilityRegistry
from agent.services.domain_policy_loader import DomainPolicyLoader
from agent.services.domain_policy_service import DomainPolicyDecision, DomainPolicyService
from agent.services.domain_registry import DomainRegistry


@dataclass(frozen=True)
class DomainActionRouteResult:
    state: str
    reason: str
    domain_id: str
    capability_id: str
    action_id: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "domain_id": self.domain_id,
            "capability_id": self.capability_id,
            "action_id": self.action_id,
            "details": dict(self.details),
        }


class DomainActionRouter:
    """Route domain actions through validation, policy and approval gates."""

    def __init__(
        self,
        *,
        domain_registry: DomainRegistry,
        capability_registry: CapabilityRegistry,
        policy_loader: DomainPolicyLoader,
        policy_service: DomainPolicyService,
        bridge_adapter_registry: BridgeAdapterRegistry,
    ) -> None:
        self.domain_registry = domain_registry
        self.capability_registry = capability_registry
        self.policy_loader = policy_loader
        self.policy_service = policy_service
        self.bridge_adapter_registry = bridge_adapter_registry

    def route(
        self,
        *,
        domain_id: str,
        capability_id: str,
        action_id: str,
        execution_mode: str = "plan",
        context_summary: dict[str, Any] | None = None,
        actor_metadata: dict[str, Any] | None = None,
        approval: dict[str, Any] | None = None,
    ) -> DomainActionRouteResult:
        normalized_domain = str(domain_id).strip()
        normalized_capability = str(capability_id).strip()
        normalized_action = str(action_id).strip()
        normalized_mode = str(execution_mode).strip().lower() or "plan"
        if not normalized_domain or not normalized_capability or not normalized_action:
            return DomainActionRouteResult(
                state="degraded",
                reason="invalid_route_input",
                domain_id=normalized_domain,
                capability_id=normalized_capability,
                action_id=normalized_action,
                details={},
            )

        descriptor = self.domain_registry.get_descriptor(normalized_domain)
        if not descriptor:
            return DomainActionRouteResult(
                state="denied",
                reason="unknown_domain",
                domain_id=normalized_domain,
                capability_id=normalized_capability,
                action_id=normalized_action,
                details={},
            )

        capability = self.capability_registry.capability(normalized_capability)
        if not capability or str(capability.get("domain_id") or "").strip() != normalized_domain:
            return DomainActionRouteResult(
                state="denied",
                reason="unknown_capability",
                domain_id=normalized_domain,
                capability_id=normalized_capability,
                action_id=normalized_action,
                details={},
            )

        known_domains = {str(item.get("domain_id") or "").strip() for item in self.domain_registry.list_domains()}
        known_domains = {item for item in known_domains if item}
        known_domains.add(normalized_domain)
        policy_document = self.policy_loader.load_for_domain(
            domain_id=normalized_domain,
            policy_refs=[str(item).strip() for item in list(descriptor.get("policy_packs") or []) if str(item).strip()],
            known_domains=known_domains,
        )
        decision = self.policy_service.evaluate(
            domain_id=normalized_domain,
            capability_id=normalized_capability,
            action_id=normalized_action,
            context_summary=dict(context_summary or {}),
            actor_metadata=dict(actor_metadata or {}),
            policy_document=policy_document,
        )
        if decision.decision == "deny":
            return self._result_from_policy(state="denied", reason=decision.reason, decision=decision)
        if decision.decision == "degraded":
            return self._result_from_policy(state="degraded", reason=decision.reason, decision=decision)
        if decision.decision == "approval_required":
            approval_state = self._validate_approval(
                approval=approval,
                domain_id=normalized_domain,
                capability_id=normalized_capability,
                action_id=normalized_action,
            )
            if approval_state != "approved":
                return self._result_from_policy(
                    state="approval_required",
                    reason=approval_state,
                    decision=decision,
                )

        adapter_state = self.bridge_adapter_registry.resolve(normalized_domain)
        if str(adapter_state.get("status") or "").strip().lower() != "ready":
            return self._result_from_policy(
                state="degraded",
                reason="bridge_unavailable",
                decision=decision,
                extra={"bridge_state": adapter_state},
            )

        if normalized_mode == "plan":
            return self._result_from_policy(
                state="plan",
                reason="policy_passed_route_planned",
                decision=decision,
                extra={"bridge_state": adapter_state},
            )
        if normalized_mode != "execute":
            return self._result_from_policy(
                state="degraded",
                reason="invalid_execution_mode",
                decision=decision,
                extra={"bridge_state": adapter_state},
            )
        return self._result_from_policy(
            state="execution_started",
            reason="policy_passed_execution_started",
            decision=decision,
            extra={"bridge_state": adapter_state},
        )

    @staticmethod
    def _validate_approval(
        *,
        approval: dict[str, Any] | None,
        domain_id: str,
        capability_id: str,
        action_id: str,
    ) -> str:
        if not isinstance(approval, dict):
            return "missing_approval"
        status = str(approval.get("status") or "").strip().lower()
        approval_domain = str(approval.get("domain_id") or "").strip()
        approval_capability = str(approval.get("capability_id") or "").strip()
        approval_action = str(approval.get("action_id") or "").strip()
        reference = str(
            approval.get("approval_id") or approval.get("approval_ref") or approval.get("approval_token") or ""
        ).strip()
        if status not in {"approved", "granted"}:
            return "approval_not_granted"
        if approval_domain != domain_id or approval_capability != capability_id:
            return "approval_scope_mismatch"
        if approval_action not in {"*", action_id}:
            return "approval_action_mismatch"
        if not reference:
            return "approval_reference_missing"
        return "approved"

    @staticmethod
    def _result_from_policy(
        *,
        state: str,
        reason: str,
        decision: DomainPolicyDecision,
        extra: dict[str, Any] | None = None,
    ) -> DomainActionRouteResult:
        details = {
            "policy_decision": decision.as_dict(),
            **dict(extra or {}),
        }
        return DomainActionRouteResult(
            state=state,
            reason=reason,
            domain_id=decision.domain_id,
            capability_id=decision.capability_id,
            action_id=decision.action_id,
            details=details,
        )

