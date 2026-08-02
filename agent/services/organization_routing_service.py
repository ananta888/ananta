"""Deterministic Hub routing for organization-bound tasks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Iterable

from agent.services.separation_of_duties_service import (
    DutyAssignment,
    SeparationOfDutiesPolicy,
    SeparationOfDutiesService,
)


@dataclass(frozen=True, slots=True)
class OrganizationRoutingRequest:
    organization_id: str
    unit_id: str
    task_id: str
    task_kind: str
    role_slot_id: str
    required_capabilities: frozenset[str]
    allowed_team_ids: frozenset[str]
    allowed_backends: frozenset[str]
    allowed_runtime_targets: frozenset[str]
    risk_level: str
    effective_policy_hash: str
    target_role_hint: str | None = None
    target_team_hint: str | None = None
    target_agent_hint: str | None = None


@dataclass(frozen=True, slots=True)
class OrganizationRoutingCandidate:
    agent_id: str
    assignment_id: str
    organization_id: str
    team_id: str
    role_slot_id: str
    capabilities: frozenset[str]
    backend: str
    runtime_target: str
    max_risk_level: str
    capacity_used: int
    capacity_limit: int
    assignment_status: str
    duties: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class RoutingCandidateEvaluation:
    agent_id: str
    assignment_id: str
    team_id: str
    allowed: bool
    exclusion_reasons: tuple[str, ...]
    capacity_used: int
    capacity_limit: int


@dataclass(frozen=True, slots=True)
class OrganizationRoutingDecision:
    status: str
    reason_code: str
    task_id: str
    selected_agent_id: str | None
    selected_assignment_id: str | None
    selected_team_id: str | None
    selected_role_slot_id: str | None
    candidates: tuple[RoutingCandidateEvaluation, ...]
    staffing_recommendation: tuple[str, ...]
    policy_hash: str


class OrganizationRoutingService:
    """Ranks eligible assignments; it never writes the queue or contacts workers."""

    _RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

    def __init__(self, *, separation_of_duties: SeparationOfDutiesService | None = None) -> None:
        self._sod = separation_of_duties or SeparationOfDutiesService()

    def decide(
        self,
        *,
        request: OrganizationRoutingRequest,
        candidates: Iterable[OrganizationRoutingCandidate],
        current_duty_assignments: Iterable[DutyAssignment] = (),
        sod_policy: SeparationOfDutiesPolicy | None = None,
    ) -> OrganizationRoutingDecision:
        invalid_request = self._validate_request(request)
        rows = tuple(candidates)
        if invalid_request:
            return self._blocked(request, (), invalid_request, ("repair_routing_contract",))

        evaluations: list[RoutingCandidateEvaluation] = []
        eligible: list[OrganizationRoutingCandidate] = []
        missing_capabilities = set(request.required_capabilities)
        current_assignments = tuple(current_duty_assignments)
        for candidate in rows:
            reasons = self._exclusions(request, candidate)
            missing_capabilities -= set(candidate.capabilities)
            if not reasons and sod_policy is not None:
                prospective = DutyAssignment(
                    principal_id=candidate.agent_id,
                    role_slot_id=candidate.role_slot_id,
                    team_id=candidate.team_id,
                    duties=candidate.duties,
                )
                sod = self._sod.evaluate(policy=sod_policy, assignments=(*current_assignments, prospective))
                if not sod.allowed:
                    reasons.append(f"separation_of_duties:{sod.reason_code}")
            allowed = not reasons
            evaluations.append(
                RoutingCandidateEvaluation(
                    agent_id=candidate.agent_id,
                    assignment_id=candidate.assignment_id,
                    team_id=candidate.team_id,
                    allowed=allowed,
                    exclusion_reasons=tuple(sorted(set(reasons))),
                    capacity_used=candidate.capacity_used,
                    capacity_limit=candidate.capacity_limit,
                )
            )
            if allowed:
                eligible.append(candidate)

        if not eligible:
            recommendations = tuple(
                [f"staff_capability:{item}" for item in sorted(missing_capabilities)]
                or ["create_or_reactivate_compatible_assignment"]
            )
            reason = "required_capability_unavailable" if missing_capabilities else "no_policy_eligible_assignment"
            return self._blocked(request, tuple(evaluations), reason, recommendations)

        # Hints are deliberately not part of ranking. They are advisory input
        # only and cannot override capability, scope, capacity, or SoD policy.
        selected = min(
            eligible,
            key=lambda candidate: (
                candidate.capacity_used / max(1, candidate.capacity_limit),
                candidate.team_id,
                candidate.role_slot_id,
                candidate.agent_id,
                candidate.assignment_id,
            ),
        )
        return OrganizationRoutingDecision(
            status="routable",
            reason_code="eligible_assignment_selected",
            task_id=request.task_id,
            selected_agent_id=selected.agent_id,
            selected_assignment_id=selected.assignment_id,
            selected_team_id=selected.team_id,
            selected_role_slot_id=selected.role_slot_id,
            candidates=tuple(evaluations),
            staffing_recommendation=(),
            policy_hash=self._decision_hash(request, evaluations),
        )

    def _exclusions(
        self,
        request: OrganizationRoutingRequest,
        candidate: OrganizationRoutingCandidate,
    ) -> list[str]:
        reasons: list[str] = []
        if candidate.organization_id != request.organization_id:
            reasons.append("organization_scope_mismatch")
        if candidate.team_id not in request.allowed_team_ids:
            reasons.append("team_not_allowed")
        if candidate.role_slot_id != request.role_slot_id:
            reasons.append("role_slot_mismatch")
        if candidate.assignment_status != "active":
            reasons.append("assignment_not_active")
        for capability in sorted(request.required_capabilities - candidate.capabilities):
            reasons.append(f"missing_capability:{capability}")
        if request.allowed_backends and candidate.backend not in request.allowed_backends:
            reasons.append("backend_not_allowed")
        if request.allowed_runtime_targets and candidate.runtime_target not in request.allowed_runtime_targets:
            reasons.append("runtime_target_not_allowed")
        if self._risk(candidate.max_risk_level) < self._risk(request.risk_level):
            reasons.append("candidate_risk_ceiling_exceeded")
        if candidate.capacity_limit <= 0 or candidate.capacity_used >= candidate.capacity_limit:
            reasons.append("assignment_capacity_exhausted")
        if candidate.capacity_used < 0:
            reasons.append("assignment_capacity_invalid")
        return reasons

    @staticmethod
    def _validate_request(request: OrganizationRoutingRequest) -> str | None:
        required = {
            "organization_id": request.organization_id,
            "unit_id": request.unit_id,
            "task_id": request.task_id,
            "task_kind": request.task_kind,
            "role_slot_id": request.role_slot_id,
            "effective_policy_hash": request.effective_policy_hash,
        }
        if any(not str(value or "").strip() for value in required.values()):
            return "routing_request_binding_missing"
        if not request.allowed_team_ids:
            return "routing_allowed_teams_empty"
        return None

    @classmethod
    def _risk(cls, value: str) -> int:
        return cls._RISK_ORDER.get(str(value or "").lower(), 10)

    def _blocked(
        self,
        request: OrganizationRoutingRequest,
        evaluations: tuple[RoutingCandidateEvaluation, ...],
        reason_code: str,
        recommendation: tuple[str, ...],
    ) -> OrganizationRoutingDecision:
        return OrganizationRoutingDecision(
            status="blocked",
            reason_code=reason_code,
            task_id=request.task_id,
            selected_agent_id=None,
            selected_assignment_id=None,
            selected_team_id=None,
            selected_role_slot_id=None,
            candidates=evaluations,
            staffing_recommendation=recommendation,
            policy_hash=self._decision_hash(request, evaluations),
        )

    @staticmethod
    def _decision_hash(
        request: OrganizationRoutingRequest,
        evaluations: tuple[RoutingCandidateEvaluation, ...] | list[RoutingCandidateEvaluation],
    ) -> str:
        payload = {
            "request": {
                "organization_id": request.organization_id,
                "unit_id": request.unit_id,
                "task_id": request.task_id,
                "task_kind": request.task_kind,
                "role_slot_id": request.role_slot_id,
                "required_capabilities": sorted(request.required_capabilities),
                "allowed_team_ids": sorted(request.allowed_team_ids),
                "allowed_backends": sorted(request.allowed_backends),
                "allowed_runtime_targets": sorted(request.allowed_runtime_targets),
                "risk_level": request.risk_level,
                "effective_policy_hash": request.effective_policy_hash,
            },
            "candidates": [
                {
                    "agent_id": row.agent_id,
                    "assignment_id": row.assignment_id,
                    "team_id": row.team_id,
                    "allowed": row.allowed,
                    "exclusion_reasons": list(row.exclusion_reasons),
                    "capacity_used": row.capacity_used,
                    "capacity_limit": row.capacity_limit,
                }
                for row in evaluations
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def infer_organization_assignment_duties(
    *,
    slot_key: str,
    role_template_key: str,
    assignment_metadata: Mapping[str, Any] | None = None,
) -> frozenset[str]:
    """Return the shared, deterministic SoD duties for a role assignment.

    Assignment metadata is authoritative when it explicitly declares duties.
    The naming fallback keeps legacy organization slots fail-closed under the
    same rules at proposal classification and final dispatch routing.
    """

    metadata = dict(assignment_metadata or {})
    explicit = {str(value) for value in list(metadata.get("duties") or []) if str(value)}
    if explicit:
        return frozenset(explicit)
    identity = f"{slot_key}:{role_template_key}".lower()
    duties: set[str] = set()
    if any(value in identity for value in ("engineer", "developer", "implement")):
        duties.add("implementer")
    if "security" in identity and any(value in identity for value in ("engineer", "author", "champion")):
        duties.add("security_change_author")
    if any(value in identity for value in ("quality", "review", "verifier")):
        duties.add("independent_reviewer")
    if "security" in identity and any(value in identity for value in ("review", "approver", "verifier")):
        duties.add("security_approver")
    if "release" in identity and any(value in identity for value in ("approver", "review", "verifier")):
        duties.add("go_no_go_approver")
    if "release" in identity and any(value in identity for value in ("executor", "deployment")):
        duties.add("release_executor")
    return frozenset(duties)


__all__ = [
    "OrganizationRoutingCandidate",
    "OrganizationRoutingDecision",
    "OrganizationRoutingRequest",
    "OrganizationRoutingService",
    "RoutingCandidateEvaluation",
    "infer_organization_assignment_duties",
]
