"""Neutral workflow backend port.

The hub owns planning, routing and policy decisions.  Workflow backends
only execute an already validated WorkflowRequest and report status/events.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


WORKFLOW_REQUEST_SCHEMA = "ananta.workflow_request.v1"
WORKFLOW_STATUS_SCHEMA = "ananta.workflow_backend_status.v1"
WORKFLOW_EVENT_SCHEMA = "ananta.workflow_backend_event.v1"

WORKFLOW_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass(frozen=True)
class WorkflowStepRequest:
    step_id: str
    title: str = ""
    task_kind: str = "coding"
    role: str = ""
    depends_on: tuple[str, ...] = ()
    gate: bool = False
    allowed_tools: tuple[str, ...] = ()
    policy_scope: dict[str, Any] = field(default_factory=dict)
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "WorkflowStepRequest":
        step_id = str(raw.get("step_id") or raw.get("id") or "").strip()
        if not step_id:
            raise ValueError("workflow step requires step_id")
        return cls(
            step_id=step_id,
            title=str(raw.get("title") or raw.get("label") or step_id).strip(),
            task_kind=str(raw.get("task_kind") or raw.get("kind") or "coding").strip() or "coding",
            role=str(raw.get("role") or raw.get("role_name") or "").strip(),
            depends_on=tuple(str(v).strip() for v in list(raw.get("depends_on") or []) if str(v).strip()),
            gate=bool(raw.get("gate", False)),
            allowed_tools=tuple(str(v).strip() for v in list(raw.get("allowed_tools") or []) if str(v).strip()),
            policy_scope=dict(raw.get("policy_scope") or raw.get("policyScope") or {}),
            input_artifacts=tuple(str(v).strip() for v in list(raw.get("input_artifacts") or raw.get("consumes") or []) if str(v).strip()),
            output_artifacts=tuple(str(v).strip() for v in list(raw.get("output_artifacts") or raw.get("produces") or []) if str(v).strip()),
            metadata=dict(raw.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "task_kind": self.task_kind,
            "role": self.role,
            "depends_on": list(self.depends_on),
            "gate": self.gate,
            "allowed_tools": list(self.allowed_tools),
            "policy_scope": dict(self.policy_scope),
            "input_artifacts": list(self.input_artifacts),
            "output_artifacts": list(self.output_artifacts),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class WorkflowRequest:
    workflow_id: str
    workflow_type: str = "custom"
    goal_id: str = ""
    plan_id: str = ""
    blueprint_id: str = ""
    blueprint_version: str = ""
    steps: tuple[WorkflowStepRequest, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    policy_scope: dict[str, Any] = field(default_factory=dict)
    input_artifacts: tuple[str, ...] = ()
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    requested_by: str = "hub"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "WorkflowRequest":
        workflow_id = str(raw.get("workflow_id") or raw.get("workflowId") or "").strip()
        if not workflow_id:
            workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
        steps = tuple(WorkflowStepRequest.from_mapping(s) for s in list(raw.get("steps") or []))
        return cls(
            workflow_id=workflow_id,
            workflow_type=str(raw.get("workflow_type") or raw.get("workflowType") or "custom").strip() or "custom",
            goal_id=str(raw.get("goal_id") or raw.get("goalId") or "").strip(),
            plan_id=str(raw.get("plan_id") or raw.get("planId") or "").strip(),
            blueprint_id=str(raw.get("blueprint_id") or raw.get("blueprintId") or "").strip(),
            blueprint_version=str(raw.get("blueprint_version") or raw.get("blueprintVersion") or "").strip(),
            steps=steps,
            allowed_tools=tuple(str(v).strip() for v in list(raw.get("allowed_tools") or raw.get("allowedTools") or []) if str(v).strip()),
            policy_scope=dict(raw.get("policy_scope") or raw.get("policyScope") or {}),
            input_artifacts=tuple(str(v).strip() for v in list(raw.get("input_artifacts") or raw.get("inputArtifacts") or []) if str(v).strip()),
            correlation_id=str(raw.get("correlation_id") or raw.get("correlationId") or uuid.uuid4()).strip(),
            requested_by=str(raw.get("requested_by") or raw.get("requestedBy") or "hub").strip() or "hub",
            metadata=dict(raw.get("metadata") or {}),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.workflow_id:
            errors.append("workflow_id_required")
        if not self.steps:
            errors.append("steps_required")
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            errors.append("duplicate_step_id")
        known = set(ids)
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in known:
                    errors.append(f"unknown_dependency:{step.step_id}:{dep}")
            effective_scope = dict(self.policy_scope)
            effective_scope.update(step.policy_scope)
            if not effective_scope:
                errors.append(f"policy_scope_required:{step.step_id}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_REQUEST_SCHEMA,
            "workflow_id": self.workflow_id,
            "workflow_type": self.workflow_type,
            "goal_id": self.goal_id,
            "plan_id": self.plan_id,
            "blueprint_id": self.blueprint_id,
            "blueprint_version": self.blueprint_version,
            "steps": [step.to_dict() for step in self.steps],
            "allowed_tools": list(self.allowed_tools),
            "policy_scope": dict(self.policy_scope),
            "input_artifacts": list(self.input_artifacts),
            "correlation_id": self.correlation_id,
            "requested_by": self.requested_by,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class WorkflowSignal:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = "system"
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "WorkflowSignal":
        return cls(
            name=str(raw.get("name") or raw.get("signal") or "").strip(),
            payload=dict(raw.get("payload") or {}),
            actor=str(raw.get("actor") or "system").strip() or "system",
        )


class WorkflowBackend(Protocol):
    backend_id: str

    def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
        ...

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        ...

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> dict[str, Any]:
        ...

    def signal_workflow(self, workflow_id: str, signal: WorkflowSignal) -> dict[str, Any]:
        ...

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        ...


def workflow_backend_event(
    *,
    workflow_id: str,
    event_type: str,
    status: str = "",
    actor: str = "system",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": WORKFLOW_EVENT_SCHEMA,
        "event_id": f"wfe-{uuid.uuid4().hex[:16]}",
        "workflow_id": workflow_id,
        "event_type": event_type,
        "status": status,
        "actor": actor,
        "details": dict(details or {}),
        "timestamp": time.time(),
    }
