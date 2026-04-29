from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .interfaces import ProviderDescriptor, ProviderHealthReport


@dataclass(frozen=True)
class WorkflowExecutionRequest:
    workflow_id: str
    input_payload: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    callback_correlation_id: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True)
class WorkflowExecutionResult:
    status: str
    output_payload: dict[str, Any] = field(default_factory=dict)
    callback_correlation_id: str | None = None
    provider_run_ref: str | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowProvider(Protocol):
    descriptor: ProviderDescriptor

    def health(self) -> ProviderHealthReport: ...

    def execute(self, request: WorkflowExecutionRequest) -> WorkflowExecutionResult: ...
