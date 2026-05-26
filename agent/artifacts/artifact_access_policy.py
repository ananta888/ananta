from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


_ALLOWED_USAGES = frozenset({"read", "summarize", "quote", "transform", "use_as_context"})
_KNOWN_SENSITIVITY = frozenset({"public", "internal", "internal_high", "secret", "credential", "security_sensitive"})
_KNOWN_BOUNDARY = frozenset({"local_only", "project_private", "approved_cloud", "public"})


@dataclass(frozen=True)
class ArtifactAccessPolicyDecision:
    decision: str
    reason_code: str
    policy_decision_ref: str

    def as_dict(self) -> dict[str, str]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "policy_decision_ref": self.policy_decision_ref,
        }


class ArtifactAccessPolicy:
    def evaluate(
        self,
        *,
        goal_id: str,
        artifact_sensitivity: str,
        requested_usage: str,
        worker_kind: str,
        provider_location: str,
        data_boundary: str,
        allow_approved_cloud: bool = False,
    ) -> ArtifactAccessPolicyDecision:
        normalized_goal = str(goal_id or "").strip()
        if not normalized_goal:
            return self._deny("missing_goal_id", goal_id="", requested_usage=requested_usage)
        usage = str(requested_usage or "").strip().lower()
        if usage not in _ALLOWED_USAGES:
            return self._deny("unknown_requested_usage", goal_id=normalized_goal, requested_usage=usage)
        sensitivity = str(artifact_sensitivity or "").strip().lower()
        if sensitivity not in _KNOWN_SENSITIVITY:
            return self._deny("unknown_artifact_sensitivity", goal_id=normalized_goal, requested_usage=usage)
        boundary = str(data_boundary or "").strip().lower()
        if boundary not in _KNOWN_BOUNDARY:
            return self._deny("unknown_data_boundary", goal_id=normalized_goal, requested_usage=usage)

        location = str(provider_location or "").strip().lower()
        if boundary == "local_only" and location not in {"local", "on_device"}:
            return self._deny("data_boundary_local_only", goal_id=normalized_goal, requested_usage=usage)
        if boundary == "project_private" and location in {"cloud", "external"}:
            return self._deny("data_boundary_project_private", goal_id=normalized_goal, requested_usage=usage)
        if boundary == "approved_cloud" and not allow_approved_cloud:
            return self._deny("approved_cloud_requires_explicit_policy", goal_id=normalized_goal, requested_usage=usage)

        if sensitivity in {"credential", "secret"} and location in {"cloud", "external"}:
            return self._deny("sensitivity_forbidden_for_remote_provider", goal_id=normalized_goal, requested_usage=usage)

        _ = str(worker_kind or "").strip().lower()
        return self._allow(goal_id=normalized_goal, requested_usage=usage)

    def _allow(self, *, goal_id: str, requested_usage: str) -> ArtifactAccessPolicyDecision:
        return ArtifactAccessPolicyDecision(
            decision="allow",
            reason_code="allowed",
            policy_decision_ref=self._decision_ref(goal_id=goal_id, requested_usage=requested_usage, reason_code="allowed"),
        )

    def _deny(self, reason_code: str, *, goal_id: str, requested_usage: str) -> ArtifactAccessPolicyDecision:
        return ArtifactAccessPolicyDecision(
            decision="deny",
            reason_code=reason_code,
            policy_decision_ref=self._decision_ref(goal_id=goal_id, requested_usage=requested_usage, reason_code=reason_code),
        )

    @staticmethod
    def _decision_ref(*, goal_id: str, requested_usage: str, reason_code: str) -> str:
        seed = f"{goal_id}:{requested_usage}:{reason_code}".encode("utf-8")
        return f"artifact-policy-{hashlib.sha1(seed).hexdigest()[:12]}"


def evaluate_artifact_access_policy(payload: dict[str, Any]) -> dict[str, str]:
    policy = ArtifactAccessPolicy()
    decision = policy.evaluate(
        goal_id=str(payload.get("goal_id") or ""),
        artifact_sensitivity=str(payload.get("artifact_sensitivity") or ""),
        requested_usage=str(payload.get("requested_usage") or ""),
        worker_kind=str(payload.get("worker_kind") or ""),
        provider_location=str(payload.get("provider_location") or ""),
        data_boundary=str(payload.get("data_boundary") or ""),
        allow_approved_cloud=bool(payload.get("allow_approved_cloud", False)),
    )
    return decision.as_dict()
