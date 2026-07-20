"""Hub-only scheduler for minimum-authority semantic compute roles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from agent.repositories.semantic_lease_repository import LeaseRequest, SemanticLeaseRepository
from agent.services.semantic_compute_consent import (
    ComputeConsentContext,
    DenySemanticComputeConsentAuthority,
    SemanticComputeConsentAuthorityPort,
)
from agent.services.semantic_compute_policy import ComputeCandidate, SemanticComputePolicy
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort


class SemanticComputeSchedulingError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ScheduleRequest:
    tenant_id: str
    owner_subject: str
    contract_id: str
    contract_digest: str
    session_id: str
    room_id: str | None
    epoch: int
    task_type: str
    audience: str
    sequence_start: int
    sequence_end: int
    resource_budget: Mapping[str, int]
    deadline_at: float
    lease_ttl_seconds: float = 30.0
    validator_count: int = 0
    hot_standby: bool = False
    minimum_capacity: int = 1


@dataclass(frozen=True, slots=True)
class ScheduledRole:
    role: str
    candidate_id: str
    lease_id: str
    fencing_token: int


@dataclass(frozen=True, slots=True)
class PlannedRole:
    """Pure scheduler decision consumed by the Hub transaction boundary."""

    role: str
    candidate_id: str
    request: LeaseRequest


class SemanticComputeScheduler:
    """Selects candidates and delegates lease creation to Hub persistence."""

    def __init__(
        self,
        leases: SemanticLeaseRepository,
        policy: SemanticComputePolicy | None = None,
        *,
        audit: SemanticMediaAuditPort | None = None,
        consent_authority: SemanticComputeConsentAuthorityPort | None = None,
    ) -> None:
        self._leases = leases
        self._policy = policy or SemanticComputePolicy()
        self._consent_authority = consent_authority or DenySemanticComputeConsentAuthority()
        if audit is not None:
            self._leases.configure_audit(audit)

    def plan(self, request: ScheduleRequest, candidates: Iterable[ComputeCandidate]) -> tuple[PlannedRole, ...]:
        if not 0 <= request.validator_count <= 2:
            raise SemanticComputeSchedulingError("validator_count_invalid")
        candidates_by_id = {item.candidate_id: item for item in candidates}
        roles = ["primary"] + ["validator"] * request.validator_count
        if request.hot_standby:
            roles.append("standby")
        chosen: list[tuple[str, ComputeCandidate]] = []
        used_ids: set[str] = set()
        validator_failure_domains: set[str] = set()
        for role in roles:
            reduced = [
                self._policy.reduce(
                    item,
                    role=role,
                    task_type=request.task_type,
                    minimum_capacity=request.minimum_capacity,
                    consent_authorized=self._consent_authority.authorized(
                        ComputeConsentContext(
                            tenant_id=request.tenant_id,
                            owner_subject=request.owner_subject,
                            contract_id=request.contract_id,
                            contract_digest=request.contract_digest,
                            session_id=request.session_id,
                            room_id=request.room_id,
                            epoch=request.epoch,
                            candidate_id=item.candidate_id,
                            task_type=request.task_type,
                            role=role,
                        )
                    ),
                )
                for item in candidates_by_id.values()
                if item.candidate_id not in used_ids
            ]
            eligible = [item for item in reduced if item.eligible]
            if role == "validator":
                eligible = [item for item in eligible if item.source.failure_domain not in validator_failure_domains]
            if not eligible:
                raise SemanticComputeSchedulingError(f"no_eligible_{role}")
            # Descending capacity/reputation, ascending load/id makes the tie
            # break stable and prevents repeated preference for busy peers.
            winner = sorted(
                eligible,
                key=lambda item: (-item.score[0], -item.score[1], -item.score[2], item.score[3]),
            )[0].source
            chosen.append((role, winner))
            used_ids.add(winner.candidate_id)
            if role in {"primary", "validator"}:
                validator_failure_domains.add(winner.failure_domain)

        return tuple(
            PlannedRole(
                role=role,
                candidate_id=candidate.candidate_id,
                request=LeaseRequest(
                    tenant_id=request.tenant_id,
                    owner_subject=request.owner_subject,
                    contract_id=request.contract_id,
                    contract_digest=request.contract_digest,
                    session_id=request.session_id,
                    epoch=request.epoch,
                    task_type=request.task_type,
                    audience=request.audience,
                    role=role,
                    executor_id=candidate.candidate_id,
                    sequence_start=request.sequence_start,
                    sequence_end=request.sequence_end,
                    resource_budget=request.resource_budget,
                    ttl_seconds=request.lease_ttl_seconds,
                    deadline_at=request.deadline_at,
                ),
            )
            for role, candidate in chosen
        )

    def schedule(self, request: ScheduleRequest, candidates: Iterable[ComputeCandidate]) -> tuple[ScheduledRole, ...]:
        """Compatibility wrapper for callers scheduling a single transaction.

        Production API scheduling uses :meth:`plan` and the repository's
        atomic ``schedule_once`` aggregate operation.
        """

        issued: list[ScheduledRole] = []
        for planned in self.plan(request, candidates):
            lease = self._leases.acquire(planned.request)
            issued.append(
                ScheduledRole(
                    planned.role,
                    planned.candidate_id,
                    lease.id,
                    lease.fencing_token,
                )
            )
        return tuple(issued)


__all__ = [
    "PlannedRole",
    "ScheduleRequest",
    "ScheduledRole",
    "SemanticComputeScheduler",
    "SemanticComputeSchedulingError",
]
