"""Base types for LangChain/LangGraph workflow adapters (LCG-005, LCG-013)."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


WorkflowAdapterKind = Literal["langchain", "langgraph", "n8n", "webhook", "mock", "unknown"]
WorkflowAdapterStatus = Literal["ready", "degraded", "blocked", "disabled", "unavailable"]


@dataclass(frozen=True)
class WorkflowAdapterDescriptor:
    adapter_id: str
    display_name: str
    kind: WorkflowAdapterKind
    status: WorkflowAdapterStatus
    enabled: bool
    reason: str
    capabilities: list[str] = field(default_factory=list)
    version: str = "1.0"

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "status": self.status,
            "enabled": self.enabled,
            "reason": self.reason,
            "capabilities": list(self.capabilities),
            "version": self.version,
        }


@dataclass
class DryRunResult:
    """Structured dry-run output — what would happen without executing."""
    adapter_id: str
    task_id: str
    task_type: str
    plan_steps: list[dict[str, Any]] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    required_context_sources: list[str] = field(default_factory=list)
    policy_decisions: list[dict[str, Any]] = field(default_factory=list)
    approval_required: bool = False
    approval_reasons: list[str] = field(default_factory=list)
    estimated_tokens: int | None = None
    risk_level: str = "low"
    blocked: bool = False
    block_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "workflow_dry_run_result.v1",
            "adapter_id": self.adapter_id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "plan_steps": self.plan_steps,
            "required_tools": self.required_tools,
            "required_context_sources": self.required_context_sources,
            "policy_decisions": self.policy_decisions,
            "approval_required": self.approval_required,
            "approval_reasons": self.approval_reasons,
            "estimated_tokens": self.estimated_tokens,
            "risk_level": self.risk_level,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "metadata": self.metadata,
        }


@dataclass
class WorkflowArtifactResult:
    """Artifact-first result contract (LCG-013)."""
    adapter_id: str
    task_id: str
    status: str   # success | partial | failed | blocked
    summary: str
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    policy_decisions: list[dict[str, Any]] = field(default_factory=list)
    execution_trace: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    reason_code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "workflow_artifact_result.v1",
            "adapter_id": self.adapter_id,
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "artifacts": self.artifacts,
            "sources": self.sources,
            "diagnostics": self.diagnostics,
            "policy_decisions": self.policy_decisions,
            "execution_trace": self.execution_trace,
            "error": self.error,
            "reason_code": self.reason_code,
        }


class WorkflowAdapter(Protocol):
    def descriptor(self) -> WorkflowAdapterDescriptor: ...
    def dry_run(self, *, task_id: str, task_type: str, payload: dict[str, Any]) -> DryRunResult: ...
    def execute(self, *, task_id: str, task_type: str, payload: dict[str, Any]) -> WorkflowArtifactResult: ...


class WorkerError(Exception):
    """Structured error from a workflow adapter."""
    def __init__(self, reason_code: str, message: str,
                  details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "worker_error.v1",
            "reason_code": self.reason_code,
            "message": str(self),
            "details": self.details,
        }
