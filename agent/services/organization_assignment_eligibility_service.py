"""Fail-closed eligibility policy for organization role assignments.

The service is deliberately persistence-agnostic.  Candidate projections and
topology writes can therefore evaluate the same Agent-directory facts without
coupling either boundary to SQLModel.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OrganizationAssignmentEligibility:
    allowed: bool
    reasons: tuple[str, ...]
    capabilities: frozenset[str]
    capacity_used: int
    capacity_limit: int


class OrganizationAssignmentEligibilityService:
    """Evaluate Hub-authorized Agent facts and bounded assignment capacity."""

    def evaluate(
        self,
        *,
        agent: Any | None,
        required_capabilities: set[str],
        forbidden_capabilities: set[str],
        capacity_used: int,
        principal_kind_allowed: bool,
        write_access_required: bool,
    ) -> OrganizationAssignmentEligibility:
        if agent is None:
            return OrganizationAssignmentEligibility(
                allowed=False,
                reasons=("agent_not_registered",),
                capabilities=frozenset(),
                capacity_used=max(0, capacity_used) if isinstance(capacity_used, int) else 0,
                capacity_limit=0,
            )

        reasons: list[str] = []
        if not bool(getattr(agent, "registration_validated", False)):
            reasons.append("agent_registration_unvalidated")
        if str(getattr(agent, "status", "") or "").lower() != "online":
            reasons.append("agent_not_online")
        if not principal_kind_allowed:
            reasons.append("assignment_principal_kind_not_allowed")

        # ``capabilities`` is retained as a compatibility source only after
        # Hub registration validation.  New registrations populate the
        # authoritative ``authorized_capabilities`` field.
        capabilities = frozenset(
            getattr(agent, "authorized_capabilities", None) or getattr(agent, "capabilities", None) or []
        )
        reasons.extend(f"missing_capability:{value}" for value in sorted(required_capabilities - capabilities))
        reasons.extend(f"forbidden_capability:{value}" for value in sorted(forbidden_capabilities & capabilities))

        execution_limits = self._execution_limits(agent)
        if write_access_required and not (
            {"write_access", "repository_write"} & capabilities or execution_limits.get("write_access") is True
        ):
            reasons.append("write_access_required")

        capacity_limit, capacity_valid = self._capacity_limit(execution_limits)
        if isinstance(capacity_used, bool) or not isinstance(capacity_used, int) or capacity_used < 0:
            normalized_capacity_used = 0
            reasons.append("agent_capacity_invalid")
        else:
            normalized_capacity_used = capacity_used
        if not capacity_valid:
            reasons.append("agent_capacity_invalid")
        if capacity_limit <= 0 or normalized_capacity_used >= capacity_limit:
            reasons.append("agent_capacity_exhausted")

        normalized_reasons = tuple(sorted(set(reasons)))
        return OrganizationAssignmentEligibility(
            allowed=not normalized_reasons,
            reasons=normalized_reasons,
            capabilities=capabilities,
            capacity_used=normalized_capacity_used,
            capacity_limit=capacity_limit,
        )

    @staticmethod
    def _execution_limits(agent: Any) -> Mapping[str, Any]:
        value = getattr(agent, "execution_limits", None)
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _capacity_limit(execution_limits: Mapping[str, Any]) -> tuple[int, bool]:
        raw_limit = execution_limits.get(
            "max_concurrent_tasks",
            execution_limits.get("max_assignments", 1),
        )
        if isinstance(raw_limit, bool):
            return 0, False
        try:
            parsed = int(raw_limit)
        except (TypeError, ValueError, OverflowError):
            return 0, False
        if parsed < 0 or isinstance(raw_limit, float) and not raw_limit.is_integer():
            return 0, False
        return parsed, True


__all__ = [
    "OrganizationAssignmentEligibility",
    "OrganizationAssignmentEligibilityService",
]
