"""VP Step Execution Layer (VPEXEC-001).

Three execution modes:
  worker_dispatch  — dispatch_capable=True, queued to worker (existing path)
  vp_adapter       — VP-native adapter runs directly in hub process
  not_executable   — implementation_state=registered_only, no adapter present

Dry-run: execution_plan() returns one StepExecutionPlan per step showing
  executable, execution_mode, execution_reason, and all runtime-truth fields.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent.visual_process.models import VisualProcessStep
from agent.visual_process.task_kind_registry import get_task_kind_info, is_dispatch_capable


@dataclass
class StepExecutionResult:
    status: str                           # "success"|"failed"|"not_wired"|"skipped"
    outputs: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    backend_service: str = ""
    executable: bool = False
    execution_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "outputs": self.outputs,
            "diagnostics": self.diagnostics,
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
            "backend_service": self.backend_service,
            "executable": self.executable,
            "execution_reason": self.execution_reason,
        }


@dataclass
class StepExecutionPlan:
    step_id: str
    step_label: str
    kind: str
    executable: bool
    execution_mode: str                   # "worker_dispatch"|"vp_adapter"|"not_executable"
    execution_reason: str
    implementation_state: str
    implementation_status: str
    backend_service: str
    uses_llm: bool
    uses_network: bool
    deterministic: bool
    side_effects: list[str]
    risk_level: str
    requires_approval: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_label": self.step_label,
            "kind": self.kind,
            "executable": self.executable,
            "execution_mode": self.execution_mode,
            "execution_reason": self.execution_reason,
            "implementation_state": self.implementation_state,
            "implementation_status": self.implementation_status,
            "backend_service": self.backend_service,
            "uses_llm": self.uses_llm,
            "uses_network": self.uses_network,
            "deterministic": self.deterministic,
            "side_effects": self.side_effects,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
        }


class StepAdapter(ABC):
    """Base class for VP-native step adapters (vp_adapter execution mode)."""

    @property
    @abstractmethod
    def kind(self) -> str: ...

    @abstractmethod
    def execute(
        self,
        step: VisualProcessStep,
        artifacts: dict[str, Any],
        context: dict[str, Any],
    ) -> StepExecutionResult: ...


class StepExecutor:
    """Dispatches VP step execution to registered adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, StepAdapter] = {}

    def register(self, adapter: StepAdapter) -> None:
        self._adapters[adapter.kind] = adapter

    def execution_mode(self, kind: str) -> str:
        if is_dispatch_capable(kind):
            return "worker_dispatch"
        if kind in self._adapters:
            return "vp_adapter"
        return "not_executable"

    def execution_plan(self, steps: list[VisualProcessStep]) -> list[StepExecutionPlan]:
        plans: list[StepExecutionPlan] = []
        for step in steps:
            info = get_task_kind_info(step.kind) or {}
            impl_state = info.get("implementation_state", "unknown")
            impl_status = info.get("implementation_status", "unknown")
            backend = info.get("backend_service", "")
            mode = self.execution_mode(step.kind)
            executable = mode != "not_executable"
            if mode == "worker_dispatch":
                reason = "worker_dispatch: dispatch_capable=true, step queued to worker"
            elif mode == "vp_adapter":
                reason = f"vp_adapter: VP-native adapter ({backend})"
            else:
                reason = (
                    f"not_executable: implementation_state='{impl_state}' — "
                    "no VP adapter registered. Use as design-only or build an adapter."
                )
            plans.append(StepExecutionPlan(
                step_id=step.id,
                step_label=step.label,
                kind=step.kind,
                executable=executable,
                execution_mode=mode,
                execution_reason=reason,
                implementation_state=impl_state,
                implementation_status=impl_status,
                backend_service=backend,
                uses_llm=bool(info.get("uses_llm", False)),
                uses_network=bool(info.get("uses_network", False)),
                deterministic=bool(info.get("deterministic", False)),
                side_effects=list(info.get("side_effects", [])),
                risk_level=info.get("risk_level", "unknown"),
                requires_approval=bool(info.get("requires_approval", False)),
            ))
        return plans

    def execute(
        self,
        step: VisualProcessStep,
        artifacts: dict[str, Any],
        context: dict[str, Any],
    ) -> StepExecutionResult:
        if is_dispatch_capable(step.kind):
            info = get_task_kind_info(step.kind) or {}
            return StepExecutionResult(
                status="skipped",
                executable=True,
                execution_reason="worker_dispatch: dispatched to worker queue",
                backend_service=info.get("backend_service", ""),
            )
        adapter = self._adapters.get(step.kind)
        if adapter is None:
            info = get_task_kind_info(step.kind) or {}
            return StepExecutionResult(
                status="not_wired",
                executable=False,
                execution_reason=(
                    f"No VP adapter for kind='{step.kind}' "
                    f"(implementation_state='{info.get('implementation_state', 'unknown')}'). "
                    "Step can be designed but not executed from VP."
                ),
                backend_service=info.get("backend_service", ""),
            )
        t0 = time.monotonic()
        result = adapter.execute(step, artifacts, context)
        result.duration_ms = (time.monotonic() - t0) * 1000.0
        return result


# ── Module-level singleton ────────────────────────────────────────────────────

_executor: StepExecutor | None = None


def get_step_executor() -> StepExecutor:
    global _executor
    if _executor is None:
        _executor = StepExecutor()
        _register_builtin_adapters(_executor)
    return _executor


def _register_builtin_adapters(executor: StepExecutor) -> None:
    from agent.visual_process.step_adapters import (
        EmbedApiAdapter,
        QueryRewriteAdapter,
        RerankAdapter,
        SignRotationAdapter,
        TurboQuantMseAdapter,
        WorkspaceDiffAdapter,
        WorkspaceSnapshotAdapter,
    )
    for cls in [
        QueryRewriteAdapter,
        RerankAdapter,
        EmbedApiAdapter,
        SignRotationAdapter,
        TurboQuantMseAdapter,
        WorkspaceSnapshotAdapter,
        WorkspaceDiffAdapter,
    ]:
        executor.register(cls())
