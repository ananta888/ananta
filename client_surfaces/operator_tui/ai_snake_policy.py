from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from worker.core.redaction import redact_payload


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    boundary: str
    reason_code: str
    decision_ref: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "boundary": self.boundary,
            "reason_code": self.reason_code,
            "decision_ref": self.decision_ref,
        }


def evaluate_policy(
    *,
    boundary: str,
    notes_released: bool,
    selected_artifact_allowed: bool = True,
    external_provider: bool = False,
) -> PolicyDecision:
    boundary_key = str(boundary or "").strip().lower() or "local_observation"
    allowed = True
    reason = "allowed"
    if boundary_key == "external_provider" and external_provider:
        allowed = False
        reason = "external_provider_denied"
    elif boundary_key in {"worker_request", "lmstudio_prompt"} and not selected_artifact_allowed:
        allowed = False
        reason = "artifact_denied"
    elif boundary_key in {"worker_request", "lmstudio_prompt", "external_provider"} and not notes_released:
        reason = "notes_metadata_only"
    decision_ref = _decision_ref(boundary_key, reason)
    return PolicyDecision(allowed=allowed, boundary=boundary_key, reason_code=reason, decision_ref=decision_ref)


def apply_policy_to_payload(
    payload: dict[str, Any],
    *,
    boundary: str,
    notes_released: bool,
    selected_artifact_allowed: bool = True,
    external_provider: bool = False,
) -> tuple[dict[str, Any], PolicyDecision]:
    decision = evaluate_policy(
        boundary=boundary,
        notes_released=notes_released,
        selected_artifact_allowed=selected_artifact_allowed,
        external_provider=external_provider,
    )
    sanitized = redact_payload(payload)
    if not decision.allowed:
        return (
            {
                "blocked": True,
                "reason_code": decision.reason_code,
                "decision_ref": decision.decision_ref,
            },
            decision,
        )
    if not notes_released:
        if isinstance(sanitized, dict):
            sanitized.pop("notes_context", None)
            summary = sanitized.get("observation_summary")
            if isinstance(summary, dict):
                summary["notes_active"] = bool(summary.get("notes_active"))
    return sanitized, decision


def _decision_ref(boundary: str, reason: str) -> str:
    digest = hashlib.sha1(f"{boundary}:{reason}".encode("utf-8")).hexdigest()[:10]
    return f"ai-pol-{digest}"
