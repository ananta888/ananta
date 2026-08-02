from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent.db_models import WorkerTaskProposalDB
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)
from agent.services.worker_task_proposal_contract_service import WorkerTaskProposalContractService
from agent.services.worker_task_proposal_policy_service import (
    AssignmentProposalScope,
    WorkerTaskProposalPolicyService,
)


class WorkerTaskProposalIngressError(ValueError):
    def __init__(self, reason_code: str, issues: list[Any] | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.issues = list(issues or [])


@dataclass(frozen=True, slots=True)
class WorkerProposalIngressContext:
    authenticated_worker_id: str
    channel: str
    credential_scopes: frozenset[str]
    capability_task_id: str
    capability_assignment_id: str
    capability_dispatch_lease_id: str


class WorkerTaskProposalIngressService:
    """Registered result-channel adapter; it owns no classification or routing."""

    _REQUIRED_SCOPES = frozenset({"worker.result.submit", "worker.task_proposal.submit"})

    def __init__(
        self,
        *,
        contract_service: WorkerTaskProposalContractService | None = None,
        policy_service: WorkerTaskProposalPolicyService | None = None,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
        authoritative_assignment_resolver: Callable[..., tuple[AssignmentProposalScope, dict[str, Any]]] | None = None,
    ) -> None:
        self._contract = contract_service or WorkerTaskProposalContractService()
        self._policy = policy_service or WorkerTaskProposalPolicyService()
        self._uow_factory = uow_factory or PlanningControlUnitOfWork
        self._authoritative_assignment_resolver = authoritative_assignment_resolver

    def ingest(
        self,
        *,
        envelope: Mapping[str, Any],
        ingress: WorkerProposalIngressContext,
        assignment: AssignmentProposalScope,
        role_policy: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        # The source Task id is the only caller value used for serialization.
        # Tenant/project/Organization and the active lease are re-read under
        # the write UoW below and must never be trusted from a stale snapshot.
        scope_key = f"proposal-ingress:{assignment.source_task_id}"
        with planning_scope_lock(scope_key):
            return self._ingest_locked(
                envelope=envelope,
                ingress=ingress,
                assignment=assignment,
                role_policy=role_policy,
                scope_key=scope_key,
            )

    def _ingest_locked(
        self,
        *,
        envelope: Mapping[str, Any],
        ingress: WorkerProposalIngressContext,
        assignment: AssignmentProposalScope,
        role_policy: Mapping[str, Any] | None,
        scope_key: str,
    ) -> dict[str, Any]:
        contract = self._contract.validate(dict(envelope or {}))
        if not contract["valid"]:
            raise WorkerTaskProposalIngressError(
                "worker_task_proposal_contract_invalid",
                list(contract["issues"]),
            )
        candidate = dict(contract["envelope"])
        proposal_id = str(candidate.get("proposal_id") or "")
        idempotency_key = str(candidate.get("idempotency_key") or "")
        with self._uow_factory() as uow:
            assert uow.planning is not None
            assert uow.session is not None
            uow.planning.acquire_scope_lock(scope_key)
            if self._authoritative_assignment_resolver is None:
                raise WorkerTaskProposalIngressError("worker_task_proposal_authoritative_resolver_required")
            assignment, role_policy = self._authoritative_assignment_resolver(
                session=uow.session,
                source_task_id=assignment.source_task_id,
            )
            self._validate_channel(ingress=ingress, assignment=assignment)
            existing_id = uow.planning.get_proposal(proposal_id)
            existing_key = uow.planning.get_proposal_by_idempotency(
                organization_id=assignment.organization_id,
                source_task_id=assignment.source_task_id,
                idempotency_key=idempotency_key,
            )
            existing = existing_id or existing_key
            if existing is not None:
                if (
                    existing.proposal_id != proposal_id
                    or existing.payload_digest != str(contract["payload_digest"])
                    or existing.envelope_digest != str(contract["envelope_digest"])
                    or dict(existing.envelope or {}) != candidate
                ):
                    raise WorkerTaskProposalIngressError("worker_task_proposal_idempotency_conflict")
                return self._response(existing, replayed=True)

            proposal_count = uow.planning.count_proposals_for_source(
                organization_id=assignment.organization_id,
                source_task_id=assignment.source_task_id,
            )
            decision = self._policy.evaluate(
                envelope=candidate,
                policy=role_policy,
                assignment=assignment,
                proposal_count=proposal_count,
            )
            if not decision["allowed"]:
                raise WorkerTaskProposalIngressError(
                    str(decision["reason_code"]),
                    list(decision["issues"]),
                )
            role_version = str(candidate.get("proposing_role_template_ref") or "").rsplit("@", 1)[-1]
            proposal = WorkerTaskProposalDB(
                proposal_id=proposal_id,
                idempotency_key=idempotency_key,
                tenant_id=assignment.tenant_id,
                project_id=assignment.project_id,
                organization_id=assignment.organization_id,
                source_goal_id=assignment.goal_id,
                source_task_id=assignment.source_task_id,
                unit_id=assignment.unit_id,
                team_id=assignment.team_id,
                role_slot_id=assignment.role_slot_id,
                assignment_id=assignment.assignment_id,
                dispatch_lease_id=assignment.dispatch_lease_id,
                proposing_role_template_ref=assignment.role_template_ref,
                proposing_worker_id=assignment.worker_id,
                role_template_version=role_version,
                payload_digest=str(contract["payload_digest"]),
                envelope_digest=str(contract["envelope_digest"]),
                policy_hash=str(decision["effective_policy_hash"]),
                envelope=candidate,
                source_category_item_ids=[
                    str(value) for value in list(candidate.get("source_category_item_ids") or [])
                ],
                state="submitted",
                amendment_depth=assignment.amendment_depth,
                budget_estimate=dict(dict(candidate.get("payload") or {}).get("budget_estimate") or {}),
            )
            uow.planning.add_proposal(proposal)
        self._audit(proposal)
        return self._response(proposal, replayed=False)

    @classmethod
    def _validate_channel(
        cls,
        *,
        ingress: WorkerProposalIngressContext,
        assignment: AssignmentProposalScope,
    ) -> None:
        if ingress.channel not in {"worker_todo_result.v1", "worker_execution_result.v1"}:
            raise WorkerTaskProposalIngressError("worker_task_proposal_channel_forbidden")
        if not cls._REQUIRED_SCOPES.issubset(ingress.credential_scopes):
            raise WorkerTaskProposalIngressError("worker_task_proposal_credential_scope_forbidden")
        if ingress.authenticated_worker_id != assignment.worker_id:
            raise WorkerTaskProposalIngressError("worker_task_proposal_worker_mismatch")
        if ingress.capability_task_id != assignment.source_task_id:
            raise WorkerTaskProposalIngressError("worker_task_proposal_capability_task_mismatch")
        if ingress.capability_assignment_id != assignment.assignment_id:
            raise WorkerTaskProposalIngressError("worker_task_proposal_capability_assignment_mismatch")
        if ingress.capability_dispatch_lease_id != assignment.dispatch_lease_id:
            raise WorkerTaskProposalIngressError("worker_task_proposal_capability_lease_mismatch")

    @staticmethod
    def _response(proposal: WorkerTaskProposalDB, *, replayed: bool) -> dict[str, Any]:
        return {
            "proposal_id": proposal.proposal_id,
            "proposal_revision": proposal.proposal_revision,
            "proposal_digest": proposal.envelope_digest,
            "payload_digest": proposal.payload_digest,
            "state": proposal.state,
            "reason_code": proposal.reason_code,
            "replayed": replayed,
            "task_created": False,
            "queue_write": False,
        }

    @staticmethod
    def _audit(proposal: WorkerTaskProposalDB) -> None:
        try:
            from agent.common.audit import log_audit

            log_audit(
                "worker_task_proposal_submitted",
                {
                    "proposal_id": proposal.proposal_id,
                    "source_task_id": proposal.source_task_id,
                    "goal_id": proposal.source_goal_id,
                    "organization_id": proposal.organization_id,
                    "payload_digest_prefix": proposal.payload_digest[:19],
                },
            )
        except Exception:
            return


__all__ = [
    "WorkerProposalIngressContext",
    "WorkerTaskProposalIngressError",
    "WorkerTaskProposalIngressService",
]
