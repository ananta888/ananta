from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agent.common.audit import log_audit
from agent.common.redaction import redact
from agent.services.workflow_policy_adapter import decide_workflow_policy
from agent.services.workflow_registry import WorkflowRegistry


@dataclass
class WorkflowExecutionAdapter:
    registry: WorkflowRegistry
    providers: dict[str, Any]

    def execute(self, payload: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
        workflow_id = str(payload.get("workflow_id") or "").strip()
        descriptor = self.registry.get(workflow_id)
        if descriptor is None:
            return {"status": "blocked", "error": "unknown_workflow"}

        policy = decide_workflow_policy(descriptor, approved=approved)
        log_audit("workflow_policy_decision", {
            "workflow_id": workflow_id,
            "provider": descriptor.provider,
            "decision": policy.get("decision"),
            "task_id": payload.get("task_id"),
            "goal_id": payload.get("goal_id"),
            "trace_id": payload.get("trace_id"),
        })
        if policy.get("decision") != "allow":
            return {"status": "blocked", "policy": policy}

        provider = self.providers.get(descriptor.provider)
        if provider is None:
            return {"status": "degraded", "error": "provider_unavailable", "provider": descriptor.provider}

        correlation_id = str(payload.get("correlation_id") or uuid4())
        request = {
            "provider": descriptor.provider,
            "workflow_id": workflow_id,
            "task_id": payload.get("task_id"),
            "goal_id": payload.get("goal_id"),
            "trace_id": payload.get("trace_id"),
            "input_payload": payload.get("input_payload") or {},
            "dry_run": bool(payload.get("dry_run", True)),
            "correlation_id": correlation_id,
        }
        log_audit("workflow_execution_started", {
            "workflow_id": workflow_id,
            "provider": descriptor.provider,
            "correlation_id": correlation_id,
            "task_id": payload.get("task_id"),
            "goal_id": payload.get("goal_id"),
            "trace_id": payload.get("trace_id"),
        })

        try:
            result = dict(provider.execute(request) or {})
        except Exception as exc:  # noqa: BLE001
            redacted = redact({"error": str(exc)})
            log_audit("workflow_execution_failed", {
                "workflow_id": workflow_id,
                "provider": descriptor.provider,
                "correlation_id": correlation_id,
                "details": redacted,
            })
            return {"status": "degraded", "error": "execution_exception", "details": redacted, "correlation_id": correlation_id}

        result.setdefault("correlation_id", correlation_id)
        result["provider"] = descriptor.provider
        result["workflow_id"] = workflow_id
        result["input_payload"] = redact(request.get("input_payload") or {})

        log_audit("workflow_execution_finished", {
            "workflow_id": workflow_id,
            "provider": descriptor.provider,
            "correlation_id": correlation_id,
            "status": result.get("status"),
            "task_id": payload.get("task_id"),
            "goal_id": payload.get("goal_id"),
            "trace_id": payload.get("trace_id"),
        })
        return result
