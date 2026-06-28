"""Default in-process WorkflowBackend.

This backend intentionally does not execute worker business logic.  It records
validated workflow requests, exposes status, and models approval/cancel signals
so the hub can use the same port before a durable backend is enabled.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent.services.workflow_backend import (
    WORKFLOW_STATUS_SCHEMA,
    WorkflowRequest,
    WorkflowSignal,
    workflow_backend_event,
)
from agent.services.workflow_status_service import build_workflow_status


@dataclass
class _RunState:
    request: WorkflowRequest
    status: str = "pending"
    step_status: dict[str, str] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class LocalWorkflowBackend:
    backend_id = "local"

    def __init__(self) -> None:
        self._runs: dict[str, _RunState] = {}

    def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
        errors = request.validate()
        if errors:
            event = workflow_backend_event(
                workflow_id=request.workflow_id,
                event_type="workflow_rejected",
                status="failed",
                details={"errors": errors},
            )
            return {
                "schema": WORKFLOW_STATUS_SCHEMA,
                "backend": self.backend_id,
                "workflow_id": request.workflow_id,
                "status": "failed",
                "errors": errors,
                "events": [event],
            }

        state = _RunState(
            request=request,
            status="waiting_for_approval" if any(step.gate for step in request.steps) else "running",
            step_status={
                step.step_id: ("waiting_for_approval" if step.gate else "pending")
                for step in request.steps
            },
        )
        if state.status == "running":
            first_ready = next((step for step in request.steps if not step.gate), None)
            if first_ready is not None:
                state.step_status[first_ready.step_id] = "running"
        state.events.append(workflow_backend_event(
            workflow_id=request.workflow_id,
            event_type="workflow_started",
            status=state.status,
            details={"step_count": len(request.steps), "correlation_id": request.correlation_id},
        ))
        active_step = next((sid for sid, status in state.step_status.items() if status == "running"), "")
        if active_step:
            state.events.append(workflow_backend_event(
                workflow_id=request.workflow_id,
                event_type="step_started",
                status="running",
                details={"step_id": active_step},
            ))
        self._runs[request.workflow_id] = state
        return self.get_workflow_status(request.workflow_id)

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        state = self._runs.get(str(workflow_id or "").strip())
        if state is None:
            return {
                "schema": WORKFLOW_STATUS_SCHEMA,
                "backend": self.backend_id,
                "workflow_id": str(workflow_id or "").strip(),
                "status": "not_found",
                "steps": [],
                "events": [],
            }
        request = state.request
        status_payload = build_workflow_status(
            goal_id=request.goal_id,
            workflow_id=request.workflow_id,
            blueprint_id=request.blueprint_id,
            blueprint_version=request.blueprint_version,
            plan_id=request.plan_id,
            steps=[_status_step_dict(step, state.step_status.get(step.step_id, "pending")) for step in request.steps],
            tasks=[],
            include_audit_log=False,
        )
        status_payload.update({
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": self.backend_id,
            "workflow_request_schema": request.to_dict().get("schema"),
            "workflow_id": request.workflow_id,
            "status": state.status,
            "steps": [_status_step_dict(step, state.step_status.get(step.step_id, "pending")) for step in request.steps],
            "correlation_id": request.correlation_id,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "events": list(state.events),
        })
        return status_payload

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> dict[str, Any]:
        state = self._runs.get(str(workflow_id or "").strip())
        if state is None:
            return self.get_workflow_status(workflow_id)
        state.status = "cancelled"
        state.updated_at = time.time()
        for step_id in list(state.step_status):
            if state.step_status[step_id] not in {"completed", "failed", "cancelled"}:
                state.step_status[step_id] = "cancelled"
        state.events.append(workflow_backend_event(
            workflow_id=state.request.workflow_id,
            event_type="workflow_cancelled",
            status=state.status,
            details={"reason": reason},
        ))
        return self.get_workflow_status(workflow_id)

    def signal_workflow(self, workflow_id: str, signal: WorkflowSignal) -> dict[str, Any]:
        state = self._runs.get(str(workflow_id or "").strip())
        if state is None:
            return self.get_workflow_status(workflow_id)
        state.events.append(workflow_backend_event(
            workflow_id=state.request.workflow_id,
            event_type=f"signal:{signal.name}",
            status=state.status,
            actor=signal.actor,
            details=signal.payload,
        ))
        if signal.name == "approve":
            for step_id, status in list(state.step_status.items()):
                if status == "waiting_for_approval":
                    state.step_status[step_id] = "pending"
            state.status = "running"
        elif signal.name == "reject":
            state.status = "failed"
            for step_id, status in list(state.step_status.items()):
                if status == "waiting_for_approval":
                    state.step_status[step_id] = "failed"
        state.updated_at = time.time()
        return self.get_workflow_status(workflow_id)

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        state = self._runs.get(str(workflow_id or "").strip())
        return list(state.events) if state else []


def _status_step_dict(step, status: str) -> dict[str, Any]:
    routing = dict((step.metadata or {}).get("model_routing") or {})
    return {
        "id": step.step_id,
        "step_id": step.step_id,
        "role": step.role,
        "task_kind": step.task_kind,
        "gate": step.gate,
        "consumes": list(step.input_artifacts),
        "status": status,
        "selected_model_profile_id": routing.get("preferred_profile_id"),
        "selected_provider_id": None,
        "selected_model": None,
        "fallback_attempts": [],
        "llm_call_profile": list((step.metadata or {}).get("llm_call_profile") or []),
        "model_routing": routing,
    }


local_workflow_backend = LocalWorkflowBackend()
