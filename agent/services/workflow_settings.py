"""Workflow layer settings (WFG-003).

This module is the single source of truth for the workflow-layer
environment variables. The hub reads the values once at startup and
exposes them to the planner, worker, and queue layers.

Contract (mirrors docs/decisions/ADR-workflow-gates-blueprint-contract.md):

  ANANTA_WORKFLOW_MODE        deployment-wide kill switch
                              Values: off | auto | enforce
                              Default: auto
  ANANTA_WORKFLOW_DEFAULT_GATE  default gate policy when a blueprint
                              doesn't specify one
                              Values: block | skip | manual
                              Default: block
  ANANTA_WORKFLOW_GATE_TIMEOUT  how long a gate can stay pending
                              before the queue surfaces it as
                              'stale' (WFG-013)
                              Type: int seconds
                              Default: 86400 (24h)
  ANANTA_WORKFLOW_AUDIT_ENABLED  persist workflow-handoff events
                              (WFG-015)
                              Values: 0 | 1
                              Default: 1
  ANANTA_WORKFLOW_ARTIFACT_FLOW  enforce the produces/consumes DAG
                              between steps (WFG-016)
                              Values: 0 | 1
                              Default: 1

A blueprint's own `workflow.mode` is the per-blueprint override; the
deployment-level ANANTA_WORKFLOW_MODE is a multiplier on top of that:

  off     - workflow block is ignored even if present
  auto    - workflow block respected if validated; gated/strg_gated
            modes are enforced, off/direct are observed as intent
  enforce - same as auto, but the planner also rejects blueprints
            whose workflow block is malformed (belt-and-braces with
            the catalog normalizer)

WFG-021 documents the full backward-compat matrix with the legacy
planning-track materialization path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class WorkflowMode(str, Enum):
    """Deployment-wide workflow mode (ANANTA_WORKFLOW_MODE)."""

    OFF = "off"
    AUTO = "auto"
    ENFORCE = "enforce"

    @classmethod
    def parse(cls, raw: str | None) -> "WorkflowMode":
        if raw is None or raw == "":
            return cls.AUTO
        normalized = raw.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        valid = ", ".join(m.value for m in cls)
        raise ValueError(
            f"ANANTA_WORKFLOW_MODE={raw!r} is invalid; valid values: {valid}"
        )


class GateFailurePolicy(str, Enum):
    """Default failure policy for gates (ANANTA_WORKFLOW_DEFAULT_GATE)."""

    BLOCK = "block"
    SKIP = "skip"
    MANUAL = "manual"

    @classmethod
    def parse(cls, raw: str | None) -> "GateFailurePolicy":
        if raw is None or raw == "":
            return cls.BLOCK
        normalized = raw.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        valid = ", ".join(m.value for m in cls)
        raise ValueError(
            f"ANANTA_WORKFLOW_DEFAULT_GATE={raw!r} is invalid; valid values: {valid}"
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name}={raw!r} is not a valid boolean (0/1/true/false)")


def _env_int(name: str, default: int, *, min_value: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a valid integer") from exc
    if value < min_value:
        raise ValueError(f"{name}={value} must be >= {min_value}")
    return value


@dataclass(frozen=True)
class WorkflowSettings:
    """Resolved workflow-layer settings (read once at startup)."""

    mode: WorkflowMode
    default_gate_policy: GateFailurePolicy
    gate_timeout_seconds: int
    audit_enabled: bool
    artifact_flow_enforced: bool

    @classmethod
    def from_env(cls) -> "WorkflowSettings":
        return cls(
            mode=WorkflowMode.parse(os.environ.get("ANANTA_WORKFLOW_MODE")),
            default_gate_policy=GateFailurePolicy.parse(
                os.environ.get("ANANTA_WORKFLOW_DEFAULT_GATE")
            ),
            gate_timeout_seconds=_env_int(
                "ANANTA_WORKFLOW_GATE_TIMEOUT", 86400, min_value=0
            ),
            audit_enabled=_env_bool("ANANTA_WORKFLOW_AUDIT_ENABLED", True),
            artifact_flow_enforced=_env_bool(
                "ANANTA_WORKFLOW_ARTIFACT_FLOW", True
            ),
        )

    def workflow_block_respected(self) -> bool:
        """Whether the deployment allows the per-blueprint workflow block
        to take effect.

        OFF — never, even if a blueprint declares a workflow block.
        AUTO — yes for validated blocks; malformed ones are rejected by
               the catalog normalizer.
        ENFORCE — yes, plus the planner refuses to start a goal whose
                  blueprint declares a workflow block that fails DAG
                  validation at materialization time.
        """
        return self.mode != WorkflowMode.OFF

    def with_overrides(self, **overrides) -> "WorkflowSettings":
        """Return a copy with selected fields overridden. Test/operator helper."""
        from dataclasses import replace
        return replace(self, **overrides)


_workflow_settings_cache: WorkflowSettings | None = None


def get_workflow_settings(*, force_reload: bool = False) -> WorkflowSettings:
    """Read workflow settings from os.environ. Cached after first read.

    Pass force_reload=True to re-read (used by tests that mutate os.environ).
    """
    global _workflow_settings_cache
    if _workflow_settings_cache is None or force_reload:
        _workflow_settings_cache = WorkflowSettings.from_env()
    return _workflow_settings_cache


def reset_workflow_settings_cache() -> None:
    """Drop the cached settings. Tests call this after env mutations."""
    global _workflow_settings_cache
    _workflow_settings_cache = None
