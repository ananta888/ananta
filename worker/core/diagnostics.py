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
    # HF-T016: Hermes-specific adapter event types
    "adapter_parse_error",
    "adapter_unsafe_output",
    "remote_output_invalid",
    "routing_selected",
    # DRR-T033: Deterministic repair lifecycle events
    "repair_signature_matched",
    "repair_plan_generated",
    "repair_approval_required",
    "repair_step_started",
    "repair_step_completed",
    "repair_step_denied",
    "repair_verification_completed",
    "repair_outcome_persisted",
    "repair_escalated",
    "repair_rollback_proposed",
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


# ── AWF-T039: WorkerDiagnosticsReadModel ─────────────────────────────────────

_SENSITIVE_KEY_FRAGMENTS = frozenset({
    "api_key", "secret", "password", "token", "credential", "private",
})


@dataclass
class WorkerDiagnosticsReadModel:
    """Operator-safe worker readiness snapshot for dashboard/UI/TUI. AWF-T039.

    Never exposes provider keys, raw prompts, or sensitive context.
    """
    native_worker_enabled: bool
    worker_profiles: list[str]
    tool_registry_summary: dict[str, Any]
    provider_summary: dict[str, Any]
    skill_registry_summary: dict[str, Any]
    memory_policy_summary: dict[str, Any]
    context_policy_summary: dict[str, Any]
    last_degraded_reasons: list[str]
    enforcement_gates_active: dict[str, bool]

    def as_dict(self) -> dict[str, Any]:
        return {
            "native_worker_enabled": self.native_worker_enabled,
            "worker_profiles": list(self.worker_profiles),
            "tool_registry_summary": dict(self.tool_registry_summary),
            "provider_summary": dict(self.provider_summary),
            "skill_registry_summary": dict(self.skill_registry_summary),
            "memory_policy_summary": dict(self.memory_policy_summary),
            "context_policy_summary": dict(self.context_policy_summary),
            "last_degraded_reasons": list(self.last_degraded_reasons),
            "enforcement_gates_active": dict(self.enforcement_gates_active),
        }

    def has_secrets(self) -> bool:
        def _scan(obj: Any) -> bool:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if any(s in str(k).lower() for s in _SENSITIVE_KEY_FRAGMENTS):
                        return True
                    if _scan(v):
                        return True
            elif isinstance(obj, (list, tuple)):
                return any(_scan(item) for item in obj)
            return False
        return _scan(self.as_dict())


def build_worker_diagnostics_read_model(
    *,
    native_worker_enabled: bool = True,
    worker_profiles: list[str] | None = None,
    tool_registry: Any = None,
    provider_registry: Any = None,
    skill_registry: Any = None,
    memory_policy: dict[str, Any] | None = None,
    context_policy: dict[str, Any] | None = None,
    last_degraded_reasons: list[str] | None = None,
) -> WorkerDiagnosticsReadModel:
    """Build a read-model diagnostics snapshot without exposing secrets. AWF-T039."""
    tool_names = list(getattr(tool_registry, "_tools", {}).keys()) if tool_registry else []
    tool_summary: dict[str, Any] = {"registered_count": len(tool_names), "tool_ids": tool_names}

    providers = list(getattr(provider_registry, "_providers", {}).keys()) if provider_registry else []
    provider_summary: dict[str, Any] = {"provider_count": len(providers), "provider_ids": providers}

    if skill_registry is not None and hasattr(skill_registry, "list_diagnostics"):
        diag = skill_registry.list_diagnostics()
        skill_summary: dict[str, Any] = {
            "registered_count": len(diag),
            "enabled_count": sum(1 for d in diag if d.get("enabled")),
            "skill_ids": [d["id"] for d in diag],
        }
    else:
        skill_summary = {"registered_count": 0, "enabled_count": 0, "skill_ids": []}

    mem_summary: dict[str, Any] = {
        k: v for k, v in (memory_policy or {}).items()
        if k in {"enabled", "redact_before_persist", "default_memory_scope",
                 "archive_raw_output", "policy_version", "default_ttl_seconds", "retention_class"}
    }

    ctx_summary: dict[str, Any] = {
        k: v for k, v in (context_policy or {}).items()
        if not any(s in str(k).lower() for s in _SENSITIVE_KEY_FRAGMENTS)
    }

    enforcement_gates: dict[str, bool] = {
        "preflight_gate": True,
        "tool_registry_check": True,
        "resource_limit_enforcer": True,
        "context_sensitivity_filter": True,
        "memory_policy_gate": bool(memory_policy),
        "provider_selection_gate": provider_registry is not None,
        "skill_registry_gate": skill_registry is not None,
    }

    return WorkerDiagnosticsReadModel(
        native_worker_enabled=native_worker_enabled,
        worker_profiles=list(worker_profiles or ["fast", "balanced", "thorough"]),
        tool_registry_summary=tool_summary,
        provider_summary=provider_summary,
        skill_registry_summary=skill_summary,
        memory_policy_summary=mem_summary,
        context_policy_summary=ctx_summary,
        last_degraded_reasons=list(last_degraded_reasons or []),
        enforcement_gates_active=enforcement_gates,
    )


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
