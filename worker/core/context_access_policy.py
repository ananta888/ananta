from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


class Sensitivity(str, Enum):
    public = "public"
    project_internal = "project_internal"
    internal = "project_internal"
    customer_confidential = "customer_confidential"
    security_sensitive = "security_sensitive"
    secret = "secret"
    credential = "credential"
    regulated_data = "regulated_data"
    generated_summary = "generated_summary"
    unknown = "unknown"


class ModelScope(str, Enum):
    local_model = "local_model"
    local_tool_only = "local_tool_only"
    private_remote = "private_remote"
    approved_cloud = "approved_cloud"
    public_cloud = "public_cloud"
    none = "none"


class SourceType(str, Enum):
    codecompass_code = "codecompass_code"
    codecompass_graph = "codecompass_graph"
    rag_chunk = "rag_chunk"
    local_file = "local_file"
    secret_file = "secret_file"
    env_file = "env_file"
    memory = "memory"
    artifact = "artifact"
    log = "log"
    config = "config"
    docs = "docs"
    external_source = "external_source"
    user_prompt = "user_prompt"


class Decision(str, Enum):
    allow = "allow"
    allow_redacted = "allow_redacted"
    allow_summary_only = "allow_summary_only"
    deny = "deny"
    approval_required = "approval_required"
    unavailable = "unavailable"


class ReasonCode(str, Enum):
    secret_blocked = "secret_blocked"
    cloud_blocked = "cloud_blocked"
    external_worker_blocked = "external_worker_blocked"
    worker_not_allowed = "worker_not_allowed"
    runtime_not_allowed = "runtime_not_allowed"
    model_scope_not_allowed = "model_scope_not_allowed"
    provider_location_blocked = "provider_location_blocked"
    write_not_allowed = "write_not_allowed"
    unmatched_source_denied = "unmatched_source_denied"
    approval_required = "approval_required"
    redaction_required = "redaction_required"
    policy_error = "policy_error"


class RequestedOperation(str, Enum):
    send_to_llm = "send_to_llm"
    send_to_worker = "send_to_worker"
    tool_read = "tool_read"
    tool_write = "tool_write"
    memory_write = "memory_write"
    artifact_store = "artifact_store"
    repair_execute = "repair_execute"
    review_only = "review_only"


@dataclass
class DestinationContext:
    worker_id: str
    worker_kind: str
    runtime_target_id: str
    runtime_kind: str
    provider_id: str
    provider_location: str
    model_id: str
    model_scope: ModelScope
    cloud_effective: bool
    external_effective: bool
    local_effective: bool
    requested_operation: RequestedOperation
    tool_id: Optional[str] = None
    task_kind: Optional[str] = None
    execution_mode: Optional[str] = None


@dataclass
class ContextBlockAccessDecision:
    block_id: str
    source_ref: str
    matched_rule_ids: List[str]
    decision: Decision
    reason_code: Optional[ReasonCode] = None
    reason_detail: Optional[str] = None
    redaction_profile: Optional[str] = None
    summarization_profile: Optional[str] = None
    approval_requirement: Optional[str] = None
    effective_sensitivity: Optional[Sensitivity] = None
    allowed_destination: Optional[bool] = None
    denied_destination: Optional[bool] = None
    policy_version: Optional[int] = None
    decision_hash: Optional[str] = None


@dataclass
class ContextAccessRule:
    id: str
    description: str
    source_match: Optional[str] = None
    source_types: List[SourceType] = field(default_factory=list)
    sensitivity: Optional[Sensitivity] = None
    allowed_worker_kinds: List[str] = field(default_factory=list)
    denied_worker_kinds: List[str] = field(default_factory=list)
    allowed_runtime_kinds: List[str] = field(default_factory=list)
    denied_runtime_kinds: List[str] = field(default_factory=list)
    allowed_model_scopes: List[ModelScope] = field(default_factory=list)
    denied_model_scopes: List[ModelScope] = field(default_factory=list)
    allowed_provider_locations: List[str] = field(default_factory=list)
    denied_provider_locations: List[str] = field(default_factory=list)
    read_allowed: Optional[bool] = None
    write_allowed: Optional[bool] = None
    send_allowed: Optional[bool] = None
    cloud_allowed: Optional[bool] = None
    external_worker_allowed: Optional[bool] = None
    redaction_required: Optional[bool] = None
    summarization_allowed: Optional[bool] = None
    approval_required: Optional[bool] = None
    reason_tags: List[str] = field(default_factory=list)


@dataclass
class ContextAccessPolicy:
    policy_id: str
    version: int
    scope: str  # system_default, project, blueprint_role, task
    rules: List[ContextAccessRule] = field(default_factory=list)
    defaults: Dict[str, Any] = field(default_factory=dict)
    precedence: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    validation_state: str = "draft"


class ContextAccessPolicyEvaluator:
    """Logic-only evaluator for ContextAccessPolicy.
    Used by both Hub (Service) and Worker (Preflight).
    """

    def __init__(self, policy: ContextAccessPolicy):
        self.policy = policy

    def compute_decision_hash(self, block_metadata: Dict[str, Any], destination: DestinationContext) -> str:
        data = {
            "policy_version": self.policy.version,
            "block_hash": block_metadata.get("content_hash"),
            "destination": {
                "worker_kind": destination.worker_kind,
                "runtime_kind": destination.runtime_kind,
                "model_scope": destination.model_scope,
                "provider_id": destination.provider_id
            },
            "operation": destination.requested_operation
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def get_decision(self, block_metadata: Dict[str, Any], destination: DestinationContext) -> ContextBlockAccessDecision:
        matched_rules = [r for r in self.policy.rules if self._match_source(r, block_metadata)]
        
        decision_hash = self.compute_decision_hash(block_metadata, destination)

        decision = Decision.deny
        reason_code = ReasonCode.unmatched_source_denied
        matched_rule_ids = [r.id for r in matched_rules]
        effective_sensitivity = block_metadata.get("sensitivity", Sensitivity.unknown)

        if self._is_approval_override_allowed(block_metadata, destination):
            return ContextBlockAccessDecision(
                block_id=block_metadata.get("block_id", "unknown"),
                source_ref=block_metadata.get("source_ref", "unknown"),
                matched_rule_ids=matched_rule_ids,
                decision=Decision.allow,
                reason_detail="Approval override active",
                effective_sensitivity=effective_sensitivity,
                policy_version=self.policy.version,
                decision_hash=decision_hash
            )
        if block_metadata.get("approval_override_id"):
            return ContextBlockAccessDecision(
                block_id=block_metadata.get("block_id", "unknown"),
                source_ref=block_metadata.get("source_ref", "unknown"),
                matched_rule_ids=matched_rule_ids,
                decision=Decision.approval_required,
                reason_code=ReasonCode.approval_required,
                reason_detail="Approval override present but scope mismatch",
                effective_sensitivity=effective_sensitivity,
                policy_version=self.policy.version,
                decision_hash=decision_hash
            )

        if self.policy.defaults:
            if destination.requested_operation == RequestedOperation.send_to_llm:
                if self.policy.defaults.get("send_allowed"):
                    decision = Decision.allow
            elif destination.requested_operation == RequestedOperation.tool_read:
                if self.policy.defaults.get("read_allowed"):
                    decision = Decision.allow
            elif destination.requested_operation == RequestedOperation.tool_write:
                if self.policy.defaults.get("write_allowed"):
                    decision = Decision.allow

        for rule in matched_rules:
            rule_decision = self._evaluate_rule(rule, destination)
            if rule_decision.get("decision") == Decision.deny:
                return ContextBlockAccessDecision(
                    block_id=block_metadata.get("block_id", "unknown"),
                    source_ref=block_metadata.get("source_ref", "unknown"),
                    matched_rule_ids=matched_rule_ids,
                    decision=Decision.deny,
                    reason_code=rule_decision.get("reason_code"),
                    reason_detail=f"Denied by rule {rule.id}: {rule.description}",
                    effective_sensitivity=effective_sensitivity,
                    policy_version=self.policy.version,
                    decision_hash=decision_hash,
                )
            elif rule_decision.get("decision") == Decision.allow:
                decision = Decision.allow
                reason_code = None
                if rule.redaction_required:
                    decision = Decision.allow_redacted
                elif rule.summarization_allowed and destination.cloud_effective:
                    if effective_sensitivity not in [Sensitivity.public, Sensitivity.generated_summary]:
                        decision = Decision.allow_summary_only

        return ContextBlockAccessDecision(
            block_id=block_metadata.get("block_id", "unknown"),
            source_ref=block_metadata.get("source_ref", "unknown"),
            matched_rule_ids=matched_rule_ids,
            decision=decision,
            reason_code=reason_code,
            effective_sensitivity=effective_sensitivity,
            policy_version=self.policy.version,
            decision_hash=decision_hash
        )

    def _is_approval_override_allowed(self, block_metadata: Dict[str, Any], destination: DestinationContext) -> bool:
        approval_id = block_metadata.get("approval_override_id")
        if not approval_id:
            return False
        scope = block_metadata.get("approval_scope") or {}
        if not isinstance(scope, dict):
            return False
        approved_block_hash = str(scope.get("block_hash") or "")
        if approved_block_hash and approved_block_hash != str(block_metadata.get("content_hash") or ""):
            return False
        requested_op = str(scope.get("requested_operation") or "")
        if requested_op and requested_op != destination.requested_operation.value:
            return False
        worker_kind = str(scope.get("worker_kind") or "")
        if worker_kind and worker_kind != destination.worker_kind:
            return False
        runtime_kind = str(scope.get("runtime_kind") or "")
        if runtime_kind and runtime_kind != destination.runtime_kind:
            return False
        provider_location = str(scope.get("provider_location") or "")
        if provider_location and provider_location != destination.provider_location:
            return False
        valid_until = scope.get("valid_until")
        if valid_until is not None:
            try:
                import time
                if float(valid_until) < time.time():
                    return False
            except Exception:
                return False
        return True

    def _match_source(self, rule: ContextAccessRule, block_metadata: Dict[str, Any]) -> bool:
        if rule.source_types:
            if block_metadata.get("source_type") not in [t.value for t in rule.source_types]:
                return False

        if rule.source_match:
            import fnmatch
            source_ref = block_metadata.get("source_ref", "")
            if not fnmatch.fnmatch(source_ref, rule.source_match):
                origin_id = block_metadata.get("origin_id", "")
                if not fnmatch.fnmatch(origin_id, rule.source_match):
                    return False

        if rule.sensitivity:
            if block_metadata.get("sensitivity") != rule.sensitivity:
                return False

        return True

    def _evaluate_rule(self, rule: ContextAccessRule, destination: DestinationContext) -> Dict[str, Any]:
        if destination.worker_kind in rule.denied_worker_kinds:
            return {"decision": Decision.deny, "reason_code": ReasonCode.worker_not_allowed}
        if rule.allowed_worker_kinds and destination.worker_kind not in rule.allowed_worker_kinds:
            return {"decision": Decision.deny, "reason_code": ReasonCode.worker_not_allowed}

        if destination.runtime_kind in rule.denied_runtime_kinds:
            return {"decision": Decision.deny, "reason_code": ReasonCode.runtime_not_allowed}
        if rule.allowed_runtime_kinds and destination.runtime_kind not in rule.allowed_runtime_kinds:
            return {"decision": Decision.deny, "reason_code": ReasonCode.runtime_not_allowed}

        if destination.provider_location in rule.denied_provider_locations:
            return {"decision": Decision.deny, "reason_code": ReasonCode.provider_location_blocked}
        if rule.allowed_provider_locations and destination.provider_location not in rule.allowed_provider_locations:
            return {"decision": Decision.deny, "reason_code": ReasonCode.provider_location_blocked}

        if destination.model_scope in rule.denied_model_scopes:
            return {"decision": Decision.deny, "reason_code": ReasonCode.model_scope_not_allowed}

        if rule.allowed_model_scopes and destination.model_scope not in rule.allowed_model_scopes:
            return {"decision": Decision.deny, "reason_code": ReasonCode.model_scope_not_allowed}

        if destination.cloud_effective and rule.cloud_allowed is False:
            return {"decision": Decision.deny, "reason_code": ReasonCode.cloud_blocked}

        if destination.external_effective and rule.external_worker_allowed is False:
            return {"decision": Decision.deny, "reason_code": ReasonCode.external_worker_blocked}

        if destination.requested_operation == RequestedOperation.send_to_llm and rule.send_allowed is False:
            return {"decision": Decision.deny, "reason_code": ReasonCode.unmatched_source_denied}

        if destination.requested_operation == RequestedOperation.tool_write and rule.write_allowed is False:
            return {"decision": Decision.deny, "reason_code": ReasonCode.write_not_allowed}

        if destination.requested_operation == RequestedOperation.tool_read and rule.read_allowed is False:
            return {"decision": Decision.deny, "reason_code": ReasonCode.unmatched_source_denied}

        if rule.send_allowed or rule.read_allowed or rule.write_allowed:
            return {"decision": Decision.allow, "reason_code": None}

        return {"decision": Decision.deny, "reason_code": ReasonCode.unmatched_source_denied}


def build_destination_context(
    *,
    worker_id: str,
    worker_kind: str,
    runtime_target_id: str,
    runtime_kind: str,
    provider_id: str,
    provider_location: str,
    model_id: str,
    requested_operation: RequestedOperation,
    tool_id: Optional[str] = None,
    task_kind: Optional[str] = None,
    execution_mode: Optional[str] = None,
) -> DestinationContext:
    rk = (runtime_kind or "").lower()
    pk = (provider_location or "").lower()
    cloud_effective = ("cloud" in rk) or ("remote" in rk) or pk in {"public", "external", "cloud", "public_cloud", "approved_cloud"}
    external_effective = ("external" in rk) or ("remote" in rk) or pk in {"external", "public", "public_cloud", "approved_cloud"}
    local_effective = not cloud_effective and not external_effective
    if local_effective:
        model_scope = ModelScope.local_model
    elif pk in {"private", "private_remote"}:
        model_scope = ModelScope.private_remote
    elif pk in {"public", "cloud", "external"}:
        model_scope = ModelScope.public_cloud
    else:
        model_scope = ModelScope.none
    return DestinationContext(
        worker_id=worker_id,
        worker_kind=worker_kind,
        runtime_target_id=runtime_target_id,
        runtime_kind=runtime_kind,
        provider_id=provider_id,
        provider_location=provider_location,
        model_id=model_id,
        model_scope=model_scope,
        cloud_effective=cloud_effective,
        external_effective=external_effective,
        local_effective=local_effective,
        requested_operation=requested_operation,
        tool_id=tool_id,
        task_kind=task_kind,
        execution_mode=execution_mode,
    )
