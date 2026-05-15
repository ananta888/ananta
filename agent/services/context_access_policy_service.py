import hashlib
import json
import fnmatch
import re
from typing import List, Optional, Dict, Any
from agent.services.source_classification_service import get_source_classification_service
from worker.core.context_access_policy import (
    ContextAccessPolicy,
    ContextAccessRule,
    DestinationContext,
    ContextBlockAccessDecision,
    Sensitivity,
    ModelScope,
    SourceType,
    Decision,
    ReasonCode,
    RequestedOperation,
    ContextAccessPolicyEvaluator
)

class ContextAccessPolicyService:
    def __init__(self):
        self._policies: Dict[str, ContextAccessPolicy] = {}
        self._classification_service = get_source_classification_service()

    def validate_policy(self, policy: ContextAccessPolicy) -> List[str]:
        errors = []
        # T001: Policy validation rejects rules that allow cloud for sensitivity=secret
        for rule in policy.rules:
            if rule.sensitivity in [Sensitivity.secret, Sensitivity.credential]:
                if rule.cloud_allowed or ModelScope.approved_cloud in rule.allowed_model_scopes or ModelScope.public_cloud in rule.allowed_model_scopes:
                    # Unless explicitly marked as impossible/invalid or admin override with approval requirement
                    if not rule.approval_required:
                        errors.append(f"Rule {rule.id}: Sensitive data (secret/credential) cannot be allowed for cloud without approval.")

            # T001: Policy validation rejects write_allowed=true when no allowed worker/runtime/tool constraints exist
            if rule.write_allowed:
                if not (rule.allowed_worker_kinds or rule.allowed_runtime_kinds or rule.allowed_model_scopes):
                   errors.append(f"Rule {rule.id}: write_allowed=true requires at least one worker/runtime/model constraint.")

        return errors

    def merge_policies(self, policies: List[ContextAccessPolicy]) -> ContextAccessPolicy:
        # T005: Policy merge order: system_default -> project -> blueprint_role -> task
        # Sorted by precedence (lower precedence first, so higher precedence overrides)
        sorted_policies = sorted(policies, key=lambda p: p.precedence)

        if not sorted_policies:
            return None # Should have system defaults at least

        merged_rules = []
        for p in sorted_policies:
            merged_rules.extend(p.rules)

        base = sorted_policies[-1]

        return ContextAccessPolicy(
            policy_id=f"merged-{base.policy_id}",
            version=base.version,
            scope="merged",
            rules=merged_rules,
            defaults=base.defaults,
            precedence=base.precedence
        )

    def compute_decision_hash(self, policy: ContextAccessPolicy, block_metadata: Dict[str, Any], destination: DestinationContext) -> str:
        evaluator = ContextAccessPolicyEvaluator(policy)
        return evaluator.compute_decision_hash(block_metadata, destination)

    def detect_sensitivity(self, content: str, source_ref: str) -> Sensitivity:
        return self._classification_service.classify_source(source_ref, content=content)

    def redact_content(self, content: str, profile: str = "default") -> str:
        # T012: Context redaction
        return self._classification_service.redact_secrets(content)

    def summarize_content(self, content: str, profile: str = "default") -> str:
        # T012: Summary-only transformations
        # In a real implementation, this might call an LLM or a specialized summarizer.
        # For now, we provide a placeholder that indicates summarization happened.
        lines = content.splitlines()
        if len(lines) > 5:
            return f"[SUMMARY of {len(lines)} lines]: {lines[0]} ... {lines[-1]}"
        return content

    def get_decision(self, policy: ContextAccessPolicy, block_metadata: Dict[str, Any], destination: DestinationContext) -> ContextBlockAccessDecision:
        # T011: can_send_context_block decision engine
        # T013: Write access decision engine
        evaluator = ContextAccessPolicyEvaluator(policy)
        return evaluator.get_decision(block_metadata, destination)

    def get_default_policy(self, scope: str = "system_default") -> ContextAccessPolicy:
        # T029: Add default policy templates
        rules = [
            ContextAccessRule(
                id="default-deny-secrets-cloud",
                description="Secrets are never allowed for public cloud",
                sensitivity=Sensitivity.secret,
                cloud_allowed=False,
                allowed_model_scopes=[ModelScope.local_model, ModelScope.private_remote]
            ),
            ContextAccessRule(
                id="default-redact-sensitive-cloud",
                description="Security sensitive data must be redacted for cloud",
                sensitivity=Sensitivity.security_sensitive,
                redaction_required=True,
                cloud_allowed=True
            ),
            ContextAccessRule(
                id="default-allow-public",
                description="Public data is always allowed",
                sensitivity=Sensitivity.public,
                cloud_allowed=True,
                send_allowed=True
            ),
            ContextAccessRule(
                id="default-internal-redact",
                description="Internal data requires redaction for cloud",
                sensitivity=Sensitivity.project_internal,
                redaction_required=True,
                cloud_allowed=True
            )
        ]
        return ContextAccessPolicy(
            policy_id=f"default-{scope}",
            version=1,
            scope=scope,
            rules=rules,
            defaults={"send_allowed": False, "read_allowed": True, "write_allowed": False}
        )
    def filter_blocks(self, policy: ContextAccessPolicy, blocks: List[Dict[str, Any]], destination: DestinationContext) -> List[Dict[str, Any]]:
        # T016: CodeCompass/RAG retrieval must filter before prompt assembly
        filtered = []
        for block in blocks:
            decision = self.get_decision(policy, block, destination)
            if decision.decision != Decision.deny:
                # Apply transformations
                processed_block = block.copy()
                processed_block["access_decision_hash"] = decision.decision_hash
                processed_block["access_decision"] = decision

                if decision.decision == Decision.allow_redacted:
                    processed_block["content"] = self.redact_content(block.get("content", ""))
                elif decision.decision == Decision.allow_summary_only:
                    processed_block["content"] = self.summarize_content(block.get("content", ""))
                
                filtered.append(processed_block)
        return filtered

    def audit_decision(self, decision: ContextBlockAccessDecision, task_id: str):
        # T031: Add context access decisions to Audit events
        from agent.common.audit import log_audit
        log_audit(
            action="context_access_decision",
            details={
                "task_id": task_id,
                "block_id": decision.block_id,
                "decision": decision.decision.value if hasattr(decision.decision, 'value') else decision.decision,
                "reason_code": (decision.reason_code.value if hasattr(decision.reason_code, 'value') else decision.reason_code) if decision.reason_code else None,
                "reason_detail": decision.reason_detail,
                "effective_sensitivity": decision.effective_sensitivity.value if hasattr(decision.effective_sensitivity, 'value') else decision.effective_sensitivity,
                "policy_version": decision.policy_version,
                "decision_hash": decision.decision_hash
            }
        )

    def save_policy(self, policy: ContextAccessPolicy, path: str):
        # T026: Persist ContextAccessPolicy
        # T028: Export policy
        with open(path, "w") as f:
            # Simplified serialization
            data = {
                "policy_id": policy.policy_id,
                "version": policy.version,
                "scope": policy.scope,
                "precedence": policy.precedence,
                "rules": [vars(r) for r in policy.rules]
            }
            json.dump(data, f, indent=2, default=str)

    def load_policy(self, path: str) -> ContextAccessPolicy:
        # T028: Import policy
        with open(path, "r") as f:
            data = json.load(f)
            rules = [ContextAccessRule(**r) for r in data.get("rules", [])]
            return ContextAccessPolicy(
                policy_id=data["policy_id"],
                version=data["version"],
                scope=data["scope"],
                rules=rules,
                precedence=data.get("precedence", 0)
            )

    def check_required_context(self, blocks: List[Dict[str, Any]], required_class: str) -> bool:
        # T017: required_context_class and fallback behavior
        # In a real system, this would check if any block satisfies the required classification.
        for block in blocks:
            if block.get("context_class") == required_class:
                return True
        return False
