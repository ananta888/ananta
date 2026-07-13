"""Compatibility translation onto the process-wide WorkflowControlService."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from agent.services.workflow_adapter_control_facade import WorkflowAdapterCommand
from agent.services.workflow_adapter_task_queue_service import (
    WORKFLOW_ADAPTER_CONTROL_SCHEMA,
    WorkflowAdapterQueueError,
)
from agent.services.workflow_backend import WorkflowRequest, WorkflowStepRequest
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_control_composition import (
    ROUTE_CONTROL_AUTHORIZATION_SCHEMA,
    WorkflowBackendControlFacade,
)
from agent.services.workflow_control_service import (
    WorkflowControlCommand,
    WorkflowPrincipal,
)
from agent.services.workflow_route_authorization_service import WorkflowRoutePrincipal
from agent.services.workflow_runtime.execution_plan import (
    ExecutionBudget,
    ExecutionPlan,
    WorkflowRequestExecutionPlanAdapter,
)
from agent.services.workflow_runtime.streaming import (
    WORKFLOW_STREAM_REQUEST_SCHEMA,
    WorkflowStreamBatch,
    WorkflowStreamError,
    WorkflowStreamRequest,
    WorkflowStreamService,
)

WORKFLOW_ADAPTER_STREAM_DESCRIPTOR_SCHEMA = (
    "ananta.workflow-adapter-stream-descriptor.v1"
)


class UnifiedWorkflowAdapterControlFacade:
    """Own no queue or selection state; bind the shared Hub control service."""

    def __init__(self, composition: WorkflowBackendControlFacade) -> None:
        self._composition = composition

    def bind(
        self,
        principal: WorkflowRoutePrincipal,
    ) -> "AuthorizedUnifiedWorkflowAdapterControl":
        if not principal.tenant_id or not principal.subject:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_principal_required", status_code=401
            )
        return AuthorizedUnifiedWorkflowAdapterControl(
            composition=self._composition,
            principal=principal,
        )


class AuthorizedUnifiedWorkflowAdapterControl:
    def __init__(
        self,
        *,
        composition: WorkflowBackendControlFacade,
        principal: WorkflowRoutePrincipal,
    ) -> None:
        self._composition = composition
        self._principal = WorkflowPrincipal(
            tenant_id=principal.tenant_id,
            subject_id=principal.subject,
            roles=principal.roles,
        )

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
            principal=WorkflowRoutePrincipal(
                tenant_id=self._principal.tenant_id,
                subject=self._principal.subject_id,
            ),
            header_idempotency_key=idempotency_key,
        )
        plan, workflow_request = self._contracts(request, command=command)
        binding = WorkflowControlRunBinding(
            tenant_id=self._principal.tenant_id,
            subject_id=self._principal.subject_id,
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            runtime_id="pending",
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            checkpoint_id=f"langgraph-initial:{plan.plan_hash}",
            request=workflow_request,
            execution_plan=plan.to_dict(),
        )
        self._composition.bindings.put(binding)
        try:
            handle = self._composition.control_service.start(
                principal=self._principal,
                plan=plan,
                run_id=request.run_id,
                authorization_envelope=self._route_envelope(binding),
                preferred_runtime="langgraph",
                allowed_runtimes=("langgraph",),
            )
        except Exception:
            self._composition.bindings.discard(
                binding.workflow_id,
                plan_hash=binding.plan_hash,
            )
            raise
        status = self._composition.bindings.last_status(binding.workflow_id) or {}
        response = {
            "schema": WORKFLOW_ADAPTER_CONTROL_SCHEMA,
            "hub_task_id": request.run_id,
            "operation_id": request.run_id,
            "workflow_id": request.workflow_id,
            "run_id": request.run_id,
            "step_id": request.step_id,
            "adapter_kind": "langgraph",
            "command": command,
            "accepted": True,
            "duplicate": False,
            "status": handle.status,
            "reason_code": handle.reason_code,
            "plan_hash": plan.plan_hash,
            "policy_version": plan.policy_version,
            "control_path": "workflow_control_service",
            "runtime_id": handle.runtime_id,
            "task_refs": [
                str(step.get("hub_task_id"))
                for step in status.get("steps") or ()
                if str(step.get("hub_task_id") or "")
            ],
            "poll_url": (
                f"/api/workflow_adapters/langgraph/operations/{request.run_id}"
            ),
            "cancel_url": (
                f"/api/workflow_adapters/langgraph/operations/{request.run_id}/cancel"
            ),
        }
        response["stream"] = self._stream_descriptor(
            run_id=request.run_id,
            workflow_id=request.workflow_id,
        )
        return response

    def status(self, *, kind: str, hub_task_id: str) -> dict[str, Any]:
        self._assert_kind(kind)
        binding = self._binding(hub_task_id)
        payload = dict(
            self._composition.control_service.query(
                principal=self._principal,
                workflow_id=binding.workflow_id,
                run_id=binding.run_id,
            )
        )
        payload["adapter_kind"] = "langgraph"
        payload["operation_id"] = binding.run_id
        payload["stream"] = self._stream_descriptor(
            run_id=binding.run_id,
            workflow_id=binding.workflow_id,
        )
        return payload

    def cancel(
        self,
        *,
        kind: str,
        hub_task_id: str,
        reason: str,
    ) -> dict[str, Any]:
        self._assert_kind(kind)
        binding = self._binding(hub_task_id)
        status = self._composition.bindings.last_status(binding.workflow_id) or {}
        command = WorkflowControlCommand(
            command_id=f"adapter-cancel-{uuid.uuid4().hex}",
            command_type="cancel",
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            step_id="__workflow__",
            checkpoint_id=str(status.get("checkpoint_ref") or binding.checkpoint_id),
            expected_revision=int(status.get("revision") or 0),
            plan_hash=binding.plan_hash,
            policy_version=binding.policy_version,
            authorization_envelope=self._route_envelope(binding),
            payload={"reason": str(reason or "workflow_adapter_cancelled")[:240]},
        )
        payload = dict(
            self._composition.control_service.command(
                principal=self._principal,
                command=command,
            )
        )
        payload["adapter_kind"] = "langgraph"
        payload["operation_id"] = binding.run_id
        return payload

    def stream(
        self,
        *,
        kind: str,
        body: Mapping[str, Any],
    ) -> WorkflowStreamBatch:
        self._assert_kind(kind)
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
        operation_id = str(
            body.get("hub_task_id") or body.get("operation_id") or ""
        ).strip()
        if not operation_id:
            raise WorkflowStreamError("workflow_adapter_stream_task_id_required")
        binding = self._binding(operation_id)
        requested = str(body.get("workflow_id") or binding.workflow_id).strip()
        if requested != binding.workflow_id:
            raise WorkflowStreamError("workflow_stream_workflow_binding_mismatch")
        request = WorkflowStreamRequest.from_mapping(
            {
                "schema": str(body.get("schema") or WORKFLOW_STREAM_REQUEST_SCHEMA),
                "workflow_id": binding.workflow_id,
                "after_cursor": body.get("after_cursor") or "",
                "max_events": body.get("max_events") or 128,
                "wait_seconds": body.get("wait_seconds") or 0,
                "heartbeat_seconds": body.get("heartbeat_seconds") or 15,
            }
        )
        history = self._composition.control_service.history(
            principal=self._principal,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
        )
        return WorkflowStreamService(_FixedHistory(history)).read(request)

    def _contracts(
        self,
        request: WorkflowAdapterCommand,
        *,
        command: str,
    ) -> tuple[ExecutionPlan, WorkflowRequest]:
        raw_plan = request.payload.get("execution_plan")
        if isinstance(raw_plan, Mapping):
            plan = ExecutionPlan.from_mapping(dict(raw_plan))
            if (
                plan.tenant_id != self._principal.tenant_id
                or plan.workflow_id != request.workflow_id
                or plan.policy_version != request.policy_version
            ):
                raise WorkflowAdapterQueueError(
                    "workflow_adapter_plan_binding_mismatch", status_code=422
                )
            steps = _steps_from_plan(plan)
        else:
            steps = (
                WorkflowStepRequest(
                    step_id=request.step_id,
                    title=f"langgraph {command}",
                    task_kind=request.task_type,
                    allowed_tools=request.allowed_tools,
                    policy_scope={"policy_version": request.policy_version},
                    metadata={
                        "side_effect_class": (
                            "read" if command == "dry_run" else "idempotent_write"
                        ),
                    },
                ),
            )
            provisional = WorkflowRequest(
                workflow_id=request.workflow_id,
                workflow_type="workflow_adapter:langgraph",
                plan_id=f"plan:{request.workflow_id}",
                steps=steps,
                allowed_tools=request.allowed_tools,
                policy_scope={"policy_version": request.policy_version},
                correlation_id=request.correlation_id,
                requested_by=f"principal:{self._principal.subject_id}",
                metadata={},
            )
            plan = WorkflowRequestExecutionPlanAdapter.adapt(
                provisional,
                tenant_id=self._principal.tenant_id,
                policy_version=request.policy_version,
                default_budget=ExecutionBudget(
                    max_attempts=request.maximum_retries + 1,
                    timeout_seconds=request.authorization_ttl_seconds,
                    max_tokens=request.max_total_tokens,
                    max_cost_micros=request.max_cost_micros,
                ),
            )
        workflow_request = WorkflowRequest(
            workflow_id=request.workflow_id,
            workflow_type="workflow_adapter:langgraph",
            plan_id=plan.plan_id,
            steps=steps,
            allowed_tools=tuple(
                sorted({tool for node in plan.nodes for tool in node.allowed_tools})
            ),
            policy_scope={"policy_version": request.policy_version},
            correlation_id=request.correlation_id,
            requested_by=f"principal:{self._principal.subject_id}",
            metadata={
                "capabilities": list(plan.capabilities),
                "adapter_kind": "langgraph",
                "adapter_command": command,
                "adapter_task_type": request.task_type,
                "adapter_payload": {
                    key: value
                    for key, value in request.payload.items()
                    if key != "execution_plan"
                },
                "execution_plan": plan.to_dict(),
                "tenant_parallel_limit": request.payload.get("tenant_parallel_limit", 4),
                "worker_parallel_limit": request.payload.get("worker_parallel_limit", 4),
                "input_data": dict(request.payload.get("input_data") or {}),
            },
        )
        return plan, workflow_request

    def _binding(self, operation_id: str) -> WorkflowControlRunBinding:
        binding = self._composition.bindings.get_by_run_id(str(operation_id))
        if binding is None or binding.runtime_id != "langgraph":
            raise WorkflowAdapterQueueError(
                "workflow_adapter_task_not_found", status_code=404
            )
        if (
            binding.tenant_id != self._principal.tenant_id
            or binding.subject_id != self._principal.subject_id
        ):
            raise WorkflowAdapterQueueError(
                "workflow_adapter_task_not_found", status_code=404
            )
        return binding

    def _route_envelope(
        self,
        binding: WorkflowControlRunBinding,
    ) -> dict[str, str]:
        return {
            "schema": ROUTE_CONTROL_AUTHORIZATION_SCHEMA,
            "tenant_id": binding.tenant_id,
            "subject_id": binding.subject_id,
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
        }

    @staticmethod
    def _assert_kind(kind: str) -> None:
        if str(kind or "").strip().lower() != "langgraph":
            raise WorkflowAdapterQueueError(
                "workflow_adapter_kind_unsupported", status_code=422
            )

    @staticmethod
    def _stream_descriptor(*, run_id: str, workflow_id: str) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_ADAPTER_STREAM_DESCRIPTOR_SCHEMA,
            "transport": "ndjson_post",
            "event_schema": "ananta.workflow_stream_frame.v1",
            "url": "/api/workflow_adapters/langgraph/stream",
            "request": {
                "schema": WORKFLOW_STREAM_REQUEST_SCHEMA,
                "hub_task_id": run_id,
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


def _steps_from_plan(plan: ExecutionPlan) -> tuple[WorkflowStepRequest, ...]:
    dependencies = {
        node.node_id: tuple(
            sorted(edge.source for edge in plan.edges if edge.target == node.node_id)
        )
        for node in plan.nodes
    }
    return tuple(
        WorkflowStepRequest(
            step_id=node.node_id,
            title=node.node_id,
            task_kind=node.task_kind,
            depends_on=dependencies[node.node_id],
            gate=bool(node.gate_id),
            allowed_tools=node.allowed_tools,
            policy_scope={"policy_version": plan.policy_version},
            input_artifacts=node.input_artifacts,
            output_artifacts=node.output_artifacts,
            metadata={
                **dict(node.metadata),
                "required_capabilities": list(node.required_capabilities),
                "side_effect_class": node.side_effect_class,
            },
        )
        for node in plan.nodes
    )


__all__ = [
    "AuthorizedUnifiedWorkflowAdapterControl",
    "UnifiedWorkflowAdapterControlFacade",
]
