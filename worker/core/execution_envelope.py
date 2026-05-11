"""Canonical Pydantic models for ExecutionEnvelope and WorkerResult.

EW-T007: Typed runtime validation for all worker executions.
Every worker execution must present a valid ExecutionEnvelope before any action occurs.
"""
from __future__ import annotations

import hashlib
import json
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Capability vocabulary ──────────────────────────────────────────────────────

KNOWN_CAPABILITY_CLASSES: frozenset[str] = frozenset({
    "planning",
    "research",
    "research_limited",
    "code_read",
    "code_review",
    "review",
    "summarize",
    "patch_propose",
    "patch_apply",
    "shell_plan",
    "shell_execute",
    "test_run",
    "verify",
    "memory_read",
    "memory_write",
    "mcp_call",
    "provider_call",
    "subworker_spawn",
    "cron_schedule",
    "artifact_publish",
    "admin_repair",
    "deterministic_repair",
    "repair.detect",
    "repair.diagnose",
    "repair.plan",
    "repair.execute.inspect",
    "repair.execute.low_risk",
    "repair.execute.approval_gated",
    "repair.verify",
    "repair.rollback",
    "repair.outcome.persist",
    "repair.llm_escalate",
})

CONFIRM_REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "patch_apply",
    "shell_execute",
    "memory_write",
    "mcp_call",
    "subworker_spawn",
    "cron_schedule",
})


# ── Sub-models ─────────────────────────────────────────────────────────────────

class RepairStep(BaseModel):
    step_id: str
    title: str = ""
    action_class: str = "inspect_state"
    mutation_candidate: bool = False
    rollback_supported: bool = True
    verification_required: bool = True
    expected_verification: str = ""
    command_hint: str = ""
    rollback_hint: str = "no_mutation"


class RepairProcedure(BaseModel):
    procedure_id: str
    safety_class: str = "bounded"
    steps: list[RepairStep] = Field(default_factory=list)
    diagnosis: dict[str, Any] = Field(default_factory=dict)


# ── Repair result contracts (DRR-T004) ─────────────────────────────────────

class RepairStepResultStatus(str, Enum):
    success = "success"
    skipped = "skipped"
    denied = "denied"
    approval_required = "approval_required"
    failed = "failed"
    escalated = "escalated"
    verification_failed = "verification_failed"


class RepairStepResult(BaseModel):
    step_id: str
    status: RepairStepResultStatus
    reason_code: str = ""
    started_at: float | None = None
    ended_at: float | None = None
    tool_result_refs: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    verification_result: dict[str, Any] | None = None
    side_effects: dict[str, Any] = Field(default_factory=dict)
    rollback_hint_used: str = ""
    warnings: list[str] = Field(default_factory=list)


class RepairResultVerdict(str, Enum):
    success = "success"
    partial_success = "partial_success"
    denied = "denied"
    needs_approval = "needs_approval"
    failed = "failed"
    escalated = "escalated"
    verification_failed = "verification_failed"
    cancelled = "cancelled"
    timeout = "timeout"


class RepairExecutionResult(BaseModel):
    plan_id: str
    procedure_id: str
    status: RepairResultVerdict
    completed_steps: list[str] = Field(default_factory=list)
    skipped_steps: list[str] = Field(default_factory=list)
    failed_step_id: str | None = None
    approval_required_step_id: str | None = None
    final_verification: dict[str, Any] | None = None
    outcome_label: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    trace_bundle_ref: str = ""
    persisted_outcome_ref: str = ""
    step_results: list[RepairStepResult] = Field(default_factory=list)


class ApprovalRef(BaseModel):
    ref_id: str
    operation: str
    granted_at: float
    granted_by: str

    @field_validator("ref_id", "operation", "granted_by")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()


class CapabilityGrant(BaseModel):
    capabilities: list[str] = Field(default_factory=list)
    snapshot_hash: str = ""

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in KNOWN_CAPABILITY_CLASSES]
        if unknown:
            raise ValueError(f"unknown capability classes: {unknown!r}")
        return v

    @model_validator(mode="after")
    def _compute_hash(self) -> "CapabilityGrant":
        computed = _capability_hash(self.capabilities)
        if not self.snapshot_hash:
            self.snapshot_hash = computed
        return self

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


class ModelPolicy(BaseModel):
    allowed_providers: list[str] = Field(default_factory=list)
    preferred_model: str | None = None
    cloud_allowed: bool = False
    max_tokens: int | None = None

    _CLOUD_PROVIDERS: frozenset[str] = frozenset({
        "openai", "anthropic", "gemini", "groq", "openrouter", "bedrock", "azure",
    })

    def is_provider_allowed(self, provider: str) -> bool:
        p = str(provider or "").strip().lower()
        if p in self._CLOUD_PROVIDERS and not self.cloud_allowed:
            return False
        if not self.allowed_providers:
            return True
        return p in [x.lower() for x in self.allowed_providers]


class ToolPolicy(BaseModel):
    allowed_tool_ids: list[str] = Field(default_factory=list)
    approval_overrides: dict[str, str] = Field(default_factory=dict)

    def is_tool_allowed(self, tool_id: str) -> bool:
        override = self.approval_overrides.get(tool_id, "").lower()
        if override == "deny":
            return False
        if not self.allowed_tool_ids:
            return True
        return tool_id in self.allowed_tool_ids

    def requires_approval(self, tool_id: str) -> bool:
        override = self.approval_overrides.get(tool_id, "").lower()
        return override == "confirm_required"


class FilesystemScope(BaseModel):
    read_paths: list[str] = Field(default_factory=list)
    write_paths: list[str] = Field(default_factory=list)
    workspace_root: str = ""


class NetworkScope(BaseModel):
    allowed_hosts: list[str] = Field(default_factory=list)
    allow_all: bool = False


# ── ExecutionEnvelope ──────────────────────────────────────────────────────────

class ExecutionEnvelope(BaseModel):
    """Hub-signed delegation contract for one worker execution.

    Must be fully validated by PreflightGate before any action is taken.
    """
    task_id: str
    goal_id: str | None = None
    actor_ref: str
    capability_grant: CapabilityGrant
    context_envelope_ref: str
    allowed_operations: list[str] = Field(default_factory=list)
    denied_operations: list[str] = Field(default_factory=list)
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    approval_refs: list[ApprovalRef] = Field(default_factory=list)
    filesystem_scope: FilesystemScope = Field(default_factory=FilesystemScope)
    network_scope: NetworkScope = Field(default_factory=NetworkScope)
    audit_correlation_id: str
    trace_parent_id: str | None = None
    repair_procedure: RepairProcedure | None = None

    @field_validator("task_id", "actor_ref", "context_envelope_ref", "audit_correlation_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()

    def has_capability(self, cap: str) -> bool:
        return self.capability_grant.has(cap)

    def approval_for(self, operation: str) -> ApprovalRef | None:
        for ref in self.approval_refs:
            if ref.operation == operation:
                return ref
        return None

    def is_operation_denied(self, operation: str) -> bool:
        return operation in self.denied_operations

    def is_operation_allowed(self, operation: str) -> bool:
        if self.is_operation_denied(operation):
            return False
        if not self.allowed_operations:
            return True
        return operation in self.allowed_operations


# ── WorkerResult ───────────────────────────────────────────────────────────────

class WorkerResultStatus(str, Enum):
    success = "success"
    partial_success = "partial_success"
    denied = "denied"
    needs_approval = "needs_approval"
    failed = "failed"
    degraded = "degraded"
    invalid_request = "invalid_request"


class ArtifactRef(BaseModel):
    artifact_id: str
    kind: str
    provenance: str
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceEvent(BaseModel):
    ts: float = Field(default_factory=time.time)
    event_type: str
    reason_code: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TraceBundle(BaseModel):
    correlation_id: str
    capability_snapshot_hash: str
    events: list[TraceEvent] = Field(default_factory=list)

    def append(self, event_type: str, *, reason_code: str | None = None, **payload: Any) -> None:
        self.events.append(TraceEvent(
            event_type=event_type,
            reason_code=reason_code,
            payload=payload,
        ))


class DegradedState(BaseModel):
    reason: str
    capabilities_unavailable: list[str] = Field(default_factory=list)
    fallback_used: str | None = None


class FollowUpTask(BaseModel):
    title: str
    description: str
    capability_hint: str | None = None


class WorkerResult(BaseModel):
    task_id: str
    status: WorkerResultStatus
    summary: str = ""
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    trace_bundle: TraceBundle
    policy_observations: list[str] = Field(default_factory=list)
    follow_up_tasks: list[FollowUpTask] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    degraded_state: DegradedState | None = None
    no_side_effects_confirmed: bool = False

    @classmethod
    def denied(cls, task_id: str, reason_code: str, trace: TraceBundle) -> "WorkerResult":
        trace.append("preflight_denied", reason_code=reason_code)
        return cls(
            task_id=task_id,
            status=WorkerResultStatus.denied,
            summary=f"Execution denied: {reason_code}",
            trace_bundle=trace,
            policy_observations=[reason_code],
            no_side_effects_confirmed=True,
        )

    @classmethod
    def needs_approval(cls, task_id: str, operation: str, trace: TraceBundle) -> "WorkerResult":
        trace.append("approval_required", reason_code="approval_missing", operation=operation)
        return cls(
            task_id=task_id,
            status=WorkerResultStatus.needs_approval,
            summary=f"Approval required for: {operation}",
            trace_bundle=trace,
            policy_observations=["approval_missing"],
            no_side_effects_confirmed=True,
        )

    @classmethod
    def invalid(cls, task_id: str, reason: str) -> "WorkerResult":
        trace = TraceBundle(correlation_id="none", capability_snapshot_hash="")
        trace.append("invalid_request", reason_code="invalid_request", detail=reason)
        return cls(
            task_id=task_id or "unknown",
            status=WorkerResultStatus.invalid_request,
            summary=f"Invalid request: {reason}",
            trace_bundle=trace,
            policy_observations=["invalid_request"],
            no_side_effects_confirmed=True,
        )


# ── Legacy adapter ─────────────────────────────────────────────────────────────

_LEGACY_MODE_CAPABILITIES: dict[str, list[str]] = {
    "plan_only":        ["planning"],
    "patch_propose":    ["code_read", "patch_propose"],
    "patch_apply":      ["code_read", "patch_propose", "patch_apply"],
    "command_plan":     ["shell_plan"],
    "command_execute":  ["shell_plan", "shell_execute"],
    "test_run":         ["test_run"],
    "verify":           ["verify"],
}


class LegacyEnvelopeAdapter:
    """EW-T006: Wraps bare legacy mode calls into a minimal ExecutionEnvelope.

    Emits a deprecation warning. Downstream code sees only ExecutionEnvelope.
    """

    import logging as _logging
    _log = _logging.getLogger(__name__)

    def wrap(
        self,
        *,
        task_id: str,
        mode: str,
        actor_ref: str = "legacy",
        context_envelope_ref: str = "legacy",
        audit_correlation_id: str = "",
    ) -> ExecutionEnvelope:
        caps = _LEGACY_MODE_CAPABILITIES.get(mode)
        if caps is None:
            self._log.warning("LegacyEnvelopeAdapter: unknown mode %r for task %s", mode, task_id)
            caps = ["planning"]
        else:
            self._log.warning(
                "LegacyEnvelopeAdapter: deprecated mode %r for task %s — migrate to ExecutionEnvelope",
                mode, task_id,
            )
        return ExecutionEnvelope(
            task_id=task_id or "unknown",
            actor_ref=actor_ref,
            capability_grant=CapabilityGrant(capabilities=caps),
            context_envelope_ref=context_envelope_ref or "legacy",
            audit_correlation_id=audit_correlation_id or f"legacy-{task_id}",
            model_policy=ModelPolicy(cloud_allowed=False),
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _capability_hash(capabilities: list[str]) -> str:
    normalized = json.dumps(sorted(set(capabilities)), separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()


def make_trace(envelope: ExecutionEnvelope) -> TraceBundle:
    return TraceBundle(
        correlation_id=envelope.audit_correlation_id,
        capability_snapshot_hash=envelope.capability_grant.snapshot_hash,
    )
