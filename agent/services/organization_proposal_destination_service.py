"""Hub-owned destination classification for worker task proposals."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from agent.db_models import (
    AgentInfoDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    TaskDB,
)
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)
from agent.services.organization_routing_service import (
    OrganizationRoutingCandidate,
    OrganizationRoutingRequest,
    OrganizationRoutingService,
    infer_organization_assignment_duties,
)
from agent.services.separation_of_duties_service import (
    DutyAssignment,
    SeparationOfDutiesPolicy,
)


@dataclass(frozen=True, slots=True)
class OrganizationProposalDestination:
    allowed: bool
    reason_code: str
    unit_id: str | None
    team_id: str | None
    role_slot_id: str | None
    preview_agent_id: str | None
    preview_assignment_id: str | None
    decision_hash: str
    candidate_count: int
    eligible_candidate_count: int
    exclusion_reasons: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "organization_proposal_destination.v1",
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "unit_id": self.unit_id,
            "team_id": self.team_id,
            "role_slot_id": self.role_slot_id,
            "preview_agent_id": self.preview_agent_id,
            "preview_assignment_id": self.preview_assignment_id,
            "decision_hash": self.decision_hash,
            "candidate_count": self.candidate_count,
            "eligible_candidate_count": self.eligible_candidate_count,
            "exclusion_reasons": [{"reason_code": reason, "count": count} for reason, count in self.exclusion_reasons],
            "hints_authoritative": False,
            "agent_assignment_revalidated_at_dispatch": True,
        }


class OrganizationProposalDestinationService:
    """Choose a proposal's target slot without trusting worker target hints.

    This service is read-only.  It uses the same eligibility, capacity, risk,
    and SoD policies as final organization dispatch.  The selected agent and
    assignment are a classification-time preview only; execute-next performs
    the final locked selection before writing its dispatch intent.
    """

    def __init__(
        self,
        *,
        routing_service: OrganizationRoutingService | None = None,
        assignment_eligibility: OrganizationAssignmentEligibilityService | None = None,
    ) -> None:
        self._routing = routing_service or OrganizationRoutingService()
        self._assignment_eligibility = assignment_eligibility or OrganizationAssignmentEligibilityService()

    def resolve(
        self,
        *,
        session: Session,
        proposal: Any,
        target_scope: set[str],
        effective_policy_hash: str,
    ) -> OrganizationProposalDestination:
        if not target_scope:
            return self._blocked("proposal_target_scope_empty", effective_policy_hash)

        envelope = dict(getattr(proposal, "envelope", None) or {})
        payload = dict(envelope.get("payload") or {})
        links = list(
            session.exec(
                select(OrganizationTeamLinkDB).where(
                    OrganizationTeamLinkDB.tenant_id == proposal.tenant_id,
                    OrganizationTeamLinkDB.project_id == proposal.project_id,
                    OrganizationTeamLinkDB.organization_id == proposal.organization_id,
                    OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")),
                )
            ).all()
        )
        allowed_links = self._allowed_links(
            links=links,
            source_unit_id=str(proposal.unit_id or ""),
            source_team_id=str(proposal.team_id or ""),
            target_scope=target_scope,
        )
        if not allowed_links:
            return self._blocked("proposal_target_scope_unavailable", effective_policy_hash)

        unit_to_team = {row.unit_id: row.team_id for row in allowed_links}
        slots = list(
            session.exec(
                select(OrganizationRoleSlotDB).where(
                    OrganizationRoleSlotDB.tenant_id == proposal.tenant_id,
                    OrganizationRoleSlotDB.project_id == proposal.project_id,
                    OrganizationRoleSlotDB.organization_id == proposal.organization_id,
                    OrganizationRoleSlotDB.unit_id.in_(sorted(unit_to_team)),
                    OrganizationRoleSlotDB.lifecycle == "active",
                )
            ).all()
        )
        if not slots:
            return self._blocked("proposal_target_role_unavailable", effective_policy_hash)

        slot_by_id = {row.id: row for row in slots}
        assignments = list(
            session.exec(
                select(OrganizationRoleAssignmentDB).where(
                    OrganizationRoleAssignmentDB.tenant_id == proposal.tenant_id,
                    OrganizationRoleAssignmentDB.project_id == proposal.project_id,
                    OrganizationRoleAssignmentDB.organization_id == proposal.organization_id,
                    OrganizationRoleAssignmentDB.role_slot_id.in_(sorted(slot_by_id)),
                    OrganizationRoleAssignmentDB.lifecycle == "active",
                )
            ).all()
        )
        agent_urls = sorted({row.agent_url for row in assignments if row.agent_url})
        agents = {
            row.url: row
            for row in (
                list(session.exec(select(AgentInfoDB).where(AgentInfoDB.url.in_(agent_urls))).all())
                if agent_urls
                else []
            )
        }
        capacity_by_agent = self._capacity_by_agent(session=session, agent_urls=agent_urls)
        current_duties = self._current_duties(
            session=session,
            proposal=proposal,
            team_by_unit={row.unit_id: row.team_id for row in links},
        )
        assignments_by_slot: dict[str, list[OrganizationRoleAssignmentDB]] = {}
        for assignment in assignments:
            assignments_by_slot.setdefault(assignment.role_slot_id, []).append(assignment)

        required_by_proposal = {str(value) for value in list(payload.get("required_capabilities") or []) if str(value)}
        route_rows: list[tuple[Any, OrganizationRoleSlotDB, str]] = []
        reason_counts: Counter[str] = Counter()
        candidate_count = 0
        eligible_count = 0
        for slot in sorted(slots, key=lambda row: (unit_to_team[row.unit_id], row.id)):
            slot_policy = dict(slot.assignment_policy or {})
            required = required_by_proposal | {
                str(value) for value in list(slot_policy.get("required_capabilities") or []) if str(value)
            }
            forbidden = {str(value) for value in list(slot_policy.get("forbidden_capabilities") or []) if str(value)}
            candidates: list[OrganizationRoutingCandidate] = []
            for assignment in assignments_by_slot.get(slot.id, []):
                agent = agents.get(assignment.agent_url)
                eligibility = self._assignment_eligibility.evaluate(
                    agent=agent,
                    required_capabilities=required,
                    forbidden_capabilities=forbidden,
                    capacity_used=capacity_by_agent.get(assignment.agent_url, 0),
                    principal_kind_allowed="agent"
                    in {str(value) for value in list(slot_policy.get("principal_kinds") or [])},
                    write_access_required=bool(slot_policy.get("write_access_required", False)),
                )
                metadata = dict(assignment.assignment_metadata or {})
                limits = dict(getattr(agent, "execution_limits", None) or {})
                candidates.append(
                    OrganizationRoutingCandidate(
                        agent_id=assignment.agent_url,
                        assignment_id=assignment.id,
                        organization_id=assignment.organization_id,
                        team_id=unit_to_team[slot.unit_id],
                        role_slot_id=slot.id,
                        capabilities=eligibility.capabilities,
                        backend=str(metadata.get("backend") or limits.get("backend") or "native"),
                        runtime_target=str(metadata.get("runtime_target") or limits.get("runtime_target") or "default"),
                        max_risk_level=str(metadata.get("max_risk_level") or limits.get("max_risk_level") or "medium"),
                        capacity_used=eligibility.capacity_used,
                        capacity_limit=eligibility.capacity_limit,
                        assignment_status="active" if eligibility.allowed else "ineligible",
                        duties=infer_organization_assignment_duties(
                            slot_key=slot.slot_key,
                            role_template_key=slot.role_template_key,
                            assignment_metadata=metadata,
                        ),
                    )
                )
            decision = self._routing.decide(
                request=OrganizationRoutingRequest(
                    organization_id=proposal.organization_id,
                    unit_id=slot.unit_id,
                    task_id=proposal.proposal_id,
                    task_kind=str(payload.get("task_kind") or ""),
                    role_slot_id=slot.id,
                    required_capabilities=frozenset(required),
                    allowed_team_ids=frozenset({unit_to_team[slot.unit_id]}),
                    allowed_backends=frozenset(),
                    allowed_runtime_targets=frozenset(),
                    risk_level=str(payload.get("risk") or "medium"),
                    effective_policy_hash=effective_policy_hash,
                    target_role_hint=self._first_hint(payload, "suggested_role_refs"),
                    target_team_hint=self._first_hint(payload, "suggested_team_refs"),
                    target_agent_hint=self._first_hint(payload, "suggested_agent_refs"),
                ),
                candidates=candidates,
                current_duty_assignments=current_duties,
                sod_policy=SeparationOfDutiesPolicy.enterprise_default(revision=effective_policy_hash[:16]),
            )
            candidate_count += len(decision.candidates)
            eligible_count += sum(1 for row in decision.candidates if row.allowed)
            for evaluation in decision.candidates:
                reason_counts.update(evaluation.exclusion_reasons)
            if decision.status == "routable":
                route_rows.append((decision, slot, unit_to_team[slot.unit_id]))
            else:
                reason_counts.update((decision.reason_code,))

        decision_hash = self._aggregate_hash(
            effective_policy_hash=effective_policy_hash,
            target_scope=target_scope,
            route_hashes=[row[0].policy_hash for row in route_rows],
            reason_counts=reason_counts,
        )
        normalized_reasons = tuple(sorted(reason_counts.items()))
        if not route_rows:
            reason = min(reason_counts, default="no_policy_eligible_assignment")
            return OrganizationProposalDestination(
                allowed=False,
                reason_code=f"proposal_routing_blocked:{reason}",
                unit_id=None,
                team_id=None,
                role_slot_id=None,
                preview_agent_id=None,
                preview_assignment_id=None,
                decision_hash=decision_hash,
                candidate_count=candidate_count,
                eligible_candidate_count=eligible_count,
                exclusion_reasons=normalized_reasons,
            )

        decision, slot, team_id = min(
            route_rows,
            key=lambda row: self._route_rank(row[0], row[1]),
        )
        return OrganizationProposalDestination(
            allowed=True,
            reason_code="proposal_destination_selected",
            unit_id=slot.unit_id,
            team_id=team_id,
            role_slot_id=slot.id,
            preview_agent_id=decision.selected_agent_id,
            preview_assignment_id=decision.selected_assignment_id,
            decision_hash=decision_hash,
            candidate_count=candidate_count,
            eligible_candidate_count=eligible_count,
            exclusion_reasons=normalized_reasons,
        )

    @staticmethod
    def _allowed_links(
        *,
        links: list[OrganizationTeamLinkDB],
        source_unit_id: str,
        source_team_id: str,
        target_scope: set[str],
    ) -> list[OrganizationTeamLinkDB]:
        if "same_organization" in target_scope:
            return links
        if "same_unit" in target_scope:
            return [row for row in links if row.unit_id == source_unit_id]
        if "same_team" in target_scope:
            return [row for row in links if row.team_id == source_team_id]
        return []

    @staticmethod
    def _capacity_by_agent(*, session: Session, agent_urls: list[str]) -> dict[str, int]:
        if not agent_urls:
            return {}
        rows = session.exec(
            select(TaskDB.assigned_agent_url, func.count(TaskDB.id))
            .where(
                TaskDB.assigned_agent_url.in_(agent_urls),
                TaskDB.status.in_(("assigned", "in_progress")),
            )
            .group_by(TaskDB.assigned_agent_url)
        ).all()
        return {str(agent_url): int(count or 0) for agent_url, count in rows if agent_url}

    @staticmethod
    def _current_duties(
        *,
        session: Session,
        proposal: Any,
        team_by_unit: dict[str, str],
    ) -> tuple[DutyAssignment, ...]:
        assignments = list(
            session.exec(
                select(OrganizationRoleAssignmentDB).where(
                    OrganizationRoleAssignmentDB.tenant_id == proposal.tenant_id,
                    OrganizationRoleAssignmentDB.project_id == proposal.project_id,
                    OrganizationRoleAssignmentDB.organization_id == proposal.organization_id,
                    OrganizationRoleAssignmentDB.lifecycle == "active",
                )
            ).all()
        )
        slot_ids = sorted({row.role_slot_id for row in assignments})
        slots = {
            row.id: row
            for row in (
                list(
                    session.exec(
                        select(OrganizationRoleSlotDB).where(
                            OrganizationRoleSlotDB.tenant_id == proposal.tenant_id,
                            OrganizationRoleSlotDB.project_id == proposal.project_id,
                            OrganizationRoleSlotDB.organization_id == proposal.organization_id,
                            OrganizationRoleSlotDB.id.in_(slot_ids),
                        )
                    ).all()
                )
                if slot_ids
                else []
            )
        }
        return tuple(
            DutyAssignment(
                principal_id=row.agent_url,
                role_slot_id=row.role_slot_id,
                team_id=str(team_by_unit.get(slots[row.role_slot_id].unit_id) or ""),
                duties=infer_organization_assignment_duties(
                    slot_key=slots[row.role_slot_id].slot_key,
                    role_template_key=slots[row.role_slot_id].role_template_key,
                    assignment_metadata=dict(row.assignment_metadata or {}),
                ),
            )
            for row in assignments
            if row.role_slot_id in slots
        )

    @staticmethod
    def _first_hint(payload: dict[str, Any], field: str) -> str | None:
        values = [str(value) for value in list(payload.get(field) or []) if str(value)]
        return sorted(values)[0] if values else None

    @staticmethod
    def _route_rank(decision: Any, slot: OrganizationRoleSlotDB) -> tuple[Any, ...]:
        selected = next(
            (row for row in decision.candidates if row.assignment_id == decision.selected_assignment_id),
            None,
        )
        capacity_ratio = (
            selected.capacity_used / max(1, selected.capacity_limit) if selected is not None else float("inf")
        )
        return (
            capacity_ratio,
            str(decision.selected_team_id or ""),
            slot.id,
            str(decision.selected_agent_id or ""),
            str(decision.selected_assignment_id or ""),
        )

    @staticmethod
    def _aggregate_hash(
        *,
        effective_policy_hash: str,
        target_scope: set[str],
        route_hashes: list[str],
        reason_counts: Counter[str],
    ) -> str:
        payload = {
            "effective_policy_hash": effective_policy_hash,
            "target_scope": sorted(target_scope),
            "route_hashes": sorted(route_hashes),
            "reason_counts": dict(sorted(reason_counts.items())),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def _blocked(
        cls,
        reason_code: str,
        effective_policy_hash: str,
    ) -> OrganizationProposalDestination:
        return OrganizationProposalDestination(
            allowed=False,
            reason_code=reason_code,
            unit_id=None,
            team_id=None,
            role_slot_id=None,
            preview_agent_id=None,
            preview_assignment_id=None,
            decision_hash=cls._aggregate_hash(
                effective_policy_hash=effective_policy_hash,
                target_scope=set(),
                route_hashes=[],
                reason_counts=Counter({reason_code: 1}),
            ),
            candidate_count=0,
            eligible_candidate_count=0,
            exclusion_reasons=((reason_code, 1),),
        )


__all__ = [
    "OrganizationProposalDestination",
    "OrganizationProposalDestinationService",
]
