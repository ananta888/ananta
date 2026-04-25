from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.capability_registry import CapabilityRegistry


@dataclass(frozen=True)
class DomainPolicyDecision:
    decision: str
    reason: str
    domain_id: str
    capability_id: str
    action_id: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "domain_id": self.domain_id,
            "capability_id": self.capability_id,
            "action_id": self.action_id,
            "details": dict(self.details),
        }


class DomainPolicyService:
    """Evaluate domain action policy decisions from generic policy packs."""

    def __init__(self, *, capability_registry: CapabilityRegistry) -> None:
        self.capability_registry = capability_registry

    def evaluate(
        self,
        *,
        domain_id: str,
        capability_id: str,
        action_id: str,
        context_summary: dict[str, Any] | None,
        actor_metadata: dict[str, Any] | None,
        policy_document: dict[str, Any] | None,
    ) -> DomainPolicyDecision:
        normalized_domain = str(domain_id).strip()
        normalized_capability = str(capability_id).strip()
        normalized_action = str(action_id).strip()
        policy = dict(policy_document or {})
        if not normalized_domain or not normalized_capability or not normalized_action:
            return DomainPolicyDecision(
                decision="degraded",
                reason="invalid_policy_input",
                domain_id=normalized_domain,
                capability_id=normalized_capability,
                action_id=normalized_action,
                details={"context_summary": dict(context_summary or {}), "actor_metadata": dict(actor_metadata or {})},
            )

        capability = self.capability_registry.capability(normalized_capability)
        if not capability or str(capability.get("domain_id") or "") != normalized_domain:
            return DomainPolicyDecision(
                decision="deny",
                reason="unknown_capability",
                domain_id=normalized_domain,
                capability_id=normalized_capability,
                action_id=normalized_action,
                details={"policy_status": policy.get("status", "missing")},
            )

        status = str(policy.get("status") or "degraded").strip().lower()
        if status != "loaded":
            return DomainPolicyDecision(
                decision="degraded",
                reason="policy_pack_not_loaded",
                domain_id=normalized_domain,
                capability_id=normalized_capability,
                action_id=normalized_action,
                details={"policy_status": status, "policy_reason": policy.get("reason")},
            )

        resolved = self._resolve_rule(policy=policy, capability_id=normalized_capability, action_id=normalized_action)
        decision = str(resolved.get("decision") or "default_deny").strip().lower()
        if decision == "default_deny":
            decision = "deny"
        if decision not in {"allow", "deny", "approval_required"}:
            return DomainPolicyDecision(
                decision="degraded",
                reason="policy_decision_invalid",
                domain_id=normalized_domain,
                capability_id=normalized_capability,
                action_id=normalized_action,
                details={"raw_decision": resolved.get("decision")},
            )
        return DomainPolicyDecision(
            decision=decision,
            reason=str(resolved.get("reason") or f"policy_rule:{decision}"),
            domain_id=normalized_domain,
            capability_id=normalized_capability,
            action_id=normalized_action,
            details={
                "rule_source": resolved.get("source"),
                "context_summary": dict(context_summary or {}),
                "actor_metadata": dict(actor_metadata or {}),
            },
        )

    @staticmethod
    def _resolve_rule(*, policy: dict[str, Any], capability_id: str, action_id: str) -> dict[str, Any]:
        rules = list(policy.get("rules") or [])
        exact: dict[str, Any] | None = None
        wildcard: dict[str, Any] | None = None
        for rule in rules:
            if str(rule.get("capability_id")) != capability_id:
                continue
            if str(rule.get("action_id")) == action_id:
                exact = dict(rule)
                break
            if str(rule.get("action_id")) == "*":
                wildcard = dict(rule)
        if exact:
            exact["source"] = "exact_rule"
            return exact
        if wildcard:
            wildcard["source"] = "wildcard_rule"
            return wildcard
        return {
            "decision": policy.get("default_decision", "default_deny"),
            "reason": "default_decision_applied",
            "source": "default_decision",
        }

