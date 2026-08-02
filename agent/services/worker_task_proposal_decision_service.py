from __future__ import annotations

import copy
import hashlib
import time
from collections.abc import Callable, Mapping
from typing import Any

from agent.db_models import (
    ApprovalRequestDB,
    PlanningArtifactRevisionDB,
    PlanningLineageDB,
    TaskDB,
    WorkerTaskProposalDB,
)
from agent.routes.tasks.dependency_policy import validate_dependency_graph
from agent.services.approval_request_service import (
    ApprovalRequestService,
    canonical_approval_intent_key,
)
from agent.services.organization_proposal_destination_service import (
    OrganizationProposalDestination,
    OrganizationProposalDestinationService,
)
from agent.services.planning_artifact_transition_service import (
    PROPOSAL_AMEND_TOOL,
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_category_contract_service import stable_planning_digest
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)
from agent.services.planning_principal_identity_service import (
    canonical_planning_actor_id,
)
from agent.services.planning_summary_engine import PlanningSummaryEngine
from agent.services.planning_track_contract_service import planning_contract_hash
from agent.services.planning_track_pipeline_service import (
    evaluate_planning_quality_gates,
    validate_planning_track_with_details,
)
from agent.services.worker_task_proposal_ingress_service import (
    WorkerTaskProposalIngressError,
)
from agent.services.worker_task_proposal_policy_service import (
    AssignmentProposalScope,
    WorkerTaskProposalPolicyService,
)
from agent.services.worker_task_proposal_result_adapter import (
    authoritative_assignment_scope_in_session,
)


class WorkerTaskProposalDecisionService:
    """Classify proposals and create reviewable Track revisions, never Tasks."""

    _TERMINAL_STATES = frozenset({"rejected", "accepted_as_plan_amendment", "materialized", "superseded"})

    def __init__(
        self,
        *,
        policy_service: WorkerTaskProposalPolicyService | None = None,
        approval_service: ApprovalRequestService | None = None,
        destination_service: OrganizationProposalDestinationService | None = None,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
    ) -> None:
        self._policy = policy_service or WorkerTaskProposalPolicyService()
        self._approvals = approval_service or ApprovalRequestService()
        self._destinations = destination_service or OrganizationProposalDestinationService()
        self._uow_factory = uow_factory or PlanningControlUnitOfWork

    def classify(
        self,
        *,
        proposal_id: str,
        context: PlanningOperationContext,
        assignment: AssignmentProposalScope,
        current_role_policy: Mapping[str, Any] | None,
        approval_request_id: str | None = None,
        expected_revision: int | None = None,
        expected_digest: str | None = None,
    ) -> dict[str, Any]:
        self._authorize(context)
        # Do not derive the lock scope from a caller-provided assignment
        # snapshot.  Organization-wide serialization is conservative and
        # keeps concurrent amendments on the same adopted Track deterministic.
        scope_key = f"proposal-decision:{context.tenant_id}:{context.project_id}:{context.organization_id}"
        with planning_scope_lock(scope_key), self._uow_factory() as uow:
            assert uow.planning is not None and uow.session is not None
            uow.planning.acquire_scope_lock(scope_key)
            proposal = uow.planning.get_proposal(proposal_id, for_update=True)
            if proposal is None:
                raise PlanningTransitionError("worker_task_proposal_not_found")
            self._validate_scope(context=context, proposal=proposal)
            self._validate_precondition(
                proposal=proposal,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if proposal.state in self._TERMINAL_STATES:
                return self._response(proposal, replayed=True)

            try:
                assignment, current_role_policy = authoritative_assignment_scope_in_session(
                    session=uow.session,
                    source_task_id=proposal.source_task_id,
                )
            except WorkerTaskProposalIngressError as exc:
                return self._finish(
                    uow=uow,
                    proposal=proposal,
                    state="rejected",
                    reason_code=exc.reason_code,
                    actor=context.subject_id,
                    decision={
                        "routing_owner": "hub",
                        "next_steps": ["repair_source_assignment"],
                    },
                )
            if assignment.worker_id != proposal.proposing_worker_id:
                return self._finish(
                    uow=uow,
                    proposal=proposal,
                    state="rejected",
                    reason_code="proposal_proposing_worker_stale",
                    actor=context.subject_id,
                    decision={
                        "routing_owner": "hub",
                        "next_steps": ["repair_source_assignment"],
                    },
                )

            proposal_count = uow.planning.count_proposals_for_source(
                organization_id=proposal.organization_id,
                source_task_id=proposal.source_task_id,
            )
            policy = self._policy.evaluate(
                envelope=proposal.envelope,
                policy=current_role_policy,
                assignment=assignment,
                proposal_count=max(0, proposal_count - 1),
            )
            if not policy["allowed"]:
                return self._finish(
                    uow=uow,
                    proposal=proposal,
                    state="rejected",
                    reason_code=str(policy["reason_code"]),
                    actor=context.subject_id,
                    decision={"policy_hash": policy["effective_policy_hash"], "issues": policy["issues"][:30]},
                )
            if proposal.policy_hash != str(policy["effective_policy_hash"]):
                return self._finish(
                    uow=uow,
                    proposal=proposal,
                    state="rejected",
                    reason_code="proposal_policy_hash_stale",
                    actor=context.subject_id,
                    decision={"policy_hash": policy["effective_policy_hash"]},
                )

            duplicate = next(
                (
                    row
                    for row in uow.planning.list_proposals(
                        organization_id=proposal.organization_id,
                        source_goal_id=proposal.source_goal_id,
                    )
                    if row.proposal_id != proposal.proposal_id
                    and row.payload_digest == proposal.payload_digest
                    and row.state in {"accepted_as_plan_amendment", "materialized"}
                ),
                None,
            )
            if duplicate is not None:
                return self._finish(
                    uow=uow,
                    proposal=proposal,
                    state="superseded",
                    reason_code="proposal_duplicate_superseded",
                    actor=context.subject_id,
                    decision={"superseded_by_proposal_id": duplicate.proposal_id},
                )

            adopted_track = self._adopted_track(uow=uow, proposal=proposal)
            if adopted_track is None:
                return self._finish(
                    uow=uow,
                    proposal=proposal,
                    state="rejected",
                    reason_code="proposal_adopted_track_not_found",
                    actor=context.subject_id,
                    decision={},
                )
            category_scope = set(adopted_track.source_category_item_ids or [])
            if not set(proposal.source_category_item_ids or []).issubset(category_scope):
                return self._finish(
                    uow=uow,
                    proposal=proposal,
                    state="rejected",
                    reason_code="proposal_category_scope_expansion",
                    actor=context.subject_id,
                    decision={},
                )
            category = uow.planning.get_revision(str(adopted_track.parent_revision_id or ""))
            if category is None or category.status != "promoted":
                return self._finish(
                    uow=uow,
                    proposal=proposal,
                    state="rejected",
                    reason_code="proposal_category_revision_not_promoted",
                    actor=context.subject_id,
                    decision={},
                )

            destination = self._destinations.resolve(
                session=uow.session,
                proposal=proposal,
                target_scope={str(value) for value in list(policy["target_scope"])},
                effective_policy_hash=str(policy["effective_policy_hash"]),
            )
            if not destination.allowed:
                return self._finish(
                    uow=uow,
                    proposal=proposal,
                    state="rejected",
                    reason_code=destination.reason_code,
                    actor=context.subject_id,
                    decision={
                        "policy_hash": policy["effective_policy_hash"],
                        "routing_owner": "hub",
                        "destination": destination.as_dict(),
                        "next_steps": ["repair_staffing_or_routing_policy"],
                    },
                )

            approval_mode = str(policy["approval_mode"])
            intent = self._approval_intent(proposal=proposal, policy_hash=str(policy["effective_policy_hash"]))
            approval_id = str(approval_request_id or "").strip()
            if approval_mode == "human_required" and not approval_id:
                request = self._approvals.ensure_passive_request_in_session(
                    uow.session,
                    tool_name=PROPOSAL_AMEND_TOOL,
                    approval_intent_key=intent,
                    tenant_id=proposal.tenant_id,
                    project_id=proposal.project_id,
                    organization_id=proposal.organization_id,
                    goal_id=proposal.source_goal_id,
                    arguments={
                        "proposal_id": proposal.proposal_id,
                        "proposal_revision": proposal.proposal_revision,
                        "proposal_digest": proposal.envelope_digest,
                        "payload_digest": proposal.payload_digest,
                        "policy_hash": policy["effective_policy_hash"],
                    },
                    target_fingerprint=proposal.envelope_digest,
                    scope={
                        "approval_class": "planning_control_plane",
                        "operation": "proposal_amend",
                        "proposal_id": proposal.proposal_id,
                        "organization_id": proposal.organization_id,
                        "goal_id": proposal.source_goal_id,
                        "policy_hash": policy["effective_policy_hash"],
                        "passive_transition": True,
                    },
                )
                proposal.state = "needs_approval"
                proposal.reason_code = "proposal_approval_required"
                proposal.approval_request_id = request.id
                proposal.decision = {
                    "classification": "needs_approval",
                    "policy_hash": policy["effective_policy_hash"],
                    "approval_request_id": request.id,
                    "proposal_revision": proposal.proposal_revision,
                    "proposal_digest": proposal.envelope_digest,
                    "category_artifact_revision_id": category.id,
                    "category_revision": category.revision,
                    "category_digest": category.content_digest,
                    "source_track_artifact_revision_id": adopted_track.id,
                    "source_track_revision": adopted_track.revision,
                    "source_track_digest": adopted_track.content_digest,
                    "destination": destination.as_dict(),
                    "next_steps": ["grant_or_deny_proposal_amendment"],
                }
                uow.session.add(proposal)
                return self._response(proposal, replayed=False)

            if approval_mode == "human_required":
                grant = self._approvals.consume_bound_request_in_session(
                    uow.session,
                    request_id=approval_id,
                    tool_name=PROPOSAL_AMEND_TOOL,
                    approval_intent_key=intent,
                    tenant_id=proposal.tenant_id,
                    project_id=proposal.project_id,
                    goal_id=proposal.source_goal_id,
                    organization_id=proposal.organization_id,
                )
                if str(grant.decided_by or "") == proposal.proposing_worker_id:
                    raise PlanningTransitionError("proposal_self_approval_forbidden")

            amendment, lineage = self._build_amendment(
                uow=uow,
                proposal=proposal,
                adopted_track=adopted_track,
                actor=context.subject_id,
                proposal_policy_hash=str(policy["effective_policy_hash"]),
                assignment=assignment,
                destination=destination,
            )
            uow.planning.add_revision(amendment)
            uow.planning.add_lineage(lineage)
            proposal.state = "accepted_as_plan_amendment"
            proposal.reason_code = "proposal_accepted_as_plan_amendment"
            proposal.decision = {
                "classification": "accepted_as_plan_amendment",
                "policy_hash": policy["effective_policy_hash"],
                "proposal_revision": proposal.proposal_revision,
                "proposal_digest": proposal.envelope_digest,
                "category_artifact_revision_id": category.id,
                "category_revision": category.revision,
                "category_digest": category.content_digest,
                "source_track_artifact_revision_id": adopted_track.id,
                "source_track_revision": adopted_track.revision,
                "source_track_digest": adopted_track.content_digest,
                "amendment_track_artifact_revision_id": amendment.id,
                "amendment_track_revision": amendment.revision,
                "amendment_track_digest": amendment.content_digest,
                "suggested_targets_authoritative": False,
                "routing_owner": "hub",
                "materialization_owner": "hub",
                "destination": destination.as_dict(),
                "next_steps": ["adopt_track_revision", "materialize_with_separate_grant"],
            }
            proposal.approval_request_id = approval_id or None
            proposal.amendment_track_revision_id = amendment.id
            proposal.decided_at = time.time()
            proposal.decided_by = context.subject_id
            uow.session.add(proposal)
        self._audit(proposal)
        return self._response(proposal, replayed=False)

    def reject(
        self,
        *,
        proposal_id: str,
        context: PlanningOperationContext,
        source_goal_id: str,
        expected_revision: int,
        expected_digest: str,
    ) -> dict[str, Any]:
        """Record an immutable Hub rejection without creating a Task or Track."""

        self._authorize(context)
        scope_key = (
            f"proposal-decision:{context.tenant_id}:{context.project_id}:{context.organization_id}:{source_goal_id}"
        )
        with planning_scope_lock(scope_key), self._uow_factory() as uow:
            assert uow.planning is not None and uow.session is not None
            uow.planning.acquire_scope_lock(scope_key)
            proposal = uow.planning.get_proposal(proposal_id, for_update=True)
            if proposal is None:
                raise PlanningTransitionError("worker_task_proposal_not_found")
            self._validate_scope(context=context, proposal=proposal)
            if proposal.source_goal_id != str(source_goal_id or ""):
                raise PlanningTransitionError("planning_scope_forbidden")
            self._validate_precondition(
                proposal=proposal,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if proposal.state == "rejected":
                return self._response(proposal, replayed=True)
            if proposal.state in self._TERMINAL_STATES:
                raise PlanningTransitionError("worker_task_proposal_decision_conflict")
            self._deny_pending_approval_in_session(
                uow=uow,
                proposal=proposal,
                actor=context.subject_id,
            )
            response = self._finish(
                uow=uow,
                proposal=proposal,
                state="rejected",
                reason_code="proposal_operator_rejected",
                actor=context.subject_id,
                decision={
                    "routing_owner": "hub",
                    "materialization_owner": "hub",
                    "next_steps": [],
                },
            )
        self._audit(proposal)
        return response

    @staticmethod
    def _deny_pending_approval_in_session(
        *,
        uow: PlanningControlUnitOfWork,
        proposal: WorkerTaskProposalDB,
        actor: str,
    ) -> None:
        assert uow.session is not None
        request_id = str(proposal.approval_request_id or "")
        if not request_id:
            return
        approval = uow.session.get(ApprovalRequestDB, request_id)
        if approval is None:
            raise PlanningTransitionError("planning_approval_not_found")
        if (
            approval.tool_name != PROPOSAL_AMEND_TOOL
            or approval.tenant_id != proposal.tenant_id
            or approval.project_id != proposal.project_id
            or approval.organization_id != proposal.organization_id
            or approval.goal_id != proposal.source_goal_id
        ):
            raise PlanningTransitionError("planning_approval_scope_mismatch")
        if approval.status == "pending":
            approval.status = "denied"
            approval.decided_at = time.time()
            approval.decided_by = actor
            approval.decision_reason = "organization_planning_proposal_rejected"
            uow.session.add(approval)

    def _build_amendment(
        self,
        *,
        uow: PlanningControlUnitOfWork,
        proposal: WorkerTaskProposalDB,
        adopted_track: PlanningArtifactRevisionDB,
        actor: str,
        proposal_policy_hash: str,
        assignment: AssignmentProposalScope,
        destination: OrganizationProposalDestination,
    ) -> tuple[PlanningArtifactRevisionDB, list[PlanningLineageDB]]:
        assert uow.planning is not None
        payload = copy.deepcopy(dict(adopted_track.payload or {}))
        proposal_payload = dict(proposal.envelope.get("payload") or {})
        tasks = [dict(row) for row in list(payload.get("tasks") or []) if isinstance(row, Mapping)]
        existing_ids = {str(row.get("id") or "") for row in tasks}
        proposed_id = f"PROP-{hashlib.sha256(proposal.proposal_id.encode('utf-8')).hexdigest()[:12]}"
        dependency_refs = [str(value) for value in list(proposal_payload.get("dependency_refs") or []) if str(value)]
        dependency_ids = [value.split(":", 1)[-1] for value in dependency_refs]
        if proposed_id in existing_ids:
            raise PlanningTransitionError("proposal_plan_task_id_conflict")
        if proposed_id in dependency_ids or any(value not in existing_ids for value in dependency_ids):
            raise PlanningTransitionError("proposal_dependency_unknown")
        task = {
            "id": proposed_id,
            "title": str(proposal_payload.get("title") or "")[:300],
            "description": str(proposal_payload.get("description") or "")[:10000],
            "status": "todo",
            "priority": "P2"
            if "P2" in list(payload.get("priority_scale") or [])
            else str(list(payload.get("priority_scale") or ["P1"])[0]),
            "risk": str(proposal_payload.get("risk") or "medium"),
            "type": str(proposal_payload.get("task_kind") or "implementation"),
            "depends_on": dependency_refs,
            "acceptance_criteria": list(proposal_payload.get("acceptance_criteria") or []),
            "required_capabilities": list(proposal_payload.get("required_capabilities") or []),
            "expected_outputs": list(proposal_payload.get("expected_outputs") or []),
            "context_refs": list(proposal_payload.get("context_refs") or []),
            "evidence_refs": list(proposal_payload.get("evidence_refs") or []),
            "allowed_source_refs": sorted(
                value for value in assignment.allowed_evidence_refs if str(value).startswith("SRC_")
            ),
            "allowed_run_refs": sorted(
                value for value in assignment.allowed_evidence_refs if str(value).startswith("RUN_")
            ),
            "budget_estimate": dict(proposal_payload.get("budget_estimate") or {}),
            "source_category_item_ids": list(proposal.source_category_item_ids or []),
            "organization_binding": {
                "unit_id": destination.unit_id,
                "team_id": destination.team_id,
                "role_slot_id": destination.role_slot_id,
            },
            "hub_destination_decision": destination.as_dict(),
            "proposal_id": proposal.proposal_id,
            "suggested_role_refs": list(proposal_payload.get("suggested_role_refs") or []),
            "suggested_team_refs": list(proposal_payload.get("suggested_team_refs") or []),
            "suggested_agent_refs": list(proposal_payload.get("suggested_agent_refs") or []),
            "target_role_hint": self._first_value(proposal_payload, "suggested_role_refs"),
            "target_team_hint": self._first_value(proposal_payload, "suggested_team_refs"),
            "target_agent_hint": self._first_value(proposal_payload, "suggested_agent_refs"),
            "suggested_targets_authoritative": False,
            "remaining_proposal_budget": self._remaining_budget(
                remaining=dict(assignment.remaining_budget or {}),
                estimate=dict(proposal_payload.get("budget_estimate") or {}),
            ),
            "amendment_depth": proposal.amendment_depth + 1,
        }
        tasks.append(task)
        graph = {
            str(row.get("id") or ""): [
                str(dep).split(":", 1)[-1] for dep in list(row.get("depends_on") or []) if str(dep)
            ]
            for row in tasks
        }
        valid_dag, reason = validate_dependency_graph(graph)
        if not valid_dag:
            raise PlanningTransitionError(reason or "proposal_dependency_cycle")
        payload["tasks"] = tasks
        payload, summary_issues = PlanningSummaryEngine().recompute(payload)
        schema_issues = validate_planning_track_with_details(payload)
        quality = evaluate_planning_quality_gates(
            payload,
            large_goal_mode=bool(payload.get("large_goal_mode")),
            small_goal_mode=bool(payload.get("small_goal_mode")),
        )
        if schema_issues or summary_issues or not bool(quality.get("ok")):
            raise PlanningTransitionError("proposal_amendment_track_invalid")
        digest = stable_planning_digest(payload)
        revision_number = uow.planning.next_revision_number(artifact_id=adopted_track.artifact_id)
        revision_seed = (f"{adopted_track.artifact_id}:{revision_number}:{digest}").encode("utf-8")
        revision_id = f"ptrk-{hashlib.sha256(revision_seed).hexdigest()[:24]}"
        amendment = PlanningArtifactRevisionDB(
            id=revision_id,
            artifact_id=adopted_track.artifact_id,
            revision=revision_number,
            artifact_type="planning_track",
            tenant_id=adopted_track.tenant_id,
            project_id=adopted_track.project_id,
            organization_id=adopted_track.organization_id,
            goal_id=adopted_track.goal_id,
            status="valid",
            payload=payload,
            content_digest=digest,
            schema_ref="todos/todo.track.schema.json",
            schema_hash=planning_contract_hash(),
            prompt_hash="",
            policy_hash=adopted_track.policy_hash,
            source_catalog_id=adopted_track.source_catalog_id,
            source_catalog_hash=adopted_track.source_catalog_hash,
            allowed_source_refs=list(adopted_track.allowed_source_refs or []),
            allowed_run_refs=list(adopted_track.allowed_run_refs or []),
            source_category_item_ids=list(adopted_track.source_category_item_ids or []),
            execution_provenance={
                "schema": "planning_proposal_amendment_provenance.v1",
                "proposal_id": proposal.proposal_id,
                "proposal_revision": proposal.proposal_revision,
                "proposal_digest": proposal.envelope_digest,
                "payload_digest": proposal.payload_digest,
                "proposal_policy_hash": proposal_policy_hash,
                "amendment_depth": proposal.amendment_depth + 1,
                "source_category_digest": str(adopted_track.execution_provenance.get("source_category_digest") or ""),
                "created_by_hub_actor": actor,
            },
            validation_result={
                "valid": True,
                "summary_recalculation_status": "recalculated",
                "quality_gate_warnings": list(quality.get("warnings") or []),
            },
            parent_revision_id=adopted_track.parent_revision_id,
            supersedes_revision_id=adopted_track.id,
            created_by=f"hub:proposal:{proposal.proposal_id}",
            created_by_principal_id=canonical_planning_actor_id(actor),
        )
        old_lineage = uow.planning.list_lineage_for_track(adopted_track.id)
        lineage = [
            PlanningLineageDB(
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                organization_id=row.organization_id,
                goal_id=row.goal_id,
                category_revision_id=row.category_revision_id,
                track_revision_id=revision_id,
                source_category_item_id=row.source_category_item_id,
                plan_task_id=row.plan_task_id,
            )
            for row in old_lineage
        ]
        lineage.extend(
            PlanningLineageDB(
                tenant_id=proposal.tenant_id,
                project_id=proposal.project_id,
                organization_id=proposal.organization_id,
                goal_id=proposal.source_goal_id,
                category_revision_id=str(adopted_track.parent_revision_id or ""),
                track_revision_id=revision_id,
                source_category_item_id=item_id,
                plan_task_id=proposed_id,
            )
            for item_id in list(proposal.source_category_item_ids or [])
        )
        return amendment, lineage

    @staticmethod
    def _first_value(payload: Mapping[str, Any], field: str) -> str | None:
        values = sorted(str(value) for value in list(payload.get(field) or []) if str(value))
        return values[0] if values else None

    @staticmethod
    def _remaining_budget(
        *,
        remaining: Mapping[str, Any],
        estimate: Mapping[str, Any],
    ) -> dict[str, float]:
        return {
            key: max(0.0, float(value) - float(estimate.get(key) or 0))
            for key, value in remaining.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }

    @staticmethod
    def _adopted_track(
        *, uow: PlanningControlUnitOfWork, proposal: WorkerTaskProposalDB
    ) -> PlanningArtifactRevisionDB | None:
        assert uow.planning is not None and uow.session is not None
        source_task = uow.session.get(TaskDB, proposal.source_task_id)
        if (
            source_task is None
            or str(source_task.tenant_id or "") != proposal.tenant_id
            or str(source_task.project_id or "") != proposal.project_id
            or str(source_task.organization_id or "") != proposal.organization_id
        ):
            return None
        lineage = dict(dict(source_task.worker_execution_context or {}).get("planning_lineage") or {})
        track_revision_id = str(lineage.get("track_revision_id") or "")
        plan_task_id = str(lineage.get("plan_task_id") or "")
        if (
            lineage.get("schema") != "organization_planning_lineage.v1"
            or str(lineage.get("organization_id") or "") != proposal.organization_id
            or not track_revision_id
            or not plan_task_id
            or not set(proposal.source_category_item_ids or []).issubset(
                {str(value) for value in list(lineage.get("source_category_item_ids") or [])}
            )
        ):
            return None
        track = uow.planning.get_revision(track_revision_id)
        mapping = uow.planning.get_mapping(
            track_revision_id=track_revision_id,
            plan_task_id=plan_task_id,
        )
        if (
            track is None
            or track.artifact_type != "planning_track"
            or track.status != "adopted"
            or track.tenant_id != proposal.tenant_id
            or track.project_id != proposal.project_id
            or track.organization_id != proposal.organization_id
            or track.goal_id != proposal.source_goal_id
            or mapping is None
            or mapping.internal_task_id != source_task.id
            or mapping.organization_id != proposal.organization_id
            or mapping.goal_id != proposal.source_goal_id
            or mapping.category_revision_id != str(track.parent_revision_id or "")
            or not set(proposal.source_category_item_ids or []).issubset(set(mapping.source_category_item_ids or []))
            or not set(proposal.source_category_item_ids or []).issubset(set(track.source_category_item_ids or []))
        ):
            return None
        return track

    @staticmethod
    def _approval_intent(*, proposal: WorkerTaskProposalDB, policy_hash: str) -> str:
        return canonical_approval_intent_key(
            tenant_id=proposal.tenant_id,
            project_id=proposal.project_id,
            organization_id=proposal.organization_id,
            goal_id=proposal.source_goal_id,
            operation="proposal_amend",
            artifact_revision_id=proposal.proposal_id,
            artifact_digest=proposal.envelope_digest,
            policy_hash=policy_hash,
        )

    @staticmethod
    def _finish(
        *,
        uow: PlanningControlUnitOfWork,
        proposal: WorkerTaskProposalDB,
        state: str,
        reason_code: str,
        actor: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        assert uow.session is not None
        proposal.state = state
        proposal.reason_code = reason_code
        proposal.decision = {"classification": state, "reason_code": reason_code, **decision}
        proposal.decided_at = time.time()
        proposal.decided_by = actor
        uow.session.add(proposal)
        return WorkerTaskProposalDecisionService._response(proposal, replayed=False)

    @staticmethod
    def _authorize(context: PlanningOperationContext) -> None:
        if not context.hub_owned:
            raise PlanningTransitionError("planning_hub_authority_required")
        if "organization_admin" not in context.roles and "proposal_classify" not in context.allowed_operations:
            raise PlanningTransitionError("proposal_classification_authority_required")

    @staticmethod
    def _validate_scope(*, context: PlanningOperationContext, proposal: WorkerTaskProposalDB) -> None:
        if (
            proposal.tenant_id != context.tenant_id
            or proposal.project_id != context.project_id
            or proposal.organization_id != context.organization_id
        ):
            raise PlanningTransitionError("planning_scope_forbidden")

    @staticmethod
    def _validate_precondition(
        *,
        proposal: WorkerTaskProposalDB,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        if expected_revision is not None and proposal.proposal_revision != int(expected_revision):
            raise PlanningTransitionError("worker_task_proposal_precondition_failed")
        if expected_digest is not None and proposal.envelope_digest != str(expected_digest or ""):
            raise PlanningTransitionError("worker_task_proposal_precondition_failed")

    @staticmethod
    def _response(proposal: WorkerTaskProposalDB, *, replayed: bool) -> dict[str, Any]:
        return {
            "proposal_id": proposal.proposal_id,
            "proposal_revision": proposal.proposal_revision,
            "proposal_digest": proposal.envelope_digest,
            "payload_digest": proposal.payload_digest,
            "state": proposal.state,
            "reason_code": proposal.reason_code,
            "approval_request_id": proposal.approval_request_id,
            "amendment_track_revision_id": proposal.amendment_track_revision_id,
            "amendment_track_artifact_revision_id": proposal.amendment_track_revision_id,
            "amendment_track_revision": dict(proposal.decision or {}).get("amendment_track_revision"),
            "amendment_track_digest": dict(proposal.decision or {}).get("amendment_track_digest"),
            "decision": dict(proposal.decision or {}),
            "replayed": replayed,
            "task_created": False,
            "queue_write": False,
        }

    @staticmethod
    def _audit(proposal: WorkerTaskProposalDB) -> None:
        try:
            from agent.common.audit import log_audit

            log_audit(
                "worker_task_proposal_decided",
                {
                    "proposal_id": proposal.proposal_id,
                    "state": proposal.state,
                    "reason_code": proposal.reason_code,
                    "organization_id": proposal.organization_id,
                    "goal_id": proposal.source_goal_id,
                    "approval_request_id": proposal.approval_request_id,
                    "amendment_track_revision_id": proposal.amendment_track_revision_id,
                },
            )
        except Exception:
            return


__all__ = ["WorkerTaskProposalDecisionService"]
