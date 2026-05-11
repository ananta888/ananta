"""PreflightGate: validates ExecutionEnvelope before any worker action.

EW-T008 / EW-T009: Fail-closed default-deny gate.
Every worker execution must pass through PreflightGate.check() before
any model call, tool call, shell call, or side effect occurs.
Unknown task kinds, tools, and providers are denied — never silently allowed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from worker.core.execution_envelope import (
    CONFIRM_REQUIRED_CAPABILITIES,
    ExecutionEnvelope,
    RepairProcedure,
    TraceBundle,
    WorkerResult,
    WorkerResultStatus,
    make_trace,
)
from worker.core.context_access_policy import (
    ContextAccessPolicy,
    ContextAccessRule,
    ContextAccessPolicyEvaluator,
    DestinationContext,
    RequestedOperation,
    Decision,
    ModelScope,
    ReasonCode as CAPReasonCode,
)


# ── Decision vocabulary ───────────────────────────────────────────────────────

class PreflightDecision(str, Enum):
    allow = "allow"
    confirm_required = "confirm_required"   # approval ref absent → needs_approval
    blocked = "blocked"                     # hard deny; no approval can override
    invalid_request = "invalid_request"     # envelope is structurally malformed


REASON_MISSING_CAPABILITY = "missing_capability"
REASON_CONTEXT_MISSING = "context_missing"
REASON_APPROVAL_MISSING = "approval_missing"
REASON_PROVIDER_BLOCKED = "provider_blocked"
REASON_TOOL_UNAVAILABLE = "tool_unavailable"
REASON_DENIED_OPERATION = "denied_operation"
REASON_INVALID_REQUEST = "invalid_request"
REASON_TASK_KIND_UNKNOWN = "task_kind_unknown"
REASON_SNAPSHOT_MISMATCH = "snapshot_mismatch"


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class PreflightResult:
    decision: PreflightDecision
    reason_code: str
    detail: str = ""
    observations: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == PreflightDecision.allow


# ── Gate ──────────────────────────────────────────────────────────────────────

class PreflightGate:
    """Fail-closed gate that checks an ExecutionEnvelope before any action.

    Usage:
        gate = PreflightGate()
        result = gate.check(envelope)
        if not result.allowed:
            return WorkerResult.denied(envelope.task_id, result.reason_code, trace)

    All checks are fail-closed: any ambiguity is treated as denied.
    """

    def check(self, envelope: ExecutionEnvelope) -> PreflightResult:
        """Run all preflight checks. Returns the first failing check, or allow."""
        checks = [
            self._check_task_id,
            self._check_capability_grant,
            self._check_context_envelope_ref,
            self._check_confirm_required_capabilities,
            self._check_repair_procedure,
            self._check_denied_operations,
        ]
        for check in checks:
            result = check(envelope)
            if not result.allowed:
                return result
        return PreflightResult(decision=PreflightDecision.allow, reason_code="preflight_allow")

    def check_provider(self, envelope: ExecutionEnvelope, provider: str, context_blocks: list[ContextBlock] | None = None) -> PreflightResult:
        """Called before any model call. Fail-closed: unknown provider → blocked."""
        if not envelope.model_policy.is_provider_allowed(provider):
            return PreflightResult(
                decision=PreflightDecision.blocked,
                reason_code=REASON_PROVIDER_BLOCKED,
                detail=f"provider {provider!r} not allowed by model_policy",
            )

        # CAP-BE-T022: Context Access Policy check for LLM provider
        if envelope.context_access_policy and context_blocks:
            try:
                policy = self._ensure_policy(envelope.context_access_policy)
                evaluator = ContextAccessPolicyEvaluator(policy)
                
                # Reconstruct DestinationContext for this provider
                # In a real worker, these details come from the runtime/model selection
                dest = DestinationContext(
                    worker_id=envelope.actor_ref,
                    worker_kind="native",
                    runtime_target_id="cloud",
                    runtime_kind="remote",
                    provider_id=provider,
                    provider_location="external",
                    model_id="unknown",
                    model_scope=ModelScope.public_cloud if provider not in ["local", "ollama"] else ModelScope.local_model,
                    cloud_effective=provider not in ["local", "ollama"],
                    external_effective=provider not in ["local", "ollama", "private_endpoint"],
                    local_effective=provider in ["local", "ollama"],
                    requested_operation=RequestedOperation.send_to_llm
                )

                for block in context_blocks:
                    # Convert ContextBlock to dict for evaluator
                    block_metadata = {
                        "source_type": block.source_type,
                        "source_ref": block.origin_id,
                        "origin_id": block.origin_id,
                        "sensitivity": block.sensitivity,
                        "content_hash": block.content_hash
                    }
                    cap_decision = evaluator.get_decision(block_metadata, dest)
                    if cap_decision.decision == Decision.deny:
                        return PreflightResult(
                            decision=PreflightDecision.blocked,
                            reason_code=REASON_DENIED_OPERATION,
                            detail=f"Context {block.origin_id!r} cannot be sent to provider {provider!r} by CAPS: {cap_decision.reason_detail}"
                        )
            except Exception as e:
                return PreflightResult(
                    decision=PreflightDecision.invalid_request,
                    reason_code=REASON_INVALID_REQUEST,
                    detail=f"Error evaluating ContextAccessPolicy in check_provider: {e}"
                )

        return PreflightResult(decision=PreflightDecision.allow, reason_code="provider_allow")

    def check_tool(self, envelope: ExecutionEnvelope, tool_id: str, context_block: dict[str, Any] | None = None) -> PreflightResult:
        """Called before any tool call. Fail-closed: tool not in allowlist → blocked."""
        if not envelope.tool_policy.is_tool_allowed(tool_id):
            return PreflightResult(
                decision=PreflightDecision.blocked,
                reason_code=REASON_TOOL_UNAVAILABLE,
                detail=f"tool {tool_id!r} not in allowed_tool_ids",
            )

        # CAP-BE-T024: Context Access Policy check for tools
        if envelope.context_access_policy and context_block:
            try:
                policy = self._ensure_policy(envelope.context_access_policy)
                evaluator = ContextAccessPolicyEvaluator(policy)

                # Infer destination context from envelope and current execution
                dest = DestinationContext(
                    worker_id=envelope.actor_ref,
                    worker_kind="native",
                    runtime_target_id="current",
                    runtime_kind="local",
                    provider_id="local",
                    provider_location="local",
                    model_id="none",
                    model_scope=ModelScope.local_tool_only,
                    cloud_effective=False,
                    external_effective=False,
                    local_effective=True,
                    requested_operation=RequestedOperation.tool_write if "write" in tool_id or "apply" in tool_id else RequestedOperation.tool_read,
                    tool_id=tool_id
                )

                cap_decision = evaluator.get_decision(context_block, dest)
                if cap_decision.decision == Decision.deny:
                    return PreflightResult(
                        decision=PreflightDecision.blocked,
                        reason_code=REASON_DENIED_OPERATION,
                        detail=f"Tool {tool_id!r} access to context denied by CAP: {cap_decision.reason_detail}"
                    )
            except Exception as e:
                return PreflightResult(
                    decision=PreflightDecision.invalid_request,
                    reason_code=REASON_INVALID_REQUEST,
                    detail=f"Error evaluating ContextAccessPolicy in check_tool: {e}"
                )

        if envelope.tool_policy.requires_approval(tool_id):
            if not envelope.approval_for(tool_id):
                return PreflightResult(
                    decision=PreflightDecision.confirm_required,
                    reason_code=REASON_APPROVAL_MISSING,
                    detail=f"tool {tool_id!r} requires approval override",
                )
        return PreflightResult(decision=PreflightDecision.allow, reason_code="tool_allow")

    def check_operation(self, envelope: ExecutionEnvelope, operation: str) -> PreflightResult:
        """Called before any named operation. Denied operations always blocked."""
        if not envelope.is_operation_allowed(operation):
            return PreflightResult(
                decision=PreflightDecision.blocked,
                reason_code=REASON_DENIED_OPERATION,
                detail=f"operation {operation!r} is denied or not in allowed_operations",
            )
        return PreflightResult(decision=PreflightDecision.allow, reason_code="operation_allow")

    def check_task_kind(
        self,
        envelope: ExecutionEnvelope,
        task_kind: str,
        known_task_kinds: frozenset[str],
    ) -> PreflightResult:
        """Unknown task_kind is denied — never treated as low-risk fallthrough."""
        if task_kind not in known_task_kinds:
            return PreflightResult(
                decision=PreflightDecision.blocked,
                reason_code=REASON_TASK_KIND_UNKNOWN,
                detail=f"task_kind {task_kind!r} is not in the known vocabulary",
            )
        return PreflightResult(decision=PreflightDecision.allow, reason_code="task_kind_allow")

    def to_worker_result(
        self,
        envelope: ExecutionEnvelope,
        preflight_result: PreflightResult,
        trace: TraceBundle,
    ) -> WorkerResult:
        """Convert a non-allow PreflightResult into the appropriate WorkerResult."""
        if preflight_result.decision == PreflightDecision.confirm_required:
            operation = preflight_result.detail or "unknown"
            return WorkerResult.needs_approval(envelope.task_id, operation, trace)
        return WorkerResult.denied(envelope.task_id, preflight_result.reason_code, trace)

    # ── Internal checks ───────────────────────────────────────────────────────

    def _ensure_policy(self, policy_data: dict[str, Any]) -> ContextAccessPolicy:
        """Helper to reconstruct ContextAccessPolicy from dict."""
        from worker.core.context_access_policy import ContextAccessRule, ContextAccessPolicy, Sensitivity, ModelScope, SourceType
        
        rules = []
        for r in policy_data.get("rules", []):
             # Deep copy and convert strings to enums if needed
             rule_dict = dict(r)
             if "sensitivity" in rule_dict and isinstance(rule_dict["sensitivity"], str):
                  rule_dict["sensitivity"] = Sensitivity(rule_dict["sensitivity"])
             if "allowed_model_scopes" in rule_dict:
                  rule_dict["allowed_model_scopes"] = [ModelScope(s) for s in rule_dict["allowed_model_scopes"]]
             if "denied_model_scopes" in rule_dict:
                  rule_dict["denied_model_scopes"] = [ModelScope(s) for s in rule_dict["denied_model_scopes"]]
             if "source_types" in rule_dict:
                  rule_dict["source_types"] = [SourceType(t) for t in rule_dict["source_types"]]
             
             rules.append(ContextAccessRule(**rule_dict))

        return ContextAccessPolicy(
            policy_id=policy_data["policy_id"],
            version=policy_data["version"],
            scope=policy_data["scope"],
            rules=rules,
            defaults=policy_data.get("defaults", {}),
            precedence=policy_data.get("precedence", [])
        )

    def _check_task_id(self, envelope: ExecutionEnvelope) -> PreflightResult:
        if not envelope.task_id or not envelope.task_id.strip():
            return PreflightResult(
                decision=PreflightDecision.invalid_request,
                reason_code=REASON_INVALID_REQUEST,
                detail="task_id is empty",
            )
        return PreflightResult(decision=PreflightDecision.allow, reason_code="ok")

    def _check_capability_grant(self, envelope: ExecutionEnvelope) -> PreflightResult:
        if not envelope.capability_grant or not envelope.capability_grant.capabilities:
            return PreflightResult(
                decision=PreflightDecision.blocked,
                reason_code=REASON_MISSING_CAPABILITY,
                detail="capability_grant is empty or missing",
            )
        return PreflightResult(decision=PreflightDecision.allow, reason_code="ok")

    def _check_context_envelope_ref(self, envelope: ExecutionEnvelope) -> PreflightResult:
        if not envelope.context_envelope_ref or not envelope.context_envelope_ref.strip():
            return PreflightResult(
                decision=PreflightDecision.blocked,
                reason_code=REASON_CONTEXT_MISSING,
                detail="context_envelope_ref is empty",
            )
        return PreflightResult(decision=PreflightDecision.allow, reason_code="ok")

    def _check_confirm_required_capabilities(self, envelope: ExecutionEnvelope) -> PreflightResult:
        """Any CONFIRM_REQUIRED capability without a matching ApprovalRef → needs_approval."""
        for cap in envelope.capability_grant.capabilities:
            if cap in CONFIRM_REQUIRED_CAPABILITIES:
                ref = envelope.approval_for(cap)
                if ref is None:
                    return PreflightResult(
                        decision=PreflightDecision.confirm_required,
                        reason_code=REASON_APPROVAL_MISSING,
                        detail=cap,
                    )
        return PreflightResult(decision=PreflightDecision.allow, reason_code="ok")

    def _check_repair_procedure(self, envelope: ExecutionEnvelope) -> PreflightResult:
        repair_caps = {"admin_repair", "deterministic_repair"}
        has_repair_cap = repair_caps & set(envelope.capability_grant.capabilities)
        if has_repair_cap:
            if envelope.repair_procedure is None:
                return PreflightResult(
                    decision=PreflightDecision.blocked,
                    reason_code=REASON_MISSING_CAPABILITY,
                    detail="repair_procedure required when admin_repair/deterministic_repair capability granted",
                )
            if not envelope.repair_procedure.steps:
                return PreflightResult(
                    decision=PreflightDecision.blocked,
                    reason_code=REASON_INVALID_REQUEST,
                    detail="repair_procedure has no steps",
                )
        return PreflightResult(decision=PreflightDecision.allow, reason_code="ok")

    def _check_denied_operations(self, envelope: ExecutionEnvelope) -> PreflightResult:
        """Pre-check: if denied_operations list is non-empty, record as observation (not a block).
        Actual per-operation blocking happens via check_operation()."""
        return PreflightResult(decision=PreflightDecision.allow, reason_code="ok")


# ── Snapshot integrity check ──────────────────────────────────────────────────

def verify_snapshot_integrity(envelope: ExecutionEnvelope, trace: TraceBundle) -> PreflightResult:
    """EW-T010: Verify the capability snapshot in the trace matches the envelope.

    Called after execution to detect mid-execution tampering.
    """
    expected = envelope.capability_grant.snapshot_hash
    actual = trace.capability_snapshot_hash
    if expected != actual:
        return PreflightResult(
            decision=PreflightDecision.blocked,
            reason_code=REASON_SNAPSHOT_MISMATCH,
            detail=f"snapshot hash mismatch: expected {expected!r}, got {actual!r}",
        )
    return PreflightResult(decision=PreflightDecision.allow, reason_code="snapshot_ok")
