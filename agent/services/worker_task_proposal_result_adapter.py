from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from sqlmodel import Session, select

from agent.services.worker_task_proposal_ingress_service import (
    WorkerProposalIngressContext,
    WorkerTaskProposalIngressError,
    WorkerTaskProposalIngressService,
)
from agent.services.worker_task_proposal_policy_service import AssignmentProposalScope


def authoritative_assignment_scope(
    *,
    source_task_id: str,
) -> tuple[AssignmentProposalScope, dict[str, Any]]:
    """Resolve the current Hub-owned assignment and its restrict-only policy.

    Both Worker ingress and later Hub classification must evaluate the same
    authoritative Task/lease snapshot.  Keeping that translation here avoids
    a second, subtly different security boundary in an HTTP adapter.
    """

    from agent.services.repository_registry import get_repository_registry

    task = get_repository_registry().task_repo.get_by_id(str(source_task_id or ""))
    if task is None:
        raise WorkerTaskProposalIngressError("worker_task_proposal_source_task_not_found")
    task_data = task.model_dump() if hasattr(task, "model_dump") else dict(task)
    return _assignment_scope_from_task_data(task_data=task_data)


def authoritative_assignment_scope_in_session(
    *,
    session: Session,
    source_task_id: str,
) -> tuple[AssignmentProposalScope, dict[str, Any]]:
    """Re-read the complete execution and Organization assignment binding.

    Hub classification uses this variant inside its locked planning UoW so a
    stale caller snapshot cannot keep an expired lease, Worker, role, or
    proposal policy alive.
    """

    from agent.db_models import (
        OrganizationRoleAssignmentDB,
        TaskDB,
        WorkerJobDB,
        WorkerSlotLeaseDB,
    )

    task = session.exec(select(TaskDB).where(TaskDB.id == str(source_task_id or "")).with_for_update()).one_or_none()
    if task is None:
        raise WorkerTaskProposalIngressError("worker_task_proposal_source_task_not_found")
    task_data = task.model_dump()
    worker_context = dict(task.worker_execution_context or {})
    binding = dict(worker_context.get("task_proposal_binding") or {})
    routing = dict(worker_context.get("organization_routing") or {})
    dispatch_lease_id = str(binding.get("dispatch_lease_id") or "")
    assignment_id = str(binding.get("assignment_id") or "")
    worker_id = str(binding.get("worker_id") or "")
    job = (
        session.exec(select(WorkerJobDB).where(WorkerJobDB.id == dispatch_lease_id).with_for_update()).one_or_none()
        if dispatch_lease_id
        else None
    )
    slot_lease = (
        session.exec(
            select(WorkerSlotLeaseDB).where(WorkerSlotLeaseDB.id == str(job.slot_lease_id or "")).with_for_update()
        ).one_or_none()
        if job is not None and job.slot_lease_id
        else None
    )
    role_assignment_id = str(routing.get("selected_assignment_id") or "")
    role_assignment = (
        session.exec(
            select(OrganizationRoleAssignmentDB)
            .where(
                OrganizationRoleAssignmentDB.id == role_assignment_id,
                OrganizationRoleAssignmentDB.tenant_id == str(task.tenant_id or ""),
                OrganizationRoleAssignmentDB.project_id == str(task.project_id or ""),
                OrganizationRoleAssignmentDB.organization_id == str(task.organization_id or ""),
                OrganizationRoleAssignmentDB.role_slot_id == str(task.role_slot_id or ""),
                OrganizationRoleAssignmentDB.agent_url == worker_id,
                OrganizationRoleAssignmentDB.lifecycle == "active",
            )
            .with_for_update()
        ).one_or_none()
        if role_assignment_id
        else None
    )
    lease_active = bool(
        job is not None
        and role_assignment is not None
        and str(job.status or "") in {"delegated", "running"}
        and job.finished_at is None
        and str(task.current_worker_job_id or "") == dispatch_lease_id
        and str(job.parent_task_id or "") == str(task.id or "")
        and str(job.subtask_id or "") == assignment_id
        and str(job.worker_url or "") == worker_id
        and (
            not job.slot_lease_id
            or (
                slot_lease is not None
                and str(slot_lease.status or "") == "active"
                and float(slot_lease.deadline_at) > time.time()
                and slot_lease.released_at is None
                and str(slot_lease.parent_task_id or "") in {"", str(task.id or "")}
                and str(slot_lease.worker_job_id or "") in {"", dispatch_lease_id}
            )
        )
        and routing.get("schema") == "organization_routing_decision.v1"
        and str(routing.get("selected_agent_id") or "") == worker_id
        and str(routing.get("selected_team_id") or "") == str(task.team_id or "")
        and str(routing.get("selected_role_slot_id") or "") == str(task.role_slot_id or "")
    )
    return _assignment_scope_from_task_data(
        task_data=task_data,
        lease_active=lease_active,
    )


def _assignment_scope_from_task_data(
    *,
    task_data: dict[str, Any],
    lease_active: bool | None = None,
) -> tuple[AssignmentProposalScope, dict[str, Any]]:
    worker_context = dict(task_data.get("worker_execution_context") or {})
    binding = dict(worker_context.get("task_proposal_binding") or {})
    policy = dict(binding.get("proposal_policy") or {})
    lineage = dict(worker_context.get("planning_lineage") or {})
    topology = dict(worker_context.get("organization_topology_refs") or {})
    allowed_source_refs = {str(value) for value in list(worker_context.get("allowed_source_refs") or []) if str(value)}
    allowed_run_refs = {str(value) for value in list(worker_context.get("allowed_run_refs") or []) if str(value)}
    assignment = AssignmentProposalScope(
        tenant_id=str(task_data.get("tenant_id") or ""),
        project_id=str(task_data.get("project_id") or ""),
        organization_id=str(task_data.get("organization_id") or ""),
        goal_id=str(task_data.get("goal_id") or ""),
        source_task_id=str(task_data.get("id") or ""),
        unit_id=str(task_data.get("unit_id") or ""),
        team_id=str(task_data.get("team_id") or ""),
        role_slot_id=str(task_data.get("role_slot_id") or ""),
        assignment_id=str(binding.get("assignment_id") or ""),
        dispatch_lease_id=str(binding.get("dispatch_lease_id") or ""),
        worker_id=str(binding.get("worker_id") or ""),
        role_template_ref=str(binding.get("role_template_ref") or ""),
        source_task_status=str(task_data.get("status") or ""),
        lease_active=(
            lease_active
            if lease_active is not None
            else (
                bool(str(binding.get("dispatch_lease_id") or ""))
                and str(task_data.get("current_worker_job_id") or "") == str(binding.get("dispatch_lease_id") or "")
            )
        ),
        allowed_task_kinds=frozenset(
            str(value) for value in list(binding.get("allowed_task_kinds") or policy.get("allowed_task_kinds") or [])
        ),
        allowed_capabilities=frozenset(str(value) for value in list(task_data.get("required_capabilities") or [])),
        allowed_context_refs=frozenset(str(value) for value in list(worker_context.get("allowed_context_refs") or [])),
        allowed_evidence_refs=frozenset(allowed_source_refs | allowed_run_refs),
        source_category_item_ids=frozenset(str(value) for value in list(lineage.get("source_category_item_ids") or [])),
        known_role_refs=frozenset(str(value) for value in list(topology.get("role_refs") or [])),
        known_team_refs=frozenset(str(value) for value in list(topology.get("team_refs") or [])),
        known_agent_refs=frozenset(str(value) for value in list(topology.get("agent_refs") or [])),
        remaining_budget=dict(worker_context.get("remaining_proposal_budget") or {}),
        amendment_depth=int(lineage.get("amendment_depth") or 0),
    )
    return assignment, policy


def ingest_callback_task_proposals(
    *,
    source_task_id: str,
    callback_payload: dict[str, Any],
    capability_claims: dict[str, Any],
) -> list[dict[str, Any]]:
    carrier = callback_payload.get("task_proposals")
    if carrier is None:
        return []
    if not isinstance(carrier, dict):
        raise WorkerTaskProposalIngressError("worker_task_proposals_carrier_invalid")
    if str(carrier.get("schema") or "") == "worker_task_proposals_ref.v1":
        raise WorkerTaskProposalIngressError("worker_task_proposals_artifact_resolution_required")
    if str(carrier.get("schema") or "") != "worker_task_proposals.v1":
        raise WorkerTaskProposalIngressError("worker_task_proposals_carrier_schema_invalid")
    proposals = [dict(row) for row in list(carrier.get("proposals") or []) if isinstance(row, dict)]
    rendered = json.dumps(proposals, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    carrier_digest = f"sha256:{hashlib.sha256(rendered).hexdigest()}"
    if str(carrier.get("payload_digest") or "") != carrier_digest:
        raise WorkerTaskProposalIngressError("worker_task_proposals_carrier_digest_mismatch")

    assignment, policy = authoritative_assignment_scope(source_task_id=source_task_id)
    ingress = WorkerProposalIngressContext(
        authenticated_worker_id=str(capability_claims.get("worker_id") or ""),
        channel="worker_execution_result.v1",
        credential_scopes=frozenset(str(value) for value in list(capability_claims.get("scopes") or [])),
        capability_task_id=str(capability_claims.get("source_task_id") or ""),
        capability_assignment_id=str(capability_claims.get("assignment_id") or ""),
        capability_dispatch_lease_id=str(capability_claims.get("dispatch_lease_id") or ""),
    )
    service = WorkerTaskProposalIngressService(
        authoritative_assignment_resolver=authoritative_assignment_scope_in_session
    )
    return [
        service.ingest(
            envelope=proposal,
            ingress=ingress,
            assignment=assignment,
            role_policy=policy,
        )
        for proposal in proposals
    ]


__all__ = [
    "authoritative_assignment_scope",
    "authoritative_assignment_scope_in_session",
    "ingest_callback_task_proposals",
]
