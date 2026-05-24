from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason_codes: list[str]
    cloud_allowed: bool
    context_mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason_codes": list(self.reason_codes),
            "cloud_allowed": self.cloud_allowed,
            "context_mode": self.context_mode,
        }


class PolicyEngine:
    """Deterministic interceptor policy classification (MVP)."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = dict(cfg or {})
        self.profiles = dict(self.cfg.get("profiles") or {})
        self.active_profile = str(self.cfg.get("active_profile") or "cloud_safe")

    def _resolve_profile(self, envelope: dict[str, Any]) -> dict[str, Any]:
        # Profile selection is metadata/config-driven, never prompt-text driven.
        task_meta = dict(envelope.get("task_metadata") or {})
        selected = str(task_meta.get("policy_profile") or self.active_profile).strip()
        return dict(self.profiles.get(selected) or self.profiles.get(self.active_profile) or {})

    def evaluate(self, *, envelope: dict[str, Any], upstream_trust_level: str) -> PolicyDecision:
        profile = self._resolve_profile(envelope)
        task_kind = str((envelope.get("task_metadata") or {}).get("task_kind") or "").strip().lower()
        model = str(envelope.get("model") or "").strip().lower()
        cloud = str(upstream_trust_level or "").strip().lower() == "cloud"
        reasons: list[str] = []

        if cloud and any(x in model for x in ("secret", "credential", "private")):
            return PolicyDecision(
                action="deny",
                reason_codes=["cloud_high_risk_model_denied"],
                cloud_allowed=False,
                context_mode="none",
            )

        if cloud and task_kind in {"security", "secrets", "incident_response"}:
            return PolicyDecision(
                action="local_only",
                reason_codes=["high_risk_task_local_only"],
                cloud_allowed=False,
                context_mode="none",
            )

        if cloud:
            reasons.append("cloud_context_reduced")
            return PolicyDecision(
                action="reduce_context",
                reason_codes=reasons,
                cloud_allowed=True,
                context_mode=str(profile.get("cloud_context_default") or self.cfg.get("cloud_context_default") or "redacted_minimal"),
            )

        reasons.append("local_allowed")
        return PolicyDecision(
            action="allow",
            reason_codes=reasons,
            cloud_allowed=True,
            context_mode=str(profile.get("local_context_default") or self.cfg.get("local_context_default") or "allowed_by_context_gate"),
        )
