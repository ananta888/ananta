"""Hub-owned admission policy for unsafe research model executions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ResearchModelRunRequest:
    trust_class: str | None
    safety_modified: bool | None
    environment: str
    route: str
    model_revision: str
    runtime_id: str
    authorization_expires_at: str | None
    tools_requested: bool = False
    network_requested: bool = False
    write_requested: bool = False
    secrets_present: bool = False
    personal_data_present: bool = False


@dataclass(frozen=True, slots=True)
class ResearchModelPolicyDecision:
    allowed: bool
    reason_code: str

    def audit_event(self, request: ResearchModelRunRequest) -> dict[str, object]:
        """Project a content-free event for the existing Hub audit sink."""
        return {
            "schema": "ananta.unsafe-research-policy-event.v1",
            "event_type": "unsafe_research.run_admitted" if self.allowed else "unsafe_research.run_blocked",
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "trust_class": request.trust_class,
            "model_revision_sha256": hashlib.sha256(request.model_revision.encode()).hexdigest(),
            "runtime_id": request.runtime_id,
            "environment": request.environment,
            "route": request.route,
            "content_persisted": False,
        }


class ResearchModelPolicyService:
    def __init__(self, policy: dict[str, object]) -> None:
        self._policy = policy

    @classmethod
    def from_file(cls, path: str | Path) -> "ResearchModelPolicyService":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema") != "ananta.model-trust-policy.v1":
            raise ValueError("model_trust_policy_invalid")
        return cls(payload)

    def evaluate(
        self,
        request: ResearchModelRunRequest,
        *,
        now: datetime | None = None,
    ) -> ResearchModelPolicyDecision:
        if request.trust_class != "unsafe_research" or request.safety_modified is not True:
            return self._deny("unsafe_research_identity_missing")
        if request.environment not in self._policy["allowed_environments"]:
            return self._deny("unsafe_research_environment_forbidden")
        if request.route in self._policy["forbidden_routes"]:
            return self._deny("unsafe_research_route_forbidden")
        if any((request.tools_requested, request.network_requested, request.write_requested)):
            return self._deny("unsafe_research_capability_forbidden")
        if request.secrets_present or request.personal_data_present:
            return self._deny("unsafe_research_data_forbidden")
        if not request.model_revision or not request.runtime_id:
            return self._deny("unsafe_research_binding_missing")
        expiry = self._parse_expiry(request.authorization_expires_at)
        if expiry is None or expiry <= (now or datetime.now(UTC)):
            return self._deny("unsafe_research_authorization_expired")
        return ResearchModelPolicyDecision(True, "unsafe_research_run_admitted")

    @staticmethod
    def _parse_expiry(value: str | None) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _deny(reason: str) -> ResearchModelPolicyDecision:
        return ResearchModelPolicyDecision(False, reason)


__all__ = [
    "ResearchModelPolicyDecision",
    "ResearchModelPolicyService",
    "ResearchModelRunRequest",
]
