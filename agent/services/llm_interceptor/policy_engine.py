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

    def evaluate(self, *, envelope: dict[str, Any], upstream_trust_level: str) -> PolicyDecision:
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
                context_mode=str(self.cfg.get("cloud_context_default") or "redacted_minimal"),
            )

        reasons.append("local_allowed")
        return PolicyDecision(
            action="allow",
            reason_codes=reasons,
            cloud_allowed=True,
            context_mode=str(self.cfg.get("local_context_default") or "allowed_by_context_gate"),
        )

