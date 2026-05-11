"""WorkerSelectionPolicy schema and validation helpers.

DRR-T046: Structured worker selection policy for deterministic repair.
Supports fixed, automatic and policy_ranked selection modes across
native_ananta_worker, opencode, hermes, shellgpt, remote_worker, custom_worker.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

# ── Vocabulary ─────────────────────────────────────────────────────────────────

KNOWN_WORKER_KINDS: frozenset[str] = frozenset({
    "native_ananta_worker",
    "opencode",
    "hermes",
    "shellgpt",
    "remote_worker",
    "custom_worker",
    "disabled_placeholder",
})

KNOWN_SELECTION_MODES: frozenset[str] = frozenset({
    "fixed",
    "automatic",
    "policy_ranked",
})

KNOWN_FALLBACK_POLICIES: frozenset[str] = frozenset({
    "deny",
    "warn",
    "allow",
})

KNOWN_RISK_PROFILES: frozenset[str] = frozenset({
    "low",
    "balanced",
    "high",
    "strict",
    "bounded",
})

# Worker kinds that may send context to external/cloud systems
EXTERNAL_WORKER_KINDS: frozenset[str] = frozenset({
    "hermes",
    "remote_worker",
    "custom_worker",
})

CLOUD_WORKER_KINDS: frozenset[str] = frozenset({
    "hermes",
})

# Worker kinds appropriate for deterministic mutation repair
DETERMINISTIC_REPAIR_CAPABLE_KINDS: frozenset[str] = frozenset({
    "native_ananta_worker",
})

# Worker kinds that support analysis/proposal only (not mutation execution)
ANALYSIS_ONLY_KINDS: frozenset[str] = frozenset({
    "hermes",
    "shellgpt",
})


# ── Schema ─────────────────────────────────────────────────────────────────────

class WorkerSelectionPolicy(BaseModel):
    """Structured policy for selecting a worker backend during repair execution."""

    mode: str = Field(default="automatic", description="Selection mode: fixed, automatic, policy_ranked")
    fixed_worker_id: Optional[str] = Field(default=None)
    fixed_worker_kind: Optional[str] = Field(default=None)
    allowed_worker_kinds: list[str] = Field(default_factory=list)
    denied_worker_kinds: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    forbidden_capabilities: list[str] = Field(default_factory=list)
    prefer_local: bool = Field(default=True)
    allow_cloud: bool = Field(default=False)
    allow_external_workers: bool = Field(default=False)
    require_code_context: bool = Field(default=False)
    risk_profile: str = Field(default="balanced")
    fallback_policy: str = Field(default="deny")
    selection_reason_required: bool = Field(default=True)

    @model_validator(mode="after")
    def _validate_policy(self) -> "WorkerSelectionPolicy":
        if self.mode not in KNOWN_SELECTION_MODES:
            raise ValueError(f"Unknown selection mode '{self.mode}'. Allowed: {sorted(KNOWN_SELECTION_MODES)}")
        if self.mode == "fixed" and not self.fixed_worker_id and not self.fixed_worker_kind:
            raise ValueError("mode='fixed' requires fixed_worker_id or fixed_worker_kind")
        if self.fixed_worker_kind and self.fixed_worker_kind not in KNOWN_WORKER_KINDS:
            raise ValueError(f"Unknown fixed_worker_kind '{self.fixed_worker_kind}'")
        for kind in self.allowed_worker_kinds:
            if kind not in KNOWN_WORKER_KINDS:
                raise ValueError(f"Unknown worker kind '{kind}' in allowed_worker_kinds")
        for kind in self.denied_worker_kinds:
            if kind not in KNOWN_WORKER_KINDS:
                raise ValueError(f"Unknown worker kind '{kind}' in denied_worker_kinds")
        if self.risk_profile not in KNOWN_RISK_PROFILES:
            raise ValueError(f"Unknown risk_profile '{self.risk_profile}'")
        if self.fallback_policy not in KNOWN_FALLBACK_POLICIES:
            raise ValueError(f"Unknown fallback_policy '{self.fallback_policy}'")
        return self


# ── Preset factories ────────────────────────────────────────────────────────────

def strict_local_policy() -> WorkerSelectionPolicy:
    """Allow only native_ananta_worker on local runtimes. No cloud or external."""
    return WorkerSelectionPolicy(
        mode="automatic",
        allowed_worker_kinds=["native_ananta_worker"],
        prefer_local=True,
        allow_cloud=False,
        allow_external_workers=False,
        risk_profile="strict",
        fallback_policy="deny",
    )


def local_first_policy() -> WorkerSelectionPolicy:
    """Prefer local workers; allow remote only with explicit operator decision."""
    return WorkerSelectionPolicy(
        mode="automatic",
        allowed_worker_kinds=["native_ananta_worker", "opencode", "shellgpt"],
        prefer_local=True,
        allow_cloud=False,
        allow_external_workers=False,
        risk_profile="balanced",
        fallback_policy="warn",
    )


def external_analysis_only_policy() -> WorkerSelectionPolicy:
    """Allow Hermes/external for analysis/proposal; deny for mutation execution."""
    return WorkerSelectionPolicy(
        mode="automatic",
        allowed_worker_kinds=["native_ananta_worker", "opencode", "hermes"],
        denied_worker_kinds=["custom_worker"],
        prefer_local=True,
        allow_cloud=False,
        allow_external_workers=True,
        risk_profile="balanced",
        fallback_policy="deny",
    )


def cloud_allowed_with_approval_policy() -> WorkerSelectionPolicy:
    """Allow cloud workers but require approval gating; operator must explicitly confirm."""
    return WorkerSelectionPolicy(
        mode="policy_ranked",
        allowed_worker_kinds=list(KNOWN_WORKER_KINDS - {"disabled_placeholder"}),
        prefer_local=True,
        allow_cloud=True,
        allow_external_workers=True,
        risk_profile="high",
        fallback_policy="warn",
        selection_reason_required=True,
    )


# ── Validation helpers ─────────────────────────────────────────────────────────

def validate_worker_kind(kind: str) -> tuple[bool, str]:
    """Return (valid, reason_code). reason_code empty if valid."""
    if not kind or not isinstance(kind, str):
        return False, "unknown_worker_kind"
    if kind.strip().lower() not in KNOWN_WORKER_KINDS:
        return False, "unknown_worker_kind"
    return True, ""


def normalize_worker_selection_policy(raw: Any) -> WorkerSelectionPolicy:
    """Parse raw dict or existing policy into WorkerSelectionPolicy."""
    if isinstance(raw, WorkerSelectionPolicy):
        return raw
    if isinstance(raw, dict):
        return WorkerSelectionPolicy(**raw)
    return WorkerSelectionPolicy()


def is_cloud_worker_kind(kind: str) -> bool:
    return kind in CLOUD_WORKER_KINDS


def is_external_worker_kind(kind: str) -> bool:
    return kind in EXTERNAL_WORKER_KINDS


def is_mutation_capable(kind: str) -> bool:
    return kind in DETERMINISTIC_REPAIR_CAPABLE_KINDS


def policy_allows_kind(policy: WorkerSelectionPolicy, kind: str) -> tuple[bool, str]:
    """Check if policy allows the given worker kind. Returns (allowed, reason_code)."""
    if kind in policy.denied_worker_kinds:
        return False, "worker_kind_denied_by_policy"
    if policy.allowed_worker_kinds and kind not in policy.allowed_worker_kinds:
        return False, "worker_kind_not_in_allowlist"
    if is_cloud_worker_kind(kind) and not policy.allow_cloud:
        return False, "cloud_worker_denied_allow_cloud_false"
    if is_external_worker_kind(kind) and not policy.allow_external_workers:
        return False, "external_worker_denied_allow_external_false"
    return True, ""
