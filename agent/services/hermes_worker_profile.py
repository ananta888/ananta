from __future__ import annotations

from dataclasses import dataclass
from typing import Any


HERMES_ALLOWED_CAPABILITIES: tuple[str, ...] = (
    "planning",
    "summarize",
    "research_limited",
    "review",        # canonical public name (HF-T002)
    "code_review",   # alias — kept for backwards compat (HF-T002)
    "patch_propose",
)

HERMES_DENIED_CAPABILITIES: tuple[str, ...] = (
    "patch_apply",
    "shell_execute",
    "shell_execution",  # legacy alias — both forms denied (HF-T003)
    "file_write",
    "service_mutation",
    "config_mutation",
    "memory_write",
    "cron_schedule",
    "unrestricted_network",
    "mcp_call",
)

# Canonical mapping: legacy/alias → canonical name (HF-T003)
_CAPABILITY_ALIASES: dict[str, str] = {
    "shell_execution": "shell_execute",
    "code_review": "review",
}


def normalize_capability(capability: str) -> str:
    """Map legacy/alias capability names to their canonical form."""
    normalized = str(capability or "").strip().lower()
    return _CAPABILITY_ALIASES.get(normalized, normalized)

HERMES_ALLOWED_TASK_KINDS: tuple[str, ...] = (
    "plan_only",
    "review",
    "summarize",
    "patch_propose",
)

HERMES_BLOCKED_TASK_KINDS: tuple[str, ...] = (
    "patch_apply",
    "command_execute",
    "service_mutation",
    "config_mutation",
)


@dataclass(frozen=True)
class HermesWorkerCapabilityProfile:
    profile_id: str = "hermes_phase1"
    risk_class: str = "medium"
    requires_structured_output: bool = True
    max_context_policy: str = "bounded"
    default_cloud_allowed: bool = False
    allowed_capabilities: tuple[str, ...] = HERMES_ALLOWED_CAPABILITIES
    denied_capabilities: tuple[str, ...] = HERMES_DENIED_CAPABILITIES
    allowed_task_kinds: tuple[str, ...] = HERMES_ALLOWED_TASK_KINDS
    blocked_task_kinds: tuple[str, ...] = HERMES_BLOCKED_TASK_KINDS
    phase: str = "phase1"

    def supports_capability(self, capability: str) -> bool:
        normalized = normalize_capability(capability)
        if not normalized:
            return False
        if normalized in self.denied_capabilities:
            return False
        # Also check canonical form after alias resolution
        canonical = normalize_capability(normalized)
        if canonical in self.denied_capabilities:
            return False
        return normalized in self.allowed_capabilities or canonical in self.allowed_capabilities

    def is_task_kind_allowed(self, task_kind: str) -> bool:
        normalized = str(task_kind or "").strip().lower()
        if not normalized:
            return False
        if normalized in self.blocked_task_kinds:
            return False
        return normalized in self.allowed_task_kinds

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "risk_class": self.risk_class,
            "requires_structured_output": self.requires_structured_output,
            "max_context_policy": self.max_context_policy,
            "default_cloud_allowed": self.default_cloud_allowed,
            "allowed_capabilities": list(self.allowed_capabilities),
            "denied_capabilities": list(self.denied_capabilities),
            "allowed_task_kinds": list(self.allowed_task_kinds),
            "blocked_task_kinds": list(self.blocked_task_kinds),
            "phase": self.phase,
        }


def get_default_hermes_profile() -> HermesWorkerCapabilityProfile:
    return HermesWorkerCapabilityProfile()
