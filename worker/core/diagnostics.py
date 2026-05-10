"""Operator diagnostics for worker state.

EW-T052: worker_id, version, runtime_mode, tools, providers, skills, active capabilities,
          policy summary — never secrets.
EW-T053: Audit event emission for every sensitive step.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ── EW-T052: WorkerDiagnostics ────────────────────────────────────────────────

@dataclass
class WorkerDiagnostics:
    worker_id: str
    version: str
    runtime_mode: str           # "local", "headless", "development", "ci"
    registered_tools: list[str] = field(default_factory=list)
    registered_providers: list[dict[str, Any]] = field(default_factory=list)
    enabled_skills: list[str] = field(default_factory=list)
    active_capabilities: list[str] = field(default_factory=list)
    policy_summary: dict[str, Any] = field(default_factory=dict)
    queue_state: dict[str, Any] = field(default_factory=dict)
    last_health_errors: list[dict[str, Any]] = field(default_factory=list)
    degraded_subsystems: list[dict[str, Any]] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        """Safe serialization — no secrets, no raw credentials. EW-T052."""
        return {
            "worker_id": self.worker_id,
            "version": self.version,
            "runtime_mode": self.runtime_mode,
            "registered_tools": sorted(self.registered_tools),
            "registered_providers": self.registered_providers,
            "enabled_skills": sorted(self.enabled_skills),
            "active_capabilities": sorted(self.active_capabilities),
            "policy_summary": self.policy_summary,
            "queue_state": self.queue_state,
            "last_health_errors": self.last_health_errors,
            "degraded_subsystems": self.degraded_subsystems,
            "generated_at": self.generated_at,
        }


class WorkerDiagnosticsBuilder:
    """Assembles WorkerDiagnostics from worker subsystems. EW-T052."""

    def build(
        self,
        *,
        worker_id: str,
        version: str,
        runtime_mode: str,
        tool_registry: Any = None,
        provider_registry: Any = None,
        skill_registry: Any = None,
        envelope: Any = None,
        queue_state: dict[str, Any] | None = None,
        last_health_errors: list[dict[str, Any]] | None = None,
        degraded_subsystems: list[dict[str, Any]] | None = None,
    ) -> WorkerDiagnostics:
        tools = []
        if tool_registry:
            try:
                tools = [e["id"] for e in tool_registry.capability_catalog()]
            except Exception:
                pass

        providers = []
        if provider_registry:
            try:
                providers = provider_registry.provider_info()  # already safe (no secrets)
            except Exception:
                pass

        skills = []
        if skill_registry:
            try:
                skills = [e.manifest.id for e in skill_registry.enabled_skills()]
            except Exception:
                pass

        active_caps = []
        policy_summary: dict[str, Any] = {}
        if envelope:
            try:
                active_caps = list(envelope.capability_grant.capabilities)
                policy_summary = {
                    "cloud_allowed": envelope.model_policy.cloud_allowed,
                    "allowed_providers": envelope.model_policy.allowed_providers,
                    "tool_count": len(envelope.tool_policy.allowed_tool_ids),
                    "denied_operations": envelope.denied_operations,
                }
            except Exception:
                pass

        return WorkerDiagnostics(
            worker_id=worker_id,
            version=version,
            runtime_mode=runtime_mode,
            registered_tools=tools,
            registered_providers=providers,
            enabled_skills=skills,
            active_capabilities=active_caps,
            policy_summary=policy_summary,
            queue_state=dict(queue_state or {}),
            last_health_errors=list(last_health_errors or []),
            degraded_subsystems=list(degraded_subsystems or []),
        )


# ── EW-T053: AuditEmitter ─────────────────────────────────────────────────────

AUDITABLE_EVENTS = frozenset({
    "preflight_allow",
    "preflight_denied",
    "approval_required",
    "approval_consumed",
    "policy_denied",
    "shell_execute",
    "patch_apply",
    "file_write",
    "memory_write",
    "subworker_spawn",
    "cron_schedule",
    "artifact_publish",
    "provider_call",
    "provider_cloud_call",
    "mcp_call",
    "context_blocked",
    "injection_blocked",
    "capability_snapshot_mismatch",
})


@dataclass
class AuditEvent:
    event_type: str
    correlation_id: str
    reason_code: str | None
    ts: float = field(default_factory=time.time)
    actor_ref: str = ""
    task_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Safe serialization — no secrets or raw sensitive payloads. EW-T053."""
        return {
            "event_type": self.event_type,
            "correlation_id": self.correlation_id,
            "reason_code": self.reason_code,
            "ts": self.ts,
            "actor_ref": self.actor_ref,
            "task_id": self.task_id,
            "payload": _redact_payload(self.payload),
        }


class AuditEmitter:
    """Emits audit events for every sensitive step. EW-T053.

    All events include correlation_id and reason_code.
    Events are buffered in memory; caller flushes to persistent sink.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def emit(
        self,
        event_type: str,
        *,
        correlation_id: str,
        reason_code: str | None = None,
        actor_ref: str = "",
        task_id: str = "",
        **payload: Any,
    ) -> AuditEvent:
        if event_type not in AUDITABLE_EVENTS:
            # Unknown event types are still recorded but flagged
            payload["_unknown_event"] = True

        event = AuditEvent(
            event_type=event_type,
            correlation_id=correlation_id,
            reason_code=reason_code,
            actor_ref=actor_ref,
            task_id=task_id,
            payload=dict(payload),
        )
        self._events.append(event)
        return event

    def emit_preflight(
        self,
        decision: str,
        *,
        correlation_id: str,
        reason_code: str | None,
        task_id: str,
        actor_ref: str = "",
    ) -> AuditEvent:
        event_type = "preflight_allow" if decision == "allow" else "preflight_denied"
        return self.emit(
            event_type,
            correlation_id=correlation_id,
            reason_code=reason_code,
            task_id=task_id,
            actor_ref=actor_ref,
            decision=decision,
        )

    def emit_approval(
        self,
        action: str,    # "required" or "consumed"
        *,
        correlation_id: str,
        operation: str,
        task_id: str,
        ref_id: str = "",
    ) -> AuditEvent:
        event_type = "approval_required" if action == "required" else "approval_consumed"
        return self.emit(
            event_type,
            correlation_id=correlation_id,
            reason_code="approval_missing" if action == "required" else None,
            task_id=task_id,
            operation=operation,
            ref_id=ref_id,
        )

    def flush(self) -> list[dict[str, Any]]:
        """Return all buffered events as safe dicts and clear buffer."""
        events = [e.as_dict() for e in self._events]
        self._events.clear()
        return events

    def peek(self) -> list[AuditEvent]:
        """Non-destructive read."""
        return list(self._events)


def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove known-sensitive keys from audit event payload."""
    sensitive_keys = frozenset({
        "api_key", "secret", "token", "password", "credential",
        "private_key", "access_key",
    })
    return {
        k: "[REDACTED]" if k.lower() in sensitive_keys else v
        for k, v in payload.items()
    }
