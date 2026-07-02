"""WorkerSelectionPolicy validation, presets and legacy normalization.

DRR-T046: Structured worker selection policy for deterministic repair.
The authoritative contract lives in ``worker.core.runtime_target``. This service
keeps convenient preset factories and legacy helpers used by Hub/API/UI code.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from worker.core.runtime_target import (
    FallbackPolicy,
    WorkerKind,
    WorkerSelectionMode,
    WorkerSelectionPolicy,
)

KNOWN_WORKER_KINDS: frozenset[str] = frozenset(kind.value for kind in WorkerKind)
KNOWN_SELECTION_MODES: frozenset[str] = frozenset(mode.value for mode in WorkerSelectionMode)
KNOWN_FALLBACK_POLICIES: frozenset[str] = frozenset(policy.value for policy in FallbackPolicy)
KNOWN_RISK_PROFILES: frozenset[str] = frozenset({"low", "balanced", "high", "strict", "bounded"})

EXTERNAL_WORKER_KINDS: frozenset[str] = frozenset({
    WorkerKind.hermes.value,
    WorkerKind.remote_worker.value,
    WorkerKind.custom_worker.value,
})
# Cloud is modeled at the runtime-target layer. Worker kinds themselves only
# encode whether they are external/non-local enough to need explicit allowance.
CLOUD_WORKER_KINDS: frozenset[str] = frozenset()
DETERMINISTIC_REPAIR_CAPABLE_KINDS: frozenset[str] = frozenset({WorkerKind.native_ananta_worker.value})
ANALYSIS_ONLY_KINDS: frozenset[str] = frozenset({WorkerKind.hermes.value, WorkerKind.shellgpt.value})

LEGACY_BACKEND_TO_WORKER_KIND: dict[str, WorkerKind] = {
    "native": WorkerKind.native_ananta_worker,
    "ananta": WorkerKind.native_ananta_worker,
    "ananta_worker": WorkerKind.native_ananta_worker,
    "native_ananta_worker": WorkerKind.native_ananta_worker,
    "opencode": WorkerKind.opencode,
    "open_code": WorkerKind.opencode,
    "hermes": WorkerKind.hermes,
    "hermes_agent": WorkerKind.hermes,
    "shellgpt": WorkerKind.shellgpt,
    "shell_gpt": WorkerKind.shellgpt,
    "remote": WorkerKind.remote_worker,
    "remote_worker": WorkerKind.remote_worker,
    "custom": WorkerKind.custom_worker,
}


class WorkerSelectionPolicyError(ValueError):
    """Raised when policy input cannot be normalized or validated."""


class WorkerSelectionPolicyService:
    """Build and validate ``WorkerSelectionPolicy`` instances.

    The service accepts the new nested ``worker_selection`` config as well as
    legacy fields such as ``preferred_backend``. It always returns the strict
    Pydantic contract from ``worker.core.runtime_target``.
    """

    def from_config(self, config: dict[str, Any] | WorkerSelectionPolicy | None) -> WorkerSelectionPolicy:
        if isinstance(config, WorkerSelectionPolicy):
            return config
        cfg = dict(config or {})
        if "worker_selection" in cfg and isinstance(cfg["worker_selection"], dict):
            cfg = dict(cfg["worker_selection"])
        elif "preferred_backend" in cfg and "mode" not in cfg:
            backend = str(cfg.get("preferred_backend") or "").strip().lower()
            kind = LEGACY_BACKEND_TO_WORKER_KIND.get(backend)
            if kind is None:
                raise WorkerSelectionPolicyError(f"unknown preferred_backend: {backend!r}")
            cfg = {
                "mode": WorkerSelectionMode.fixed.value,
                "fixed_worker_kind": kind.value,
                "allowed_worker_kinds": [kind.value],
                "fallback_policy": FallbackPolicy.deny.value,
                "allow_cloud": bool(cfg.get("allow_cloud", False)),
                "allow_external_workers": bool(cfg.get("allow_external_workers", False)),
                "risk_profile": cfg.get("risk_profile", "strict"),
            }
        try:
            return WorkerSelectionPolicy.model_validate(cfg)
        except ValidationError as exc:
            raise WorkerSelectionPolicyError(str(exc)) from exc

    def to_config(self, policy: WorkerSelectionPolicy) -> dict[str, Any]:
        return {"worker_selection": policy.model_dump(mode="json", exclude_none=True)}

    def validate_or_error(self, payload: dict[str, Any]) -> tuple[WorkerSelectionPolicy | None, str | None]:
        try:
            return self.from_config(payload), None
        except WorkerSelectionPolicyError as exc:
            return None, str(exc)


# ── Preset factories ────────────────────────────────────────────────────────────

def strict_local_policy() -> WorkerSelectionPolicy:
    """Allow only native_ananta_worker on local/private runtimes. No cloud/external."""
    return WorkerSelectionPolicy(
        mode=WorkerSelectionMode.automatic,
        allowed_worker_kinds=[WorkerKind.native_ananta_worker],
        prefer_local=True,
        allow_cloud=False,
        allow_external_workers=False,
        risk_profile="strict",
        fallback_policy=FallbackPolicy.deny,
    )


def local_first_policy() -> WorkerSelectionPolicy:
    """Prefer local workers and disallow cloud/external by default."""
    return WorkerSelectionPolicy(
        mode=WorkerSelectionMode.automatic,
        allowed_worker_kinds=[WorkerKind.native_ananta_worker, WorkerKind.opencode, WorkerKind.shellgpt],
        prefer_local=True,
        allow_cloud=False,
        allow_external_workers=False,
        risk_profile="balanced",
        fallback_policy=FallbackPolicy.same_or_lower_risk,
    )


def external_analysis_only_policy() -> WorkerSelectionPolicy:
    """Allow Hermes/external for analysis/proposal; not mutation execution."""
    return WorkerSelectionPolicy(
        mode=WorkerSelectionMode.automatic,
        allowed_worker_kinds=[WorkerKind.native_ananta_worker, WorkerKind.opencode, WorkerKind.hermes],
        denied_worker_kinds=[WorkerKind.custom_worker],
        prefer_local=True,
        allow_cloud=False,
        allow_external_workers=True,
        risk_profile="balanced",
        fallback_policy=FallbackPolicy.deny,
    )


def cloud_allowed_with_approval_policy() -> WorkerSelectionPolicy:
    """Cloud-capable preset for explicit approval-gated scenarios."""
    return WorkerSelectionPolicy(
        mode=WorkerSelectionMode.policy_ranked,
        allowed_worker_kinds=[kind for kind in WorkerKind if kind != WorkerKind.disabled_placeholder],
        prefer_local=True,
        allow_cloud=True,
        allow_external_workers=True,
        risk_profile="high",
        fallback_policy=FallbackPolicy.same_or_lower_risk,
        selection_reason_required=True,
    )


# ── Compatibility helpers ──────────────────────────────────────────────────────

def validate_worker_kind(kind: str) -> tuple[bool, str]:
    if not kind or not isinstance(kind, str):
        return False, "unknown_worker_kind"
    if kind.strip().lower() not in KNOWN_WORKER_KINDS:
        return False, "unknown_worker_kind"
    return True, ""


def normalize_worker_selection_policy(raw: Any) -> WorkerSelectionPolicy:
    return WorkerSelectionPolicyService().from_config(raw if isinstance(raw, dict) else None) if not isinstance(raw, WorkerSelectionPolicy) else raw


def is_cloud_worker_kind(kind: str) -> bool:
    return str(kind or "") in CLOUD_WORKER_KINDS


def is_external_worker_kind(kind: str) -> bool:
    return str(kind or "") in EXTERNAL_WORKER_KINDS


def is_mutation_capable(kind: str) -> bool:
    return str(kind or "") in DETERMINISTIC_REPAIR_CAPABLE_KINDS


def policy_allows_kind(policy: WorkerSelectionPolicy, kind: str) -> tuple[bool, str]:
    try:
        worker_kind = WorkerKind(str(kind or ""))
    except ValueError:
        return False, "unknown_worker_kind"
    if worker_kind in policy.denied_worker_kinds:
        return False, "worker_kind_denied_by_policy"
    if policy.allowed_worker_kinds and worker_kind not in policy.allowed_worker_kinds:
        return False, "worker_kind_not_in_allowlist"
    if is_cloud_worker_kind(worker_kind.value) and not policy.allow_cloud:
        return False, "cloud_worker_denied_allow_cloud_false"
    if is_external_worker_kind(worker_kind.value) and not policy.allow_external_workers:
        return False, "external_worker_denied_allow_external_false"
    return True, ""


# ── T05 — Token Budget Worker Gate ────────────────────────────────────────────

_EXPENSIVE_WORKER_PREFIXES = ("openai", "openrouter", "cloud", "remote", "gpt", "claude")
_EXPENSIVE_WORKER_KEYWORDS = frozenset({"openai", "openrouter", "cloud", "remote", "gpt4", "frontier"})


def _is_expensive_worker_name(worker_name: str) -> bool:
    name_lower = str(worker_name or "").lower()
    if any(name_lower.startswith(p) for p in _EXPENSIVE_WORKER_PREFIXES):
        return True
    return any(kw in name_lower for kw in _EXPENSIVE_WORKER_KEYWORDS)


def check_worker_allowed(
    *,
    worker_name: str,
    decision: Any,  # ContextBudgetDecision | None
) -> dict[str, Any]:
    """Determine whether a worker may be invoked given the current context budget decision.

    Args:
        worker_name: Identifier of the worker to check.
        decision: ContextBudgetDecision from context_budget_policy_service, or None.

    Returns:
        {"allowed": bool, "reason_code": str}
    """
    if decision is None:
        return {"allowed": True, "reason_code": "no_budget_gate_active"}

    mode = str(getattr(decision, "mode", "") or "")

    if mode == "safe_minimal_chat" and _is_expensive_worker_name(worker_name):
        return {
            "allowed": False,
            "reason_code": "safe_minimal_chat_blocks_expensive_workers",
        }

    return {"allowed": True, "reason_code": "worker_allowed_by_budget_policy"}


__all__ = [
    "ANALYSIS_ONLY_KINDS",
    "CLOUD_WORKER_KINDS",
    "DETERMINISTIC_REPAIR_CAPABLE_KINDS",
    "EXTERNAL_WORKER_KINDS",
    "KNOWN_FALLBACK_POLICIES",
    "KNOWN_RISK_PROFILES",
    "KNOWN_SELECTION_MODES",
    "KNOWN_WORKER_KINDS",
    "LEGACY_BACKEND_TO_WORKER_KIND",
    "WorkerSelectionPolicy",
    "WorkerSelectionPolicyError",
    "WorkerSelectionPolicyService",
    "cloud_allowed_with_approval_policy",
    "external_analysis_only_policy",
    "is_cloud_worker_kind",
    "is_external_worker_kind",
    "is_mutation_capable",
    "local_first_policy",
    "normalize_worker_selection_policy",
    "policy_allows_kind",
    "strict_local_policy",
    "validate_worker_kind",
    "check_worker_allowed",
]
