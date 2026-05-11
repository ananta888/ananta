"""Worker audit event emitter for sensitive worker transitions. AWF-T040.

Audit events cover: preflight, tool invocations, provider calls, memory writes,
skill runs, skill proposals, subworker spawns, artifact persistence, result recording.

Events exclude raw secrets and full prompts.
Unknown event types raise ValueError (fail-closed per AWF-T006).
"""
from __future__ import annotations

import time
from typing import Any

WORKER_AUDIT_EVENTS = frozenset({
    "worker_preflight_decision",
    "worker_tool_invocation",
    "worker_provider_call",
    "worker_memory_write",
    "worker_skill_run",
    "worker_skill_proposal",
    "worker_subworker_spawn",
    "worker_artifact_persisted",
    "worker_result_recorded",
})

_SENSITIVE_KEYS = frozenset({
    "api_key", "secret", "password", "token", "credential",
    "raw_prompt", "full_prompt", "raw_output", "private_key",
})


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        k: "[REDACTED]" if any(s in str(k).lower() for s in _SENSITIVE_KEYS) else v
        for k, v in payload.items()
    }


def emit_worker_audit_event(
    *,
    event_type: str,
    task_id: str | None,
    goal_id: str | None = None,
    execution_id: str | None = None,
    policy_decision_ref: str | None = None,
    capability_snapshot_hash: str | None = None,
    reason_code: str | None = None,
    extra: dict[str, Any] | None = None,
    trace_port=None,
) -> dict[str, Any]:
    """Emit a worker audit event. AWF-T040.

    Raises ValueError for unknown event types (fail-closed).
    Strips sensitive keys from payload before emitting.
    """
    if event_type not in WORKER_AUDIT_EVENTS:
        raise ValueError(f"unknown_worker_audit_event:{event_type!r}")

    payload: dict[str, Any] = {
        "event_type": event_type,
        "task_id": task_id,
        "goal_id": goal_id,
        "execution_id": execution_id,
        "policy_decision_ref": policy_decision_ref,
        "capability_snapshot_hash": capability_snapshot_hash,
        "reason_code": reason_code,
        "emitted_at": time.time(),
    }
    if extra:
        payload.update(_scrub(extra))

    if trace_port is not None:
        trace_port.emit(event_type=event_type, payload=payload)

    return payload
