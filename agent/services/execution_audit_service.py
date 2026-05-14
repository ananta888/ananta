from __future__ import annotations

import time
from typing import Any

from agent.common.audit import log_audit
from agent.common.redaction import redact


EXECUTION_AUDIT_EVENT_SCHEMA = {
    "schema": "execution_audit_event.v1",
    "required_fields": [
        "trace_id",
        "goal_id",
        "task_id",
        "actor_role",
        "operation_type",
        "outcome",
        "timestamp",
    ],
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
        actor_role: str = "system",
        details: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "schema": EXECUTION_AUDIT_EVENT_SCHEMA["schema"],
            "trace_id": str(trace_id or "").strip() or None,
            "goal_id": str(goal_id or "").strip() or None,
            "task_id": str(task_id or "").strip() or None,
            "actor_role": str(actor_role or "system"),
            "operation_type": str(operation_type or "unknown"),
            "outcome": str(outcome or "unknown"),
            "timestamp": time.time(),
            "prompt_audit_policy": _prompt_audit_policy(),
            "details": redact(details or {}),
        }
        log_audit("execution_audit_event", payload)

    def schema(self) -> dict[str, Any]:
        return dict(EXECUTION_AUDIT_EVENT_SCHEMA)


_service = ExecutionAuditService()


def get_execution_audit_service() -> ExecutionAuditService:
    return _service

