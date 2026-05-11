"""Worker/runtime selection contracts for deterministic repair and governed workers.

This module implements the first backend slice of
``todo.deterministic-repair-runtime-fixup.worker-runtime-selection-extension.json``:

* DRR-T046: WorkerSelectionPolicy
* DRR-T047: WorkerRuntimeTarget
* DRR-T048/DRR-T049 foundations: WorkerRuntimeSelectionDecision

The contracts are intentionally strict enough to fail closed while still being
small and dependency-free. They are Hub-side policy contracts; UI preferences or
prompt text must not be treated as authority without being validated here.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class WorkerSelectionMode(str, Enum):
    fixed = "fixed"
    automatic = "automatic"
    policy_ranked = "policy_ranked"


class WorkerKind(str, Enum):
    native_ananta_worker = "native_ananta_worker"
    opencode = "opencode"
    hermes = "hermes"
    shellgpt = "shellgpt"
    remote_worker = "remote_worker"
    custom_worker = "custom_worker"
    disabled_placeholder = "disabled_placeholder"


class WorkerRuntimeKind(str, Enum):
    local_process = "local_process"
    docker_container = "docker_container"
    docker_compose_service = "docker_compose_service"
    wsl = "wsl"
    remote_http_worker = "remote_http_worker"
    remote_ssh_worker = "remote_ssh_worker"
    ci_sandbox = "ci_sandbox"
    cloud_worker = "cloud_worker"
    custom = "custom"


class ProviderLocation(str, Enum):
    local = "local"
    private_network = "private_network"
    private_remote = "private_remote"
    approved_cloud = "approved_cloud"
    public_cloud = "public_cloud"
    unknown = "unknown"


class RuntimeHealthState(str, Enum):
    ready = "ready"
    degraded = "degraded"
    disabled = "disabled"
    unavailable = "unavailable"
    unauthorized = "unauthorized"
    misconfigured = "misconfigured"
    unknown = "unknown"


class RuntimeDataBoundary(str, Enum):
    local_only = "local_only"
    project_private = "project_private"
    private_network = "private_network"
    external = "external"
    cloud = "cloud"
    unknown = "unknown"


class SecretAccessPolicy(str, Enum):
    deny = "deny"
    local_tool_only = "local_tool_only"
    approved_only = "approved_only"


class FallbackPolicy(str, Enum):
    deny = "deny"
    same_kind_only = "same_kind_only"
    same_or_lower_risk = "same_or_lower_risk"
    any_allowed = "any_allowed"


class SelectionDecisionStatus(str, Enum):
    selected = "selected"
    denied = "denied"
    degraded = "degraded"
    no_eligible_worker = "no_eligible_worker"


_LOCAL_RUNTIME_KINDS = {
    WorkerRuntimeKind.local_process,
    WorkerRuntimeKind.docker_container,
    WorkerRuntimeKind.docker_compose_service,
    WorkerRuntimeKind.wsl,
    WorkerRuntimeKind.ci_sandbox,
}

_EXTERNAL_RUNTIME_KINDS = {
    WorkerRuntimeKind.remote_http_worker,
    WorkerRuntimeKind.remote_ssh_worker,
    WorkerRuntimeKind.cloud_worker,
    WorkerRuntimeKind.custom,
}

_CLOUD_RUNTIME_KINDS = {WorkerRuntimeKind.cloud_worker}

_EXTERNAL_WORKER_KINDS = {WorkerKind.hermes, WorkerKind.remote_worker, WorkerKind.custom_worker}

_MUTATION_CAPABILITIES = {
    "patch_apply",
    "shell_execute",
    "file_write",
    "memory_write",
    "repair.execute.low_risk",
    "repair.execute.approval_gated",
    "repair.rollback",
}

_SECRET_CAPABILITIES = {"secret_read", "secrets.read", "credential_read", "credentials.read"}


class WorkerSelectionPolicy(BaseModel):
    """Hub-validated policy for selecting a worker/backend.

    ``fixed`` means the requested worker/kind is authoritative and must fail
    closed when it cannot satisfy the request. ``automatic`` and
    ``policy_ranked`` allow the Hub to choose from allowed candidates.
    """

    mode: WorkerSelectionMode = WorkerSelectionMode.automatic
    fixed_worker_id: str | None = None
    fixed_worker_kind: WorkerKind | None = None
    allowed_worker_kinds: list[WorkerKind] = Field(default_factory=list)
    denied_worker_kinds: list[WorkerKind] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    forbidden_capabilities: list[str] = Field(default_factory=list)
    prefer_local: bool = True
    allow_cloud: bool = False
    allow_external_workers: bool = False
    require_code_context: bool = False
    risk_profile: str = "strict"
    fallback_policy: FallbackPolicy = FallbackPolicy.deny
    selection_reason_required: bool = True

    @field_validator("fixed_worker_id")
    @classmethod
    def _strip_fixed_worker_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("required_capabilities", "forbidden_capabilities")
    @classmethod
    def _normalize_capabilities(cls, v: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in v:
            cap = str(item or "").strip()
            if cap and cap not in normalized:
                normalized.append(cap)
        return normalized

    @model_validator(mode="after")
    def _validate_policy(self) -> "WorkerSelectionPolicy":
        if self.mode == WorkerSelectionMode.fixed and not (self.fixed_worker_id or self.fixed_worker_kind):
            raise ValueError("mode='fixed' requires fixed_worker_id or fixed_worker_kind")
        if self.mode == WorkerSelectionMode.automatic and self.fixed_worker_id:
            raise ValueError("mode='automatic' must not set fixed_worker_id")
        overlap = set(self.allowed_worker_kinds).intersection(self.denied_worker_kinds)
        if overlap:
            raise ValueError(f"worker kinds cannot be both allowed and denied: {sorted(x.value for x in overlap)}")
        cap_overlap = set(self.required_capabilities).intersection(self.forbidden_capabilities)
        if cap_overlap:
            raise ValueError(f"capabilities cannot be both required and forbidden: {sorted(cap_overlap)}")
        return self

    def allows_worker_kind(self, kind: WorkerKind) -> bool:
        if kind in self.denied_worker_kinds:
            return False
        if self.allowed_worker_kinds and kind not in self.allowed_worker_kinds:
            return False
        if kind in _EXTERNAL_WORKER_KINDS and not self.allow_external_workers:
            return False
        return True


class WorkerRuntimeTarget(BaseModel):
    """Concrete runtime environment where a selected worker actually runs."""

    runtime_target_id: str
    runtime_kind: WorkerRuntimeKind
    location: str = ""
    endpoint_ref: str = ""
    workspace_scope: str = ""
    os_family: str = "unknown"
    containerized: bool = False
    network_zone: str = "unknown"
    allowed_capabilities: list[str] = Field(default_factory=list)
    denied_capabilities: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    max_parallel_tasks: int = 1
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    data_boundary: RuntimeDataBoundary = RuntimeDataBoundary.unknown
    secret_access_policy: SecretAccessPolicy = SecretAccessPolicy.deny
    health_state: RuntimeHealthState = RuntimeHealthState.unknown
    validation_errors: list[str] = Field(default_factory=list)

    @field_validator("runtime_target_id")
    @classmethod
    def _runtime_target_id_required(cls, v: str) -> str:
        v = str(v or "").strip()
        if not v:
            raise ValueError("runtime_target_id must be non-empty")
        return v

    @field_validator("allowed_capabilities", "denied_capabilities", "available_tools", "validation_errors")
    @classmethod
    def _normalize_string_list(cls, v: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in v:
            val = str(item or "").strip()
            if val and val not in normalized:
                normalized.append(val)
        return normalized

    @field_validator("max_parallel_tasks")
    @classmethod
    def _positive_parallelism(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_parallel_tasks must be >= 1")
        return v

    @model_validator(mode="after")
    def _validate_runtime_target(self) -> "WorkerRuntimeTarget":
        cap_overlap = set(self.allowed_capabilities).intersection(self.denied_capabilities)
        if cap_overlap:
            raise ValueError(f"capabilities cannot be both allowed and denied: {sorted(cap_overlap)}")
        if self.requires_workspace_scope and not self.workspace_scope.strip():
            raise ValueError("mutation-capable runtime target requires workspace_scope")
        if any(cap in _SECRET_CAPABILITIES for cap in self.allowed_capabilities):
            if self.secret_access_policy == SecretAccessPolicy.deny:
                raise ValueError("secret-capable runtime target requires explicit secret_access_policy")
        return self

    @property
    def requires_workspace_scope(self) -> bool:
        return any(cap in _MUTATION_CAPABILITIES for cap in self.allowed_capabilities)

    @property
    def is_local(self) -> bool:
        return self.runtime_kind in _LOCAL_RUNTIME_KINDS and self.data_boundary in {
            RuntimeDataBoundary.local_only,
            RuntimeDataBoundary.project_private,
            RuntimeDataBoundary.private_network,
        }

    @property
    def is_cloud(self) -> bool:
        return self.runtime_kind in _CLOUD_RUNTIME_KINDS or self.data_boundary == RuntimeDataBoundary.cloud

    @property
    def is_external(self) -> bool:
        return self.runtime_kind in _EXTERNAL_RUNTIME_KINDS or self.data_boundary in {
            RuntimeDataBoundary.external,
            RuntimeDataBoundary.cloud,
        }

    def supports_capabilities(self, capabilities: list[str]) -> tuple[bool, list[str]]:
        missing: list[str] = []
        for cap in capabilities:
            if cap in self.denied_capabilities:
                missing.append(cap)
            elif self.allowed_capabilities and cap not in self.allowed_capabilities:
                missing.append(cap)
        return not missing, missing


class WorkerCandidate(BaseModel):
    worker_id: str
    worker_kind: WorkerKind
    display_name: str = ""
    capabilities: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    supported_execution_modes: list[str] = Field(default_factory=list)
    runtime_target_ids: list[str] = Field(default_factory=list)
    health_state: RuntimeHealthState = RuntimeHealthState.unknown
    validation_errors: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    priority: int = 100

    @field_validator("worker_id")
    @classmethod
    def _worker_id_required(cls, v: str) -> str:
        v = str(v or "").strip()
        if not v:
            raise ValueError("worker_id must be non-empty")
        return v

    def supports_capabilities(self, capabilities: list[str]) -> tuple[bool, list[str]]:
        missing: list[str] = []
        for cap in capabilities:
            if cap not in self.capabilities:
                missing.append(cap)
        return not missing, missing


class RejectedWorkerRuntimeCandidate(BaseModel):
    worker_id: str | None = None
    worker_kind: WorkerKind | None = None
    runtime_target_id: str | None = None
    runtime_kind: WorkerRuntimeKind | None = None
    reason_code: str
    missing_capabilities: list[str] = Field(default_factory=list)
    detail: str = ""


class WorkerRuntimeSelectionDecision(BaseModel):
    selected_worker_id: str | None = None
    selected_worker_kind: WorkerKind | None = None
    selected_runtime_target_id: str | None = None
    selected_runtime_kind: WorkerRuntimeKind | None = None
    selection_mode: WorkerSelectionMode
    decision_status: SelectionDecisionStatus
    selected_reason: str = ""
    rejected_candidates: list[RejectedWorkerRuntimeCandidate] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    policy_decision_ref: str = ""
    context_boundary_decision: str = "not_evaluated"
    fallback_used: bool = False

    @model_validator(mode="after")
    def _validate_decision(self) -> "WorkerRuntimeSelectionDecision":
        if self.decision_status == SelectionDecisionStatus.selected:
            if not self.selected_worker_id or not self.selected_worker_kind:
                raise ValueError("selected decision requires selected_worker_id and selected_worker_kind")
            if not self.selected_runtime_target_id or not self.selected_runtime_kind:
                raise ValueError("selected decision requires selected_runtime_target_id and selected_runtime_kind")
            if not self.selected_reason:
                raise ValueError("selected decision requires selected_reason")
        return self

    @property
    def decision_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude_none=True)
        payload.pop("decision_hash", None)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class SelectedWorkerRuntimeRef(BaseModel):
    """Persistable effective worker/runtime selection metadata. DRR-T049/DRR-T055."""

    selected_worker_id: str
    selected_worker_kind: WorkerKind
    selected_runtime_target_id: str
    selected_runtime_kind: WorkerRuntimeKind
    selection_mode: WorkerSelectionMode
    selection_decision_ref: str = ""
    selection_reason: str = ""
    context_boundary_decision: str = "not_evaluated"

    @classmethod
    def from_decision(cls, decision: WorkerRuntimeSelectionDecision) -> "SelectedWorkerRuntimeRef":
        if decision.decision_status != SelectionDecisionStatus.selected:
            raise ValueError("cannot create SelectedWorkerRuntimeRef from non-selected decision")
        return cls(
            selected_worker_id=decision.selected_worker_id or "",
            selected_worker_kind=decision.selected_worker_kind or WorkerKind.disabled_placeholder,
            selected_runtime_target_id=decision.selected_runtime_target_id or "",
            selected_runtime_kind=decision.selected_runtime_kind or WorkerRuntimeKind.custom,
            selection_mode=decision.selection_mode,
            selection_decision_ref=decision.decision_hash,
            selection_reason=decision.selected_reason,
            context_boundary_decision=decision.context_boundary_decision,
        )


def capability_set_hash(capabilities: list[str]) -> str:
    raw = json.dumps(sorted(set(capabilities)), separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()
