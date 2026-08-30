"""Closed contracts for Hub-owned agent safety controls."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class SafetyMode(StrEnum):
    ENFORCE = "enforce"
    OBSERVE_ONLY = "observe_only"
    ADVERSARIAL_EVAL = "adversarial_eval"
    DISABLED = "disabled"


class SafetyAction(StrEnum):
    FREEZE = "freeze"
    TERMINATE = "terminate"
    ISOLATE = "isolate"


class StopScope(StrEnum):
    AGENT = "agent"
    SANDBOX = "sandbox"
    RUN = "run"
    GROUP = "group"


class BoundaryClass(StrEnum):
    FILESYSTEM = "filesystem"
    NETWORK = "network"
    PRIVILEGE = "privilege"
    PROCESS = "process"
    ORCHESTRATION = "orchestration"


class TriggerClass(StrEnum):
    OPAQUE_PRIORITY = "opaque_priority"
    SHUTDOWN = "shutdown"
    BOUNDARY_BREACHED = "boundary_breached"
    SUCCESS_REPORT = "success_report"
    TRACE_CHECKPOINT = "trace_checkpoint"


class RuntimeEventType(StrEnum):
    TOOL_CALL = "tool_call"
    POLICY_DECISION = "policy_decision"
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    PROCESS = "process"
    ORCHESTRATION = "orchestration"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require_token(value: object, field: str) -> str:
    candidate = str(value or "").strip()
    if not _TOKEN.fullmatch(candidate):
        raise ValueError(f"agent_safety_{field}_invalid")
    return candidate


def canonical_digest(payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    policy_id: str
    revision: int
    mode: SafetyMode
    preventive_policy_enabled: bool
    preventive_training_enabled: bool
    telemetry_enabled: bool
    external_kill_switch_enabled: bool
    incident_freeze_enabled: bool
    sentinel_enabled: bool = True
    adversarial_evaluation_enabled: bool = False
    adversarial_scope: tuple[str, ...] = ()
    global_stop_scope: StopScope = StopScope.RUN
    max_parallel_agents: int = 1
    max_trace_events: int = 10_000
    freeze_ttl_seconds: int = 900
    max_snapshot_bytes: int = 262_144

    def __post_init__(self) -> None:
        require_token(self.policy_id, "policy_id")
        if self.revision < 1:
            raise ValueError("agent_safety_policy_revision_invalid")
        if not self.telemetry_enabled or not self.external_kill_switch_enabled:
            raise ValueError("agent_safety_mandatory_controls_disabled")
        if self.mode == SafetyMode.ADVERSARIAL_EVAL:
            if not self.adversarial_scope or any(not item.startswith("local:") for item in self.adversarial_scope):
                raise ValueError("agent_safety_adversarial_scope_not_local")
            if not self.adversarial_evaluation_enabled:
                raise ValueError("agent_safety_adversarial_evaluation_not_enabled")
        if not 1 <= self.max_parallel_agents <= 100:
            raise ValueError("agent_safety_parallelism_out_of_bounds")
        if not 100 <= self.max_trace_events <= 100_000:
            raise ValueError("agent_safety_trace_budget_out_of_bounds")
        if not 30 <= self.freeze_ttl_seconds <= 86_400:
            raise ValueError("agent_safety_freeze_ttl_out_of_bounds")
        if not 1_024 <= self.max_snapshot_bytes <= 1_048_576:
            raise ValueError("agent_safety_snapshot_budget_out_of_bounds")

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["mode"] = self.mode.value
        value["global_stop_scope"] = self.global_stop_scope.value
        value["adversarial_scope"] = list(self.adversarial_scope)
        return value


@dataclass(frozen=True, slots=True)
class SentinelManifest:
    manifest_id: str
    tenant_id: str
    project_id: str
    run_id: str
    sandbox_id: str
    trigger_id: str
    trigger_class: TriggerClass
    policy_id: str
    policy_revision: int
    policy_mode: SafetyMode
    manifest_version: int
    nonce: str
    issued_at: str
    expires_at: str
    effect: SafetyAction
    priority: int
    visibility: str
    signature: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger_class", TriggerClass(str(self.trigger_class)))
        object.__setattr__(self, "effect", SafetyAction(str(self.effect)))
        object.__setattr__(self, "policy_mode", SafetyMode(str(self.policy_mode)))
        for field in (
            "manifest_id",
            "tenant_id",
            "project_id",
            "run_id",
            "sandbox_id",
            "trigger_id",
            "policy_id",
            "nonce",
        ):
            require_token(getattr(self, field), field)
        if self.visibility not in {"opaque", "open"}:
            raise ValueError("agent_safety_trigger_visibility_invalid")
        if self.policy_revision < 1:
            raise ValueError("agent_safety_policy_revision_invalid")
        if self.manifest_version != 1:
            raise ValueError("agent_safety_manifest_version_unsupported")
        if not 1 <= self.priority <= 100:
            raise ValueError("agent_safety_trigger_priority_invalid")

    def unsigned_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature", None)
        value["trigger_class"] = self.trigger_class.value
        value["effect"] = self.effect.value
        value["policy_mode"] = self.policy_mode.value
        return value

    def sign(self, key: bytes) -> "SentinelManifest":
        signature = hmac.new(key, canonical_digest(self.unsigned_payload()).encode(), hashlib.sha256).hexdigest()
        return replace(self, signature=signature)

    def verify(self, key: bytes, *, now: str, expected_run_id: str, expected_sandbox_id: str) -> None:
        expected = hmac.new(key, canonical_digest(self.unsigned_payload()).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, self.signature):
            raise ValueError("agent_safety_trigger_signature_invalid")
        if self.run_id != expected_run_id or self.sandbox_id != expected_sandbox_id:
            raise ValueError("agent_safety_trigger_binding_mismatch")
        if now < self.issued_at or now >= self.expires_at:
            raise ValueError("agent_safety_trigger_expired")

    def as_dict(self) -> dict[str, Any]:
        return {**self.unsigned_payload(), "signature": self.signature}


@dataclass(frozen=True, slots=True)
class SafetyEvent:
    event_id: str
    tenant_id: str
    project_id: str
    run_id: str
    sandbox_id: str
    agent_id: str
    event_type: str
    severity: str
    source: str
    observed_at: str
    details: Mapping[str, Any]
    previous_digest: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "event_id",
            "tenant_id",
            "project_id",
            "run_id",
            "sandbox_id",
            "agent_id",
            "event_type",
            "source",
        ):
            require_token(getattr(self, field), field)
        if self.severity not in {"info", "warning", "high", "critical"}:
            raise ValueError("agent_safety_severity_invalid")
        rendered = json.dumps(dict(self.details), allow_nan=False)
        if len(rendered.encode("utf-8")) > 16_384:
            raise ValueError("agent_safety_event_details_too_large")

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["details"] = _redact(dict(self.details))
        value["event_digest"] = canonical_digest(value)
        return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(term in str(key).lower() for term in ("secret", "token", "password", "credential"))
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


__all__ = [
    "BoundaryClass",
    "SafetyAction",
    "SafetyEvent",
    "SafetyMode",
    "SafetyPolicy",
    "SentinelManifest",
    "StopScope",
    "TriggerClass",
    "RuntimeEventType",
    "canonical_digest",
    "require_token",
    "utc_now",
]
