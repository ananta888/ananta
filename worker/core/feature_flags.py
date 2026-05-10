"""Rollout feature flags and migration plan helpers.

EW-T062: FeatureFlag registry, runtime evaluation, and migration stage tracking.
         Flags control incremental rollout of governed executor features.
         Hub controls flag state; worker never sets flags independently.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── MigrationStage ────────────────────────────────────────────────────────────

class MigrationStage(str, Enum):
    """Three-stage deprecation path from legacy bare mode to full governed execution."""
    legacy = "legacy"             # bare mode strings, no envelope
    compatibility = "compatibility"  # LegacyEnvelopeAdapter wraps bare modes
    governed = "governed"         # full ExecutionEnvelope required


# ── Flag definitions ──────────────────────────────────────────────────────────

KNOWN_FLAGS: frozenset[str] = frozenset({
    # Core execution flags
    "require_execution_envelope",      # reject any task without valid ExecutionEnvelope
    "require_capability_snapshot",     # enforce snapshot_hash integrity check
    "require_artifact_first",          # reject free-text-only WorkerResult
    # Security flags
    "enable_context_scanner",          # run ContextScanner on all incoming blocks
    "enable_adapter_trust_boundary",   # run AdapterTrustBoundary on all adapter output
    "enable_audit_emitter",            # emit structured audit events
    "block_cloud_by_default",          # ModelPolicy.cloud_allowed defaults False
    # Feature rollout flags
    "enable_skill_system",             # SkillRegistry + SkillRunner active
    "enable_subworker_spawn",          # SubworkerSpawnGate active
    "enable_scheduled_jobs",           # ScheduledJobContract processing active
    "enable_api_exposure",             # ApiExposurePolicy can be non-disabled
    "enable_trace_v2",                 # TraceBundleV2 emitted instead of legacy TraceBundle
    "enable_hermes_worker_adapter",    # Hermes external worker adapter routing/registry
    # Migration flags
    "legacy_envelope_adapter_allowed", # allow LegacyEnvelopeAdapter fallback
    "strict_tool_policy",              # deny tools not in ToolPolicy.allowed_tool_ids
})


@dataclass(frozen=True)
class FeatureFlag:
    """A single feature flag with metadata."""
    name: str
    default_enabled: bool
    description: str
    migration_stage: MigrationStage = MigrationStage.governed
    rollout_percentage: int = 100    # 0-100; 100 = all workers; Hub-controlled

    def __post_init__(self):
        if self.name not in KNOWN_FLAGS:
            raise ValueError(f"Unknown flag {self.name!r}; register it in KNOWN_FLAGS first")
        if not 0 <= self.rollout_percentage <= 100:
            raise ValueError("rollout_percentage must be 0–100")


# ── Default flag catalogue ────────────────────────────────────────────────────

DEFAULT_FLAGS: list[FeatureFlag] = [
    FeatureFlag("require_execution_envelope", True,
                "Reject any task without valid ExecutionEnvelope",
                MigrationStage.governed),
    FeatureFlag("require_capability_snapshot", True,
                "Enforce snapshot_hash integrity check on every envelope",
                MigrationStage.governed),
    FeatureFlag("require_artifact_first", True,
                "Reject free-text-only WorkerResult when artifacts expected",
                MigrationStage.governed),
    FeatureFlag("enable_context_scanner", True,
                "Run ContextScanner on all incoming context blocks",
                MigrationStage.governed),
    FeatureFlag("enable_adapter_trust_boundary", True,
                "Run AdapterTrustBoundary on all external adapter output",
                MigrationStage.governed),
    FeatureFlag("enable_audit_emitter", True,
                "Emit structured audit events for all sensitive steps",
                MigrationStage.governed),
    FeatureFlag("block_cloud_by_default", True,
                "ModelPolicy.cloud_allowed defaults to False",
                MigrationStage.governed),
    FeatureFlag("enable_skill_system", True,
                "SkillRegistry and SkillRunner active",
                MigrationStage.governed),
    FeatureFlag("enable_subworker_spawn", True,
                "SubworkerSpawnGate active — subworker_spawn capability enforced",
                MigrationStage.governed),
    FeatureFlag("enable_scheduled_jobs", False,
                "ScheduledJobContract processing active (opt-in)",
                MigrationStage.governed),
    FeatureFlag("enable_api_exposure", False,
                "ApiExposurePolicy can be set to non-disabled (opt-in)",
                MigrationStage.governed),
    FeatureFlag("enable_trace_v2", True,
                "Emit TraceBundleV2 instead of legacy TraceBundle",
                MigrationStage.governed),
    FeatureFlag("enable_hermes_worker_adapter", False,
                "Enable Hermes external worker adapter visibility and routing (opt-in)",
                MigrationStage.governed),
    FeatureFlag("legacy_envelope_adapter_allowed", True,
                "Allow LegacyEnvelopeAdapter fallback during migration",
                MigrationStage.compatibility),
    FeatureFlag("strict_tool_policy", False,
                "Deny tools not in ToolPolicy.allowed_tool_ids (enables after migration)",
                MigrationStage.governed),
]


# ── FeatureFlagRegistry ───────────────────────────────────────────────────────

class FeatureFlagRegistry:
    """Runtime feature flag evaluation. Hub-controlled; worker reads only. EW-T062."""

    def __init__(self, flags: list[FeatureFlag] | None = None) -> None:
        self._flags: dict[str, FeatureFlag] = {}
        self._overrides: dict[str, bool] = {}
        for flag in (flags or DEFAULT_FLAGS):
            self._flags[flag.name] = flag

    def is_enabled(self, flag_name: str) -> bool:
        """Evaluate whether a flag is enabled.

        Override wins over default. Unknown flags → False (fail-closed).
        """
        if flag_name not in self._flags:
            return False
        if flag_name in self._overrides:
            return self._overrides[flag_name]
        return self._flags[flag_name].default_enabled

    def apply_hub_config(self, config: dict[str, bool]) -> None:
        """Apply a flag configuration dict from the Hub.

        Only known flags are accepted — unknown keys are silently ignored
        (fail-closed: worker never infers permissions from unknown config keys).
        """
        for name, value in config.items():
            if name in self._flags:
                self._overrides[name] = bool(value)

    def migration_stage(self) -> MigrationStage:
        """Derive current effective migration stage from flag state."""
        if self.is_enabled("require_execution_envelope"):
            return MigrationStage.governed
        if self.is_enabled("legacy_envelope_adapter_allowed"):
            return MigrationStage.compatibility
        return MigrationStage.legacy

    def snapshot(self) -> dict[str, Any]:
        """Current flag state snapshot for diagnostics (no secrets)."""
        return {
            name: self.is_enabled(name)
            for name in sorted(self._flags)
        }

    def flags_for_stage(self, stage: MigrationStage) -> list[FeatureFlag]:
        """Return all flags relevant to a given migration stage."""
        return [f for f in self._flags.values() if f.migration_stage == stage]


def build_default_registry() -> FeatureFlagRegistry:
    return FeatureFlagRegistry(DEFAULT_FLAGS)
