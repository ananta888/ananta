from __future__ import annotations

from typing import Any

from agent.common.audit import log_audit
from agent.common.redaction import redact
from agent.services.audit_event_schema import (
    CANONICAL_AUDIT_EVENT_SCHEMA,
    build_canonical_audit_event,
    classify_context_classes,
)


EXECUTION_AUDIT_EVENT_SCHEMA = {
    "schema": "execution_audit_event.v2",
    "required_fields": list(CANONICAL_AUDIT_EVENT_SCHEMA["required_fields"]),
}


def _prompt_audit_policy() -> dict[str, Any]:
    return {
        "mode": "metadata_only_default",
        "default_store_prompt_content": False,
        "debug_store_prompt_content": False,
        "restricted_store_prompt_content": True,
        "redaction_required": True,
    }


class ExecutionAuditService:
    def emit(
        self,
        *,
        operation_type: str,
        outcome: str,
        trace_id: str | None,
        goal_id: str | None,
        task_id: str | None,
        actor: str = "system",
        actor_role: str = "system",
        policy_version: str = "unknown",
        target: dict[str, Any] | None = None,
        parent_trace_id: str | None = None,
        prompt_bundle_class: str | None = None,
        task_metadata: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        redacted_details = redact(details or {})
        context_classes = classify_context_classes(details=redacted_details, task_metadata=task_metadata)
        payload = build_canonical_audit_event(
            trace_id=trace_id,
            task_id=task_id,
            actor=actor,
            role=actor_role,
            policy_version=policy_version,
            operation_type=operation_type,
            target=target or {"goal_id": str(goal_id or "").strip() or None},
            outcome=outcome,
            details=redacted_details,
            parent_trace_id=parent_trace_id,
            context_classes=context_classes,
            prompt_bundle_class=prompt_bundle_class or str((task_metadata or {}).get("prompt_bundle_class") or "unknown"),
        )
        payload = {
            **payload,
            "goal_id": str(goal_id or "").strip() or None,
            "actor_role": str(actor_role or "system"),  # backward-compatible mirror
            "prompt_audit_policy": _prompt_audit_policy(),
        }
        log_audit("execution_audit_event", payload)

    def emit_tool_call(
        self,
        *,
        trace_id: str | None,
        parent_trace_id: str | None,
        tool_name: str,
        target_scope: dict[str, Any] | None,
        outcome: str,
        task_id: str | None = None,
        goal_id: str | None = None,
        actor_role: str = "system",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.emit(
            operation_type="tool_call",
            outcome=outcome,
            trace_id=trace_id,
            goal_id=goal_id,
            task_id=task_id,
            actor="agent",
            actor_role=actor_role,
            policy_version="kritis-audit-v1",
            target={
                "tool_name": str(tool_name or "").strip() or "unknown",
                "target_scope": dict(target_scope or {}),
            },
            parent_trace_id=parent_trace_id,
            details=details or {},
        )

    def schema(self) -> dict[str, Any]:
        return dict(EXECUTION_AUDIT_EVENT_SCHEMA)


_service = ExecutionAuditService()


def get_execution_audit_service() -> ExecutionAuditService:
    return _service
