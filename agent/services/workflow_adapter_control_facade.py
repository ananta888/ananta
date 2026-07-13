"""Authenticated Hub control facade for legacy workflow-adapter HTTP calls.

The facade translates the old adapter-shaped request into one execution plan
and one Hub queue task.  It deliberately does not start a configured Workflow
backend as that would duplicate execution when Temporal is active.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.workflow_adapter_task_queue_service import (
    WORKFLOW_ADAPTER_CONTROL_SCHEMA,
    WorkflowAdapterQueueError,
    WorkflowAdapterTaskQueuePort,
    WorkflowAdapterTaskSubmission,
)
from agent.services.workflow_backend import WorkflowRequest, WorkflowStepRequest
from agent.services.workflow_provider_selection_service import (
    WorkflowProviderDecisionPort,
    WorkflowProviderRequirement,
    build_workflow_provider_decision_service,
)
from agent.services.workflow_route_authorization_service import WorkflowRoutePrincipal
from agent.services.workflow_runtime.execution_plan import (
    ExecutionBudget,
    WorkflowRequestExecutionPlanAdapter,
)
from agent.services.workflow_runtime.streaming import (
    WORKFLOW_STREAM_REQUEST_SCHEMA,
    WorkflowStreamBatch,
    WorkflowStreamError,
    WorkflowStreamRequest,
    WorkflowStreamService,
)

WORKFLOW_ADAPTER_COMMAND_SCHEMA = "ananta.workflow-adapter-command.v1"
WORKFLOW_ADAPTER_STREAM_DESCRIPTOR_SCHEMA = (
    "ananta.workflow-adapter-stream-descriptor.v1"
)

_CONTROL_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "task_type",
        "workflow_id",
        "run_id",
        "step_id",
        "policy_version",
        "correlation_id",
        "idempotency_key",
        "allowed_tools",
        "allowed_artifacts",
        "maximum_retries",
        "max_total_tokens",
        "max_cost_micros",
        "authorization_ttl_seconds",
        "payload",
    }
)


@dataclass(frozen=True)
class WorkflowAdapterCommand:
    workflow_id: str
    run_id: str
    step_id: str
    task_type: str
    policy_version: str
    correlation_id: str
    idempotency_key: str
    payload: dict[str, Any]
    allowed_tools: tuple[str, ...]
    allowed_artifacts: tuple[str, ...]
    maximum_retries: int
    max_total_tokens: int
    max_cost_micros: int
    authorization_ttl_seconds: float

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        kind: str,
        command: str,
        principal: WorkflowRoutePrincipal,
        header_idempotency_key: str = "",
    ) -> "WorkflowAdapterCommand":
        schema = str(raw.get("schema") or "")
        if schema and schema != WORKFLOW_ADAPTER_COMMAND_SCHEMA:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_command_schema_unsupported", status_code=422
            )
        if str(kind or "").strip().lower() != "langgraph":
            raise WorkflowAdapterQueueError(
                "workflow_adapter_kind_unsupported", status_code=422
            )
        task_type = _identifier(raw.get("task_type"), "workflow_adapter_task_type_invalid")
        client_task_id = str(raw.get("task_id") or "").strip()
        idempotency_key = str(
            header_idempotency_key
            or raw.get("idempotency_key")
            or client_task_id
            or f"request-{uuid.uuid4().hex}"
        ).strip()
        if not idempotency_key or len(idempotency_key) > 256 or "\x00" in idempotency_key:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_idempotency_key_invalid", status_code=422
            )
        identity_digest = hashlib.sha256(
            "\x00".join(
                (principal.tenant_id, principal.subject, idempotency_key, command)
            ).encode("utf-8")
        ).hexdigest()[:24]
        workflow_id = _identifier(
            raw.get("workflow_id") or f"wf-adapter-{identity_digest}",
            "workflow_adapter_workflow_id_invalid",
        )
        run_id = _identifier(
            raw.get("run_id") or workflow_id,
            "workflow_adapter_run_id_invalid",
        )
        step_id = _identifier(
            raw.get("step_id") or "adapter-step",
            "workflow_adapter_step_id_invalid",
        )
        policy_version = _identifier(
            raw.get("policy_version") or "workflow-adapter-policy-v1",
            "workflow_adapter_policy_version_invalid",
        )
        correlation_id = _identifier(
            raw.get("correlation_id") or f"corr-{identity_digest}",
            "workflow_adapter_correlation_id_invalid",
        )
        explicit_payload = raw.get("payload")
        if explicit_payload is not None and not isinstance(explicit_payload, Mapping):
            raise WorkflowAdapterQueueError(
                "workflow_adapter_payload_invalid", status_code=422
            )
        # Legacy callers placed graph fields next to task_type.  Keep them as
        # an additive compatibility projection while control fields stay
        # server-bound and cannot be shadowed from payload.
        payload = {
            str(key): value
            for key, value in raw.items()
            if str(key) not in _CONTROL_FIELDS
        }
        for key, value in dict(explicit_payload or {}).items():
            if str(key) in payload and payload[str(key)] != value:
                raise WorkflowAdapterQueueError(
                    "workflow_adapter_payload_field_conflict", status_code=422
                )
            payload[str(key)] = value
        allowed_tools = _bounded_strings(
            raw.get("allowed_tools"), "workflow_adapter_allowed_tools_invalid"
        )
        allowed_artifacts = _bounded_strings(
            raw.get("allowed_artifacts"),
            "workflow_adapter_allowed_artifacts_invalid",
        )
        try:
            maximum_retries = int(raw.get("maximum_retries") or 0)
            max_total_tokens = int(raw.get("max_total_tokens") or 0)
            max_cost_micros = int(raw.get("max_cost_micros") or 0)
            ttl = float(raw.get("authorization_ttl_seconds") or 1800.0)
        except (TypeError, ValueError) as exc:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_budget_invalid", status_code=422
            ) from exc
        return cls(
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            task_type=task_type,
            policy_version=policy_version,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            payload=payload,
            allowed_tools=allowed_tools,
            allowed_artifacts=allowed_artifacts,
            maximum_retries=maximum_retries,
            max_total_tokens=max_total_tokens,
            max_cost_micros=max_cost_micros,
            authorization_ttl_seconds=ttl,
        )


class WorkflowAdapterControlFacade:
    """Process-level Hub composition; bind it before every operation."""

    def __init__(
        self,
        queue: WorkflowAdapterTaskQueuePort,
        *,
        provider_decisions: WorkflowProviderDecisionPort | None = None,
    ) -> None:
        self._queue = queue
        self._provider_decisions = (
            provider_decisions or build_workflow_provider_decision_service()
        )

    def bind(
        self, principal: WorkflowRoutePrincipal
    ) -> "AuthorizedWorkflowAdapterControl":
        if not principal.tenant_id or not principal.subject:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_principal_required", status_code=401
            )
        return AuthorizedWorkflowAdapterControl(
            queue=self._queue,
            provider_decisions=self._provider_decisions,
            principal=principal,
        )


class AuthorizedWorkflowAdapterControl:
    """Request-scoped view; principal fields are never accepted from JSON."""

    def __init__(
        self,
        *,
        queue: WorkflowAdapterTaskQueuePort,
        provider_decisions: WorkflowProviderDecisionPort,
        principal: WorkflowRoutePrincipal,
    ) -> None:
        self._queue = queue
        self._provider_decisions = provider_decisions
        self._principal = principal

    def submit(
        self,
        *,
        kind: str,
        command: str,
        body: Mapping[str, Any],
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        request = WorkflowAdapterCommand.from_mapping(
            body,
            kind=kind,
            command=command,
            principal=self._principal,
            header_idempotency_key=idempotency_key,
        )
        provider_decision = self._provider_decisions.decide(
            WorkflowProviderRequirement(
                tenant_id=self._principal.tenant_id,
                workflow_id=request.workflow_id,
                step_id=request.step_id,
                task_type=request.task_type,
                runtime_kind=str(kind).lower(),
                requires_provider=str(command).lower() == "execute",
            )
        )
        if str(command).lower() == "execute" and provider_decision.binding is None:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_provider_selection_unavailable:"
                f"{provider_decision.reason_code}",
                status_code=503,
            )
        workflow_request = WorkflowRequest(
            workflow_id=request.workflow_id,
            workflow_type=f"workflow_adapter:{kind}",
            plan_id=f"plan:{request.workflow_id}",
            steps=(
                WorkflowStepRequest(
                    step_id=request.step_id,
                    title=f"{kind} {command}",
                    task_kind=request.task_type,
                    allowed_tools=request.allowed_tools,
                    policy_scope={
                        "policy_version": request.policy_version,
                        "control_path": "workflow_adapter_task_queue",
                    },
                    metadata={
                        "required_capabilities": [
                            f"workflow.adapter.{kind}",
                        ],
                        "side_effect_class": (
                            "read" if command == "dry_run" else "idempotent_write"
                        ),
                    },
                ),
            ),
            allowed_tools=request.allowed_tools,
            policy_scope={"policy_version": request.policy_version},
            correlation_id=request.correlation_id,
            requested_by=f"principal:{self._principal.subject}",
            metadata={
                "capabilities": [
                    f"workflow.adapter.{kind}",
                ],
                "adapter_kind": kind,
                "adapter_command": command,
            },
        )
        plan = WorkflowRequestExecutionPlanAdapter.adapt(
            workflow_request,
            tenant_id=self._principal.tenant_id,
            policy_version=request.policy_version,
            default_budget=ExecutionBudget(
                max_attempts=request.maximum_retries + 1,
                timeout_seconds=request.authorization_ttl_seconds,
                max_tokens=request.max_total_tokens,
                max_cost_micros=request.max_cost_micros,
            ),
        )
        receipt = self._queue.submit(
            WorkflowAdapterTaskSubmission(
                tenant_id=self._principal.tenant_id,
                subject_id=self._principal.subject,
                workflow_id=request.workflow_id,
                run_id=request.run_id,
                step_id=request.step_id,
                plan_hash=plan.plan_hash,
                policy_version=request.policy_version,
                adapter_kind=str(kind).lower(),
                command=str(command).lower(),
                task_type=request.task_type,
                payload=request.payload,
                allowed_tools=request.allowed_tools,
                allowed_artifacts=request.allowed_artifacts,
                correlation_id=request.correlation_id,
                idempotency_key=request.idempotency_key,
                maximum_retries=request.maximum_retries,
                max_total_tokens=int(plan.budget.max_tokens or 0),
                max_cost_micros=int(plan.budget.max_cost_micros or 0),
                authorization_ttl_seconds=request.authorization_ttl_seconds,
                provider_binding=provider_decision.binding,
                provider_decision_reason=provider_decision.reason_code,
            )
        )
        payload = receipt.to_dict()
        payload.update(
            {
                "schema": WORKFLOW_ADAPTER_CONTROL_SCHEMA,
                "plan_hash": plan.plan_hash,
                "policy_version": plan.policy_version,
                "control_path": "hub_task_queue",
                "poll_url": (
                    f"/api/workflow_adapters/{kind}/operations/"
                    f"{receipt.hub_task_id}"
                ),
                "cancel_url": (
                    f"/api/workflow_adapters/{kind}/operations/"
                    f"{receipt.hub_task_id}/cancel"
                ),
                "stream": self._stream_descriptor(
                    kind=str(kind).lower(),
                    hub_task_id=receipt.hub_task_id,
                    workflow_id=receipt.workflow_id,
                ),
            }
        )
        return payload

    def status(self, *, kind: str, hub_task_id: str) -> dict[str, Any]:
        payload = self._queue.status(
            tenant_id=self._principal.tenant_id,
            subject_id=self._principal.subject,
            hub_task_id=hub_task_id,
        )
        self._assert_kind(payload, kind)
        payload["stream"] = self._stream_descriptor(
            kind=str(kind).lower(),
            hub_task_id=hub_task_id,
            workflow_id=str(payload.get("workflow_id") or ""),
        )
        return payload

    def cancel(
        self, *, kind: str, hub_task_id: str, reason: str
    ) -> dict[str, Any]:
        payload = self._queue.cancel(
            tenant_id=self._principal.tenant_id,
            subject_id=self._principal.subject,
            hub_task_id=hub_task_id,
            reason=reason,
        )
        self._assert_kind(payload, kind)
        return payload

    def stream(
        self, *, kind: str, body: Mapping[str, Any]
    ) -> WorkflowStreamBatch:
        allowed = {
            "schema",
            "hub_task_id",
            "operation_id",
            "workflow_id",
            "after_cursor",
            "max_events",
            "wait_seconds",
            "heartbeat_seconds",
        }
        if set(body) - allowed:
            raise WorkflowStreamError("workflow_stream_unknown_field")
        hub_task_id = str(
            body.get("hub_task_id") or body.get("operation_id") or ""
        ).strip()
        if not hub_task_id:
            raise WorkflowStreamError("workflow_adapter_stream_task_id_required")
        status = self.status(kind=kind, hub_task_id=hub_task_id)
        workflow_id = str(status.get("workflow_id") or "")
        requested_workflow_id = str(body.get("workflow_id") or workflow_id).strip()
        if requested_workflow_id != workflow_id:
            raise WorkflowStreamError("workflow_stream_workflow_binding_mismatch")
        request = WorkflowStreamRequest.from_mapping(
            {
                "schema": str(body.get("schema") or WORKFLOW_STREAM_REQUEST_SCHEMA),
                "workflow_id": workflow_id,
                "after_cursor": body.get("after_cursor") or "",
                "max_events": body.get("max_events") or 128,
                "wait_seconds": body.get("wait_seconds") or 0,
                "heartbeat_seconds": body.get("heartbeat_seconds") or 15,
            }
        )
        history = self._queue.history(
            tenant_id=self._principal.tenant_id,
            subject_id=self._principal.subject,
            hub_task_id=hub_task_id,
        )
        return WorkflowStreamService(_FixedHistory(history)).read(request)

    @staticmethod
    def _assert_kind(payload: Mapping[str, Any], kind: str) -> None:
        if str(payload.get("adapter_kind") or "") != str(kind).strip().lower():
            raise WorkflowAdapterQueueError(
                "workflow_adapter_task_not_found", status_code=404
            )

    @staticmethod
    def _stream_descriptor(
        *, kind: str, hub_task_id: str, workflow_id: str
    ) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_ADAPTER_STREAM_DESCRIPTOR_SCHEMA,
            "transport": "ndjson_post",
            "event_schema": "ananta.workflow_stream_frame.v1",
            "url": f"/api/workflow_adapters/{kind}/stream",
            "request": {
                "schema": WORKFLOW_STREAM_REQUEST_SCHEMA,
                "hub_task_id": hub_task_id,
                "workflow_id": workflow_id,
                "after_cursor": "",
                "max_events": 128,
            },
        }


class _FixedHistory:
    def __init__(self, events: tuple[dict[str, Any], ...]) -> None:
        self._events = events

    def list_workflow_events(self, _workflow_id: str) -> tuple[dict[str, Any], ...]:
        return self._events


_lock = threading.RLock()
_facade: Any | None = None


def get_workflow_adapter_control_facade() -> Any:
    global _facade
    if _facade is not None:
        return _facade
    with _lock:
        if _facade is None:
            from agent.services.workflow_adapter_unified_control import (
                UnifiedWorkflowAdapterControlFacade,
            )
            from agent.services.workflow_control_composition import (
                get_workflow_backend_control_facade,
            )

            _facade = UnifiedWorkflowAdapterControlFacade(
                get_workflow_backend_control_facade()
            )
    return _facade


def reset_workflow_adapter_control_facade() -> None:
    global _facade
    with _lock:
        _facade = None


def _identifier(value: object, reason_code: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 256
        or "\x00" in text
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:/-"
            for character in text
        )
    ):
        raise WorkflowAdapterQueueError(reason_code, status_code=422)
    return text


def _bounded_strings(value: object, reason_code: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise WorkflowAdapterQueueError(reason_code, status_code=422)
    result: list[str] = []
    for item in value:
        text = _identifier(item, reason_code)
        if text not in result:
            result.append(text)
    if len(result) > 128:
        raise WorkflowAdapterQueueError(reason_code, status_code=422)
    return tuple(result)


__all__ = [
    "WORKFLOW_ADAPTER_COMMAND_SCHEMA",
    "WORKFLOW_ADAPTER_STREAM_DESCRIPTOR_SCHEMA",
    "AuthorizedWorkflowAdapterControl",
    "WorkflowAdapterCommand",
    "WorkflowAdapterControlFacade",
    "get_workflow_adapter_control_facade",
    "reset_workflow_adapter_control_facade",
]
