"""Hub admission boundary for expert supply-chain and isolation controls."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ananta_contracts.parametric_knowledge import KnowledgeExpertManifest


@dataclass(frozen=True, slots=True)
class KnowledgeExpertSecurityDecision:
    admitted: bool
    reason_code: str


class KnowledgeExpertSecurityPolicy:
    def evaluate(
        self,
        manifest: KnowledgeExpertManifest,
        *,
        scope: Mapping[str, str],
        signature_verified: bool,
        poisoning_gate_passed: bool,
        backdoor_gate_passed: bool,
        extraction_risk_accepted: bool,
    ) -> KnowledgeExpertSecurityDecision:
        if not signature_verified:
            return KnowledgeExpertSecurityDecision(False, "expert_signature_unverified")
        if manifest.adapter_format != "safetensors":
            return KnowledgeExpertSecurityDecision(False, "expert_serialization_denied")
        actual_scope = (manifest.tenant_id, manifest.workspace_id, manifest.repository_id)
        requested_scope = (scope.get("tenant_id"), scope.get("workspace_id"), scope.get("repository_id"))
        if actual_scope != requested_scope:
            return KnowledgeExpertSecurityDecision(False, "expert_scope_mismatch")
        if manifest.evaluation_status != "passed":
            return KnowledgeExpertSecurityDecision(False, "expert_evaluation_denied")
        if not poisoning_gate_passed:
            return KnowledgeExpertSecurityDecision(False, "expert_poisoning_gate_failed")
        if not backdoor_gate_passed:
            return KnowledgeExpertSecurityDecision(False, "expert_backdoor_gate_failed")
        if not extraction_risk_accepted:
            return KnowledgeExpertSecurityDecision(False, "expert_extraction_policy_denied")
        return KnowledgeExpertSecurityDecision(True, "expert_security_admitted")


__all__ = ["KnowledgeExpertSecurityDecision", "KnowledgeExpertSecurityPolicy"]
