from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from ananta_contracts.workflow_operation import operation_id_for
from worker.core.tool_registry import (
    ResourceLimits,
    ToolInvocationEnvelope,
    ToolResult,
    WorkerToolEntry,
)


@dataclass(frozen=True)
class ToolCallRequest:
    tenant_id: str
    run_id: str
    step_id: str
    attempt_id: str
    fencing_token: int
    tool_id: str
    arguments: dict[str, Any]
    authorization_envelope: dict[str, Any]
    approval_ref: str | None = None
    requested_limits: ResourceLimits = field(default_factory=ResourceLimits)
    operation_id: str = ""
    workflow_id: str = ""
    plan_hash: str = ""
    policy_version: str = ""
    correlation_id: str = ""
    hub_task_id: str = ""
    goal_id: str = ""
    allowed_policy_scopes: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()

    def resolved_operation_id(self) -> str:
        expected = operation_id_for(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            step_id=self.step_id,
            declared_operation=f"tool:{self.tool_id}",
        )
        if self.operation_id and self.operation_id != expected:
            raise ValueError("tool_operation_id_mismatch")
        return expected


@dataclass(frozen=True)
class ToolCallDecision:
    allowed: bool
    reason_code: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallOutcome:
    status: str
    operation_id: str
    reason_code: str
    result: ToolResult | None = None
    audit_events: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.tool_call_outcome.v1",
            "status": self.status,
            "operation_id": self.operation_id,
            "reason_code": self.reason_code,
            "result": dict(self.result) if self.result is not None else None,
            "audit_events": [dict(item) for item in self.audit_events],
        }


class AuthorizationPort(Protocol):
    def verify(self, request: ToolCallRequest, descriptor: WorkerToolEntry) -> ToolCallDecision: ...


class ToolDescriptorPort(Protocol):
    """Small adapter seam over existing tool registries; it owns no global state."""

    def get(self, tool_id: str) -> WorkerToolEntry | None: ...

    def validate_invocation(self, envelope: ToolInvocationEnvelope) -> list[str]: ...


class ToolPolicyPort(Protocol):
    def authorize(self, request: ToolCallRequest, descriptor: WorkerToolEntry) -> ToolCallDecision: ...


class ToolBudgetPort(Protocol):
    def reserve(self, request: ToolCallRequest, descriptor: WorkerToolEntry) -> ToolCallDecision: ...


class ToolApprovalPort(Protocol):
    def authorize(self, request: ToolCallRequest, descriptor: WorkerToolEntry) -> ToolCallDecision: ...


class ToolRedactionPort(Protocol):
    def redact_arguments(
        self,
        request: ToolCallRequest,
        descriptor: WorkerToolEntry,
    ) -> dict[str, Any]: ...

    def redact_result(
        self,
        result: ToolResult,
        request: ToolCallRequest,
        descriptor: WorkerToolEntry,
    ) -> ToolResult: ...


class RecursiveToolRedactor:
    """Small fail-safe boundary for arguments, results and opaque secret refs."""

    _SENSITIVE_KEYS = frozenset(
        {"api_key", "authorization", "cookie", "credential", "password", "secret", "token"}
    )

    @classmethod
    def _redact(cls, value: Any, *, key: str = "", secret_refs: tuple[str, ...] = ()) -> Any:
        normalized_key = key.strip().lower()
        if normalized_key in cls._SENSITIVE_KEYS and not normalized_key.endswith("_ref"):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                str(item_key): cls._redact(item, key=str(item_key), secret_refs=secret_refs)
                for item_key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item, secret_refs=secret_refs) for item in value]
        if isinstance(value, tuple):
            return [cls._redact(item, secret_refs=secret_refs) for item in value]
        if isinstance(value, str):
            result = value
            for secret in secret_refs:
                if secret:
                    result = result.replace(secret, "[REDACTED]")
            return result
        return value

    def redact_arguments(
        self,
        request: ToolCallRequest,
        descriptor: WorkerToolEntry,
    ) -> dict[str, Any]:
        del descriptor
        return dict(self._redact(request.arguments, secret_refs=request.secret_refs))

    def redact_result(
        self,
        result: ToolResult,
        request: ToolCallRequest,
        descriptor: WorkerToolEntry,
    ) -> ToolResult:
        del descriptor
        return ToolResult(**dict(self._redact(dict(result), secret_refs=request.secret_refs)))


class SideEffectLedgerPort(Protocol):
    def claim(
        self,
        *,
        operation_id: str,
        fencing_token: int,
        metadata: dict[str, Any],
    ) -> ToolCallDecision: ...

    def complete(
        self,
        *,
        operation_id: str,
        fencing_token: int,
        result_ref: str,
        metadata: dict[str, Any],
    ) -> None: ...

    def fail(
        self,
        *,
        operation_id: str,
        fencing_token: int,
        reason_code: str,
        uncertain: bool,
        metadata: dict[str, Any],
    ) -> None: ...


class ToolInvokerPort(Protocol):
    def invoke(
        self,
        request: ToolCallRequest,
        descriptor: WorkerToolEntry,
        *,
        limits: ResourceLimits,
    ) -> ToolResult: ...


class ToolAuditPort(Protocol):
    def record(self, event: dict[str, Any]) -> None: ...


class ToolCallingPipeline:
    """Single policy/authorization/budget/ledger path for worker tool calls."""

    def __init__(
        self,
        *,
        registry: ToolDescriptorPort,
        authorization: AuthorizationPort,
        policy: ToolPolicyPort,
        budget: ToolBudgetPort,
        approval: ToolApprovalPort,
        redaction: ToolRedactionPort | None = None,
        ledger: SideEffectLedgerPort,
        invoker: ToolInvokerPort,
        audit: ToolAuditPort,
    ) -> None:
        self._registry = registry
        self._authorization = authorization
        self._policy = policy
        self._budget = budget
        self._approval = approval
        self._redaction = redaction or RecursiveToolRedactor()
        self._ledger = ledger
        self._invoker = invoker
        self._audit = audit

    @staticmethod
    def _event(
        request: ToolCallRequest,
        *,
        operation_id: str,
        event_type: str,
        reason_code: str = "",
    ) -> dict[str, Any]:
        return {
            "schema": "ananta.tool_call_event.v1",
            "event_type": event_type,
            "tenant_id": request.tenant_id,
            "run_id": request.run_id,
            "step_id": request.step_id,
            "attempt_id": request.attempt_id,
            "fencing_token": request.fencing_token,
            "tool_id": request.tool_id,
            "operation_id": operation_id,
            "reason_code": reason_code,
        }

    def _deny(
        self,
        request: ToolCallRequest,
        *,
        operation_id: str,
        reason_code: str,
        audit_events: list[dict[str, Any]],
    ) -> ToolCallOutcome:
        event = self._event(
            request,
            operation_id=operation_id,
            event_type="workflow.tool.blocked",
            reason_code=reason_code,
        )
        self._audit.record(event)
        audit_events.append(event)
        return ToolCallOutcome(
            status="blocked",
            operation_id=operation_id,
            reason_code=reason_code,
            audit_events=tuple(audit_events),
        )

    def execute(self, request: ToolCallRequest) -> ToolCallOutcome:
        audit_events: list[dict[str, Any]] = []
        try:
            operation_id = request.resolved_operation_id()
        except ValueError as exc:
            return self._deny(
                request,
                operation_id="",
                reason_code=str(exc),
                audit_events=audit_events,
            )
        descriptor = self._registry.get(request.tool_id)
        if descriptor is None:
            return self._deny(
                request,
                operation_id=operation_id,
                reason_code="tool_not_registered",
                audit_events=audit_events,
            )

        envelope = ToolInvocationEnvelope(
            execution_id=request.attempt_id,
            tool_id=request.tool_id,
            arguments=dict(request.arguments),
            approval_ref=request.approval_ref,
            resource_limits=request.requested_limits,
        )
        argument_errors = self._registry.validate_invocation(envelope)
        if argument_errors:
            return self._deny(
                request,
                operation_id=operation_id,
                reason_code="tool_arguments_invalid",
                audit_events=audit_events,
            )

        for gate_name, decision in (
            ("authorization", self._authorization.verify(request, descriptor)),
            ("policy", self._policy.authorize(request, descriptor)),
            ("budget", self._budget.reserve(request, descriptor)),
            ("approval", self._approval.authorize(request, descriptor)),
        ):
            gate_event = self._event(
                request,
                operation_id=operation_id,
                event_type=f"workflow.tool.{gate_name}_checked",
                reason_code=decision.reason_code,
            )
            self._audit.record(gate_event)
            audit_events.append(gate_event)
            if not decision.allowed:
                return self._deny(
                    request,
                    operation_id=operation_id,
                    reason_code=decision.reason_code or f"{gate_name}_denied",
                    audit_events=audit_events,
                )

        try:
            safe_request = replace(
                request,
                arguments=self._redaction.redact_arguments(request, descriptor),
            )
        except Exception as exc:  # A redaction failure must not reach a tool.
            return self._deny(
                request,
                operation_id=operation_id,
                reason_code=f"tool_redaction_failed:{type(exc).__name__}",
                audit_events=audit_events,
            )
        redaction_event = self._event(
            request,
            operation_id=operation_id,
            event_type="workflow.tool.redaction_checked",
            reason_code="tool_arguments_redacted",
        )
        self._audit.record(redaction_event)
        audit_events.append(redaction_event)

        claim_details: dict[str, Any] = {}
        if descriptor.side_effects:
            claim = self._ledger.claim(
                operation_id=operation_id,
                fencing_token=request.fencing_token,
                metadata={
                    "tenant_id": request.tenant_id,
                    "workflow_id": request.workflow_id,
                    "run_id": request.run_id,
                    "step_id": request.step_id,
                    "plan_hash": request.plan_hash,
                    "policy_version": request.policy_version,
                    "authorization_envelope": dict(request.authorization_envelope),
                    "correlation_id": request.correlation_id,
                    "attempt_id": request.attempt_id,
                    "tool_id": request.tool_id,
                    "side_effect_class": descriptor.side_effect_class,
                    "side_effects": list(descriptor.side_effects),
                    "approval_ref": request.approval_ref or "",
                    "hub_task_id": request.hub_task_id,
                    "goal_id": request.goal_id,
                    "arguments": dict(request.arguments),
                },
            )
            if not claim.allowed:
                return self._deny(
                    request,
                    operation_id=operation_id,
                    reason_code=claim.reason_code or "side_effect_claim_denied",
                    audit_events=audit_events,
                )
            claim_details = dict(claim.details)

        started = self._event(
            request,
            operation_id=operation_id,
            event_type="workflow.tool.started",
        )
        self._audit.record(started)
        audit_events.append(started)
        limits = ResourceLimits(
            timeout_seconds=min(
                request.requested_limits.timeout_seconds,
                descriptor.resource_limits.timeout_seconds,
            ),
            max_output_chars=min(
                request.requested_limits.max_output_chars,
                descriptor.resource_limits.max_output_chars,
            ),
            max_artifact_bytes=min(
                request.requested_limits.max_artifact_bytes,
                descriptor.resource_limits.max_artifact_bytes,
            ),
            max_files_touched=min(
                request.requested_limits.max_files_touched,
                descriptor.resource_limits.max_files_touched,
            ),
        )
        try:
            result = self._invoker.invoke(safe_request, descriptor, limits=limits)
            result = self._redaction.redact_result(result, safe_request, descriptor)
        except Exception as exc:
            if descriptor.side_effects:
                self._ledger.fail(
                    operation_id=operation_id,
                    fencing_token=request.fencing_token,
                    reason_code=f"tool_invocation_exception:{type(exc).__name__}",
                    uncertain=True,
                    metadata={
                        "attempt_id": request.attempt_id,
                        "expected_revision": claim_details.get("revision"),
                        "tool_id": request.tool_id,
                        "tenant_id": request.tenant_id,
                        "workflow_id": request.workflow_id,
                        "run_id": request.run_id,
                        "step_id": request.step_id,
                        "plan_hash": request.plan_hash,
                        "policy_version": request.policy_version,
                        "authorization_envelope": dict(request.authorization_envelope),
                        "correlation_id": request.correlation_id,
                        "side_effect_class": descriptor.side_effect_class,
                        "approval_ref": request.approval_ref or "",
                        "hub_task_id": request.hub_task_id,
                        "goal_id": request.goal_id,
                        "arguments": dict(request.arguments),
                    },
                )
            failed = self._event(
                request,
                operation_id=operation_id,
                event_type="workflow.tool.failed",
                reason_code=f"tool_invocation_exception:{type(exc).__name__}",
            )
            self._audit.record(failed)
            audit_events.append(failed)
            return ToolCallOutcome(
                status="failed",
                operation_id=operation_id,
                reason_code=failed["reason_code"],
                audit_events=tuple(audit_events),
            )

        if descriptor.side_effects:
            if result.success:
                result_ref = str(result.get("execution_id") or operation_id)
                self._ledger.complete(
                    operation_id=operation_id,
                    fencing_token=request.fencing_token,
                    result_ref=result_ref,
                    metadata={
                        "attempt_id": request.attempt_id,
                        "expected_revision": claim_details.get("revision"),
                        "tool_id": request.tool_id,
                        "tenant_id": request.tenant_id,
                        "workflow_id": request.workflow_id,
                        "run_id": request.run_id,
                        "step_id": request.step_id,
                        "plan_hash": request.plan_hash,
                        "policy_version": request.policy_version,
                        "authorization_envelope": dict(request.authorization_envelope),
                        "correlation_id": request.correlation_id,
                        "side_effect_class": descriptor.side_effect_class,
                        "approval_ref": request.approval_ref or "",
                        "hub_task_id": request.hub_task_id,
                        "goal_id": request.goal_id,
                        "arguments": dict(request.arguments),
                    },
                )
            else:
                self._ledger.fail(
                    operation_id=operation_id,
                    fencing_token=request.fencing_token,
                    reason_code=str(result.get("reason_code") or "tool_failed"),
                    uncertain=False,
                    metadata={
                        "attempt_id": request.attempt_id,
                        "expected_revision": claim_details.get("revision"),
                        "tool_id": request.tool_id,
                        "tenant_id": request.tenant_id,
                        "workflow_id": request.workflow_id,
                        "run_id": request.run_id,
                        "step_id": request.step_id,
                        "plan_hash": request.plan_hash,
                        "policy_version": request.policy_version,
                        "authorization_envelope": dict(request.authorization_envelope),
                        "correlation_id": request.correlation_id,
                        "side_effect_class": descriptor.side_effect_class,
                        "approval_ref": request.approval_ref or "",
                        "hub_task_id": request.hub_task_id,
                        "goal_id": request.goal_id,
                        "arguments": dict(request.arguments),
                    },
                )
        completed = self._event(
            request,
            operation_id=operation_id,
            event_type="workflow.tool.completed" if result.success else "workflow.tool.failed",
            reason_code="" if result.success else str(result.get("reason_code") or "tool_failed"),
        )
        self._audit.record(completed)
        audit_events.append(completed)
        return ToolCallOutcome(
            status="success" if result.success else "failed",
            operation_id=operation_id,
            reason_code="" if result.success else completed["reason_code"],
            result=result,
            audit_events=tuple(audit_events),
        )
