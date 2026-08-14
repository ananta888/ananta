"""Segregated runtime and Hub bridge ports for workflow execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.security import SignedCheckpoint


@dataclass(frozen=True)
class RuntimeValidationReport:
    runtime_id: str
    valid: bool
    reason_codes: tuple[str, ...] = ()
    unsupported_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class DelegatedExecutionRequest:
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    attempt_id: str
    fencing_token: int
    plan_hash: str
    policy_version: str
    authorization_envelope: dict[str, Any]
    input_artifact_refs: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    schema: str = "ananta.delegated_execution_request.v1"


@dataclass(frozen=True)
class DelegatedExecutionResult:
    runtime_id: str
    run_id: str
    step_id: str
    attempt_id: str
    fencing_token: int
    status: str
    artifact_refs: tuple[str, ...] = ()
    reason_code: str = ""
    schema: str = "ananta.delegated_execution_result.v1"


@dataclass(frozen=True)
class RuntimeStreamEvent:
    event_type: str
    run_id: str
    step_id: str
    cursor: str
    payload: dict[str, Any] = field(default_factory=dict)
    schema: str = "ananta.runtime_stream_event.v1"


class ExecutionRuntimePort(Protocol):
    """Worker-side validation and one already delegated step execution."""

    @property
    def runtime_id(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[str]: ...

    def validate(self, plan: ExecutionPlan) -> RuntimeValidationReport: ...

    def execute(self, request: DelegatedExecutionRequest) -> DelegatedExecutionResult: ...


class StreamingRuntimePort(Protocol):
    def stream(self, request: DelegatedExecutionRequest, *, after_cursor: str = "") -> Iterable[RuntimeStreamEvent]: ...


class CheckpointRuntimePort(Protocol):
    def checkpoint(self, *, request: DelegatedExecutionRequest) -> SignedCheckpoint: ...


class ResumableRuntimePort(Protocol):
    def resume(
        self,
        *,
        request: DelegatedExecutionRequest,
        checkpoint: SignedCheckpoint,
    ) -> DelegatedExecutionResult: ...


class DurableRunInfrastructurePort(Protocol):
    """Technical durable-run operations; never a second control plane."""

    def start(self, command: dict[str, Any]) -> dict[str, Any]: ...

    def describe(self, *, tenant_id: str, run_id: str) -> dict[str, Any]: ...

    def signal(self, *, tenant_id: str, run_id: str, command: dict[str, Any]) -> dict[str, Any]: ...

    def signal_persisted(
        self,
        *,
        tenant_id: str,
        run_id: str,
        command: dict[str, Any],
    ) -> dict[str, Any]: ...

    def cancel(self, *, tenant_id: str, run_id: str, reason: str) -> dict[str, Any]: ...

    def history(self, *, tenant_id: str, run_id: str, after_cursor: str = "") -> dict[str, Any]: ...
