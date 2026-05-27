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

    def to_decision_result(self) -> Any:
        from agent.services.heuristic_runtime.decision_result import DecisionResult
        if not self.allowed:
            return DecisionResult.policy_denied(self.reason_code or "policy_blocked")
        return DecisionResult(
            action_kind="follow",
            confidence=1.0,
            source="heuristic",
            reason_codes=[self.reason_code] if self.reason_code and self.reason_code != "allowed" else [],
        )


def evaluate_policy(
    *,
    boundary: str,
    notes_released: bool,
    selected_artifact_allowed: bool = True,
    external_provider: bool = False,
    training_context_allowed: bool = False,
) -> PolicyDecision:
    boundary_key = str(boundary or "").strip().lower() or "local_observation"
    allowed = True
    reason = "allowed"
    if boundary_key == "external_provider" and external_provider:
        allowed = False
        reason = "external_provider_denied"
    elif boundary_key in {"worker_request", "lmstudio_prompt", "external_provider"} and external_provider and not training_context_allowed:
        allowed = False
        reason = "training_context_cloud_denied"
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
    training_context_allowed: bool = False,
) -> tuple[dict[str, Any], PolicyDecision]:
    decision = evaluate_policy(
        boundary=boundary,
        notes_released=notes_released,
        selected_artifact_allowed=selected_artifact_allowed,
        external_provider=external_provider,
        training_context_allowed=training_context_allowed,
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
    if not training_context_allowed and isinstance(sanitized, dict):
        env = sanitized.get("context_envelope_ref")
        if isinstance(env, dict):
            env.pop("training_profile_ref", None)
            env.pop("active_pattern_refs", None)
    return sanitized, decision


def _decision_ref(boundary: str, reason: str) -> str:
    digest = hashlib.sha1(f"{boundary}:{reason}".encode("utf-8")).hexdigest()[:10]
    return f"ai-pol-{digest}"
