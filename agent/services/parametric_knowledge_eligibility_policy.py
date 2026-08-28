"""Hub-owned eligibility policy for compiling source knowledge into weights."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from ananta_contracts.parametric_knowledge import ParametricKnowledgeUnit, canonical_sha256


@dataclass(frozen=True, slots=True)
class ParametricKnowledgeEligibilityDecision:
    decision: str
    reason_codes: tuple[str, ...]
    policy_version: str
    policy_digest: str
    knowledge_unit_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.parametric-knowledge-eligibility-decision.v1",
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "knowledge_unit_digest": self.knowledge_unit_digest,
        }


class ParametricKnowledgeEligibilityPolicy:
    """Evaluate authoritative unit metadata; request flags cannot grant admission."""

    def __init__(self, policy: Mapping[str, Any], *, clock: Callable[[], datetime] | None = None) -> None:
        self._policy = dict(policy)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._version = str(self._policy.get("version") or "").strip()
        expected_fields = {
            "schema",
            "version",
            "default_decision",
            "allowed_sensitivity",
            "allowed_license_spdx",
            "citation_required_mode",
            "unknown_provenance_mode",
            "revoked_source_mode",
            "volatile_knowledge_mode",
            "authoritative_approval_required",
        }
        if (
            set(self._policy) != expected_fields
            or self._policy.get("schema") != "ananta.parametric-knowledge-eligibility-policy.v1"
            or self._policy.get("default_decision") != "deny"
            or self._policy.get("citation_required_mode") != "rag_only"
            or self._policy.get("unknown_provenance_mode") != "deny"
            or self._policy.get("revoked_source_mode") != "deny"
            or self._policy.get("volatile_knowledge_mode") != "deny"
            or self._policy.get("authoritative_approval_required") is not True
            or not self._version
        ):
            raise ValueError("parametric_knowledge_policy_invalid")
        licenses = self._policy.get("allowed_license_spdx")
        sensitivities = self._policy.get("allowed_sensitivity")
        if (
            not isinstance(licenses, list)
            or not licenses
            or any(not isinstance(item, str) or not item.strip() for item in licenses)
            or not isinstance(sensitivities, list)
            or not sensitivities
            or any(item not in {"public", "internal"} for item in sensitivities)
        ):
            raise ValueError("parametric_knowledge_policy_invalid")
        self._allowed_licenses = frozenset(licenses)
        self._allowed_sensitivity = frozenset(sensitivities)
        self._policy_digest = canonical_sha256(self._policy)

    @property
    def policy_digest(self) -> str:
        return self._policy_digest

    def evaluate(
        self,
        unit: ParametricKnowledgeUnit,
        *,
        tenant_id: str,
        workspace_id: str,
        repository_id: str,
    ) -> ParametricKnowledgeEligibilityDecision:
        deny: list[str] = []
        review: list[str] = []
        if (unit.tenant_id, unit.workspace_id, unit.repository_id) != (
            str(tenant_id),
            str(workspace_id),
            str(repository_id),
        ):
            deny.append("scope_binding_mismatch")
        if unit.revoked:
            deny.append("source_revoked")
        if not unit.stable:
            deny.append("knowledge_volatile")
        if unit.sensitivity not in self._allowed_sensitivity:
            deny.append(f"sensitivity_{unit.sensitivity}_denied")
        if unit.license_spdx not in self._allowed_licenses:
            deny.append("license_not_allowlisted")
        if unit.citation_required:
            deny.append("citation_required_rag_only")
        if unit.approval_state == "denied":
            deny.append("authoritative_approval_denied")
        elif unit.approval_state != "approved":
            review.append("authoritative_approval_required")
        if self._expired(unit.retention_until):
            deny.append("retention_expired")
        decision = "deny" if deny else "require_approval" if review else "allow"
        reasons = tuple(deny or review or ["eligible_stable_approved_knowledge"])
        return ParametricKnowledgeEligibilityDecision(
            decision=decision,
            reason_codes=reasons,
            policy_version=self._version,
            policy_digest=self._policy_digest,
            knowledge_unit_digest=unit.binding_digest,
        )

    def _expired(self, value: str) -> bool:
        try:
            expires = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            current = self._clock()
            if current.tzinfo is None:
                return True
            return expires <= current
        except (TypeError, ValueError):
            return True


__all__ = ["ParametricKnowledgeEligibilityDecision", "ParametricKnowledgeEligibilityPolicy"]
