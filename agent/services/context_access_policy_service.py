import hashlib
import json
import fnmatch
import re
from typing import List, Optional, Dict, Any
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
        # Simple secret patterns for T007
        self._secret_patterns = [
            re.compile(r"(?i)(?:api_key|password|bearer|secret|token|credential)\s*(?:[:=]|\bis\b)\s*['\"]?[\w\-]{5,}['\"]?"),
            re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
            re.compile(r"(?i)\.env|secrets?\.json|credentials?\.xml")
        ]

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

    def compute_decision_hash(self, policy_version: int, block_metadata: Dict[str, Any], destination: DestinationContext, operation: RequestedOperation) -> str:
        data = {
            "policy_version": policy_version,
            "block_hash": block_metadata.get("content_hash"),
            "destination": {
                "worker_kind": destination.worker_kind,
                "runtime_kind": destination.runtime_kind,
                "model_scope": destination.model_scope,
                "provider_id": destination.provider_id
            },
            "operation": operation
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def detect_sensitivity(self, content: str, source_ref: str) -> Sensitivity:
        # T007: Built-in detectors for secrets and credentials

        # Check source_ref (file name) first
        for pattern in self._secret_patterns[-1:]: # Only the last one which is filename based
             if pattern.search(source_ref):
                 return Sensitivity.secret

        # Check content
        for pattern in self._secret_patterns[:-1]:
            if pattern.search(content):
                return Sensitivity.secret

        return Sensitivity.unknown

    def redact_content(self, content: str, profile: str = "default") -> str:
        # T012: Context redaction
        redacted = content
        for pattern in self._secret_patterns[:-1]: # Don't use the filename pattern here
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted

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
        decision = evaluator.get_decision(block_metadata, destination)

        # T015: deterministic hash
        decision.decision_hash = self.compute_decision_hash(policy.version, block_metadata, destination, destination.requested_operation)

        return decision

    def filter_blocks(self, policy: ContextAccessPolicy, blocks: List[Dict[str, Any]], destination: DestinationContext) -> List[Dict[str, Any]]:
        # T016: CodeCompass/RAG retrieval must filter before prompt assembly
        filtered = []
        for block in blocks:
            decision = self.get_decision(policy, block, destination)
            if decision.decision != Decision.deny:
                # Apply transformations
                processed_block = block.copy()
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
