from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy import update as sa_update
from sqlmodel import select

from agent.db_models import (
    AgentInfoDB,
    ArchivedTaskDB,
    CrossTeamTaskDependencyDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    PlanningOperationReceiptDB,
    PlanningTaskDispatchDB,
    PlanningTaskMappingDB,
    TaskDB,
)
from agent.services.approval_request_service import (
    ApprovalRequestService,
    canonical_approval_intent_key,
)
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)
from agent.services.organization_planning_adapter import OrganizationPlanningAdapter
from agent.services.organization_routing_service import (
    OrganizationRoutingCandidate,
    OrganizationRoutingRequest,
    OrganizationRoutingService,
    infer_organization_assignment_duties,
)
from agent.services.organization_workflow_task_binding_service import (
    OrganizationWorkflowTaskBindingPort,
    OrganizationWorkflowTaskBindingService,
)
from agent.services.planning_artifact_transition_service import (
    TRACK_MATERIALIZE_TOOL,
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_category_contract_service import stable_planning_digest
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)
from agent.services.planning_principal_identity_service import (
    planning_separation_of_duties_reason,
)
from agent.services.planning_track_contract_service import planning_contract_hash
from agent.services.planning_track_pipeline_service import (
    validate_planning_track_with_details,
)
from agent.services.separation_of_duties_service import (
    DutyAssignment,
    SeparationOfDutiesPolicy,
)
from agent.services.worker_task_proposal_policy_service import (
    WorkerTaskProposalPolicyService,
)


class PlanningTaskMaterializationService:
    """Hub-owned Track -> Task writer, separate from artifact transitions."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
        approval_service: ApprovalRequestService | None = None,
        organization_adapter: OrganizationPlanningAdapter | None = None,
        routing_service: OrganizationRoutingService | None = None,
        assignment_eligibility: OrganizationAssignmentEligibilityService | None = None,
        workflow_task_bindings: OrganizationWorkflowTaskBindingPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory or PlanningControlUnitOfWork
        self._approvals = approval_service or ApprovalRequestService()
        self._organization_adapter = organization_adapter or OrganizationPlanningAdapter()
        self._routing = routing_service or OrganizationRoutingService()
        self._assignment_eligibility = assignment_eligibility or OrganizationAssignmentEligibilityService()
        self._workflow_task_bindings = workflow_task_bindings or OrganizationWorkflowTaskBindingService()

    def materialize(
        self,
        *,
        context: PlanningOperationContext,
        track_revision_id: str,
        expected_track_digest: str,
        expected_policy_hash: str,
        approval_request_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._authorize(context)
        if not str(approval_request_id or "").strip():
            raise PlanningTransitionError("planning_materialization_approval_required")
        if not str(idempotency_key or "").strip():
            raise PlanningTransitionError("planning_idempotency_key_required")
        with planning_scope_lock(f"planning-materialize:{track_revision_id}"), self._uow_factory() as uow:
            assert uow.planning is not None and uow.session is not None
            uow.planning.acquire_scope_lock(f"planning-materialize:{track_revision_id}")
            track = uow.planning.get_revision(track_revision_id, for_update=True)
            if track is None or track.artifact_type != "planning_track":
                raise PlanningTransitionError("planning_track_revision_not_found")
            self._validate_scope(context=context, row=track)
            if track.status != "adopted":
                raise PlanningTransitionError("planning_track_not_adopted")
            if track.content_digest != str(expected_track_digest or ""):
                raise PlanningTransitionError("planning_revision_digest_mismatch")
            if track.policy_hash != str(expected_policy_hash or ""):
                raise PlanningTransitionError("planning_policy_hash_stale")
            if stable_planning_digest(track.payload) != track.content_digest:
                raise PlanningTransitionError("planning_track_payload_digest_stale")
            if track.schema_hash != planning_contract_hash():
                raise PlanningTransitionError("planning_track_schema_hash_stale")
            if not bool(dict(track.validation_result or {}).get("valid")) or validate_planning_track_with_details(
                dict(track.payload or {})
            ):
                raise PlanningTransitionError("planning_track_not_valid")
            category = uow.planning.get_revision(str(track.parent_revision_id or ""), for_update=True)
            if category is None or category.status != "promoted":
                raise PlanningTransitionError("planning_category_not_promoted")
            if str(track.execution_provenance.get("source_category_digest") or "") != category.content_digest:
                raise PlanningTransitionError("planning_category_lineage_stale")

            tasks = [dict(row) for row in list(track.payload.get("tasks") or []) if isinstance(row, Mapping)]

            intent = canonical_approval_intent_key(
                tenant_id=track.tenant_id,
                project_id=track.project_id,
                organization_id=track.organization_id,
                goal_id=track.goal_id,
                operation="track_materialize",
                artifact_revision_id=track.id,
                artifact_digest=track.content_digest,
                policy_hash=track.policy_hash,
            )
            prior_receipt = uow.planning.get_receipt_by_intent(
                approval_intent_key=intent,
                operation="track_materialize",
            )
            if prior_receipt is not None:
                runtime_contracts = self._workflow_task_bindings.contracts(
                    track=track,
                    tasks=tasks,
                    current_definition_revision=(
                        str(track.payload.get("definition_revision") or "")
                        if any("organization_workflow_step_binding" in task for task in tasks)
                        else None
                    ),
                )
                mappings = uow.planning.list_mappings(track.id)
                self._verify_committed_materialization(
                    uow=uow,
                    receipt=prior_receipt,
                    mappings=mappings,
                    runtime_contracts=runtime_contracts,
                    expected_plan_task_ids={
                        str(row.get("id") or "")
                        for row in list(track.payload.get("tasks") or [])
                        if isinstance(row, Mapping) and str(row.get("id") or "")
                    },
                )
                return self._materialization_response(prior_receipt, mappings)

            current_definition_revision = (
                self._workflow_task_bindings.current_definition_revision(
                    session=uow.session,
                    track=track,
                )
                if any("organization_workflow_step_binding" in task for task in tasks)
                else None
            )
            runtime_contracts = self._workflow_task_bindings.contracts(
                track=track,
                tasks=tasks,
                current_definition_revision=current_definition_revision,
            )

            grant = self._approvals.consume_bound_request_in_session(
                uow.session,
                request_id=approval_request_id,
                tool_name=TRACK_MATERIALIZE_TOOL,
                approval_intent_key=intent,
                tenant_id=track.tenant_id,
                project_id=track.project_id,
                goal_id=track.goal_id,
                organization_id=track.organization_id,
            )
            sod_reason = planning_separation_of_duties_reason(
                revision=track,
                decided_by=grant.decided_by,
            )
            if sod_reason is not None:
                raise PlanningTransitionError(sod_reason)
            lineage: dict[str, list[Any]] = {}
            for row in uow.planning.list_lineage_for_track(track.id):
                lineage.setdefault(row.plan_task_id, []).append(row)
            plan_ids = {str(row.get("id") or "") for row in tasks if str(row.get("id") or "")}
            if len(plan_ids) != len(tasks) or set(lineage) != plan_ids:
                raise PlanningTransitionError("planning_task_lineage_incomplete")

            receipt_id = self._stable_id("pmat", intent, idempotency_key)
            replan = dict(track.payload.get("planning_replan") or {})
            retained_plan_task_ids = {
                str(value) for value in list(replan.get("retained_plan_task_ids") or []) if str(value)
            }
            source_mapping_by_plan_id: dict[str, PlanningTaskMappingDB] = {}
            if retained_plan_task_ids:
                source_track = uow.planning.get_revision(str(replan.get("source_track_artifact_revision_id") or ""))
                if (
                    source_track is None
                    or source_track.content_digest != str(replan.get("source_track_digest") or "")
                    or source_track.revision != int(replan.get("source_track_revision") or 0)
                ):
                    raise PlanningTransitionError("planning_replan_source_stale")
                source_mapping_by_plan_id = {
                    row.plan_task_id: row
                    for row in uow.planning.list_mappings(source_track.id)
                    if row.plan_task_id in retained_plan_task_ids
                }
                if set(source_mapping_by_plan_id) != retained_plan_task_ids:
                    raise PlanningTransitionError("planning_replan_mapping_incomplete")
            internal_task_ids = {
                plan_task_id: (
                    source_mapping_by_plan_id[plan_task_id].internal_task_id
                    if plan_task_id in source_mapping_by_plan_id
                    else self._stable_id("ptask", track.id, plan_task_id)
                )
                for plan_task_id in plan_ids
            }
            structure = self._organization_adapter.stage_structure(
                uow=uow,
                track=track,
                tasks=tasks,
                internal_task_ids=internal_task_ids,
            )
            self._mark_replaced_source_tasks(
                uow=uow,
                replacement_track=track,
                replan=replan,
            )
            mapping_by_plan_id: dict[str, PlanningTaskMappingDB] = {}
            for task in tasks:
                plan_task_id = str(task.get("id") or "")
                binding = self._task_binding(task)
                internal_task_id = internal_task_ids[plan_task_id]
                structure_binding = structure.task_bindings[plan_task_id]
                mapping_by_plan_id[plan_task_id] = PlanningTaskMappingDB(
                    tenant_id=track.tenant_id,
                    project_id=track.project_id,
                    organization_id=track.organization_id,
                    goal_id=track.goal_id,
                    execution_goal_id=structure_binding.execution_goal_id,
                    category_revision_id=category.id,
                    track_revision_id=track.id,
                    source_category_item_ids=sorted({row.source_category_item_id for row in lineage[plan_task_id]}),
                    plan_task_id=plan_task_id,
                    internal_task_id=internal_task_id,
                    unit_id=binding["unit_id"],
                    team_id=binding["team_id"],
                    role_slot_id=binding["role_slot_id"],
                    materialization_receipt_id=receipt_id,
                )

            receipt = PlanningOperationReceiptDB(
                id=receipt_id,
                tenant_id=track.tenant_id,
                project_id=track.project_id,
                organization_id=track.organization_id,
                goal_id=track.goal_id,
                artifact_revision_id=track.id,
                operation="track_materialize",
                approval_intent_key=intent,
                approval_request_id=approval_request_id,
                idempotency_key=idempotency_key,
                artifact_digest=track.content_digest,
                policy_hash=track.policy_hash,
                details={},
            )
            # The durable operation receipt is the parent of every mapping.
            # Staging it first keeps FK ordering deterministic on all engines.
            uow.planning.add_receipt(receipt)

            topology_index = self._organization_topology_index(
                session=uow.session,
                track=track,
            )
            created_ids: list[str] = []
            for task in tasks:
                plan_task_id = str(task.get("id") or "")
                mapping = mapping_by_plan_id[plan_task_id]
                structure_binding = structure.task_bindings[plan_task_id]
                existing_mapping = uow.planning.get_mapping(
                    track_revision_id=track.id,
                    plan_task_id=plan_task_id,
                )
                if existing_mapping is not None:
                    if existing_mapping.internal_task_id != mapping.internal_task_id:
                        raise PlanningTransitionError("planning_task_mapping_conflict")
                    mapping = existing_mapping
                    mapping_by_plan_id[plan_task_id] = existing_mapping
                dependencies = self._resolve_dependencies(
                    uow=uow,
                    track=track,
                    task=task,
                    local_mappings=mapping_by_plan_id,
                )
                existing_task = uow.session.get(TaskDB, mapping.internal_task_id)
                if existing_task is not None:
                    self._verify_existing_task(
                        existing_task,
                        mapping,
                        runtime_contract=runtime_contracts[plan_task_id],
                    )
                    created_ids.append(existing_task.id)
                else:
                    proposal_policy = dict(
                        WorkerTaskProposalPolicyService().validate_policy(
                            task.get("task_proposal_policy")
                            if isinstance(task.get("task_proposal_policy"), dict)
                            else None
                        )["policy"]
                    )
                    runtime_task = TaskDB(
                        id=mapping.internal_task_id,
                        title=str(task.get("title") or plan_task_id)[:200],
                        description=self._description(task),
                        status="todo" if not dependencies else "blocked_by_dependency",
                        priority=str(task.get("priority") or "Medium"),
                        tenant_id=track.tenant_id,
                        project_id=track.project_id,
                        organization_id=track.organization_id,
                        unit_id=mapping.unit_id,
                        team_id=mapping.team_id,
                        role_slot_id=mapping.role_slot_id,
                        goal_id=structure_binding.execution_goal_id,
                        plan_id=structure_binding.plan_id,
                        plan_node_id=structure_binding.plan_node_id,
                        task_kind=str(task.get("task_kind") or task.get("type") or "implementation"),
                        required_capabilities=[
                            str(value) for value in list(task.get("required_capabilities") or []) if str(value)
                        ],
                        depends_on=dependencies,
                        worker_execution_context={
                            "planning_lineage": {
                                "schema": "organization_planning_lineage.v1",
                                "organization_id": track.organization_id,
                                "organization_goal_id": track.goal_id,
                                "team_goal_id": structure_binding.execution_goal_id,
                                "category_revision_id": category.id,
                                "category_digest": category.content_digest,
                                "track_revision_id": track.id,
                                "track_digest": track.content_digest,
                                "source_category_item_ids": list(mapping.source_category_item_ids or []),
                                "plan_task_id": plan_task_id,
                                "materialization_receipt_id": receipt_id,
                                "amendment_depth": int(
                                    task.get("amendment_depth")
                                    or dict(track.execution_provenance or {}).get("amendment_depth")
                                    or 0
                                ),
                            },
                            "allowed_source_refs": self._task_evidence_refs(
                                task=task,
                                field="allowed_source_refs",
                                track_refs=list(track.allowed_source_refs or []),
                            ),
                            "allowed_run_refs": self._task_evidence_refs(
                                task=task,
                                field="allowed_run_refs",
                                track_refs=list(track.allowed_run_refs or []),
                            ),
                            "role_template_ref": self._role_template_ref(
                                uow=uow,
                                track=track,
                                task=task,
                                role_slot_id=mapping.role_slot_id,
                            ),
                            "task_proposal_policy": proposal_policy,
                            "allowed_context_refs": [
                                str(value) for value in list(task.get("context_refs") or []) if str(value)
                            ],
                            "risk_level": str(task.get("risk") or "medium").lower(),
                            "routing_hints": {
                                "target_role_hint": str(task.get("target_role_hint") or "") or None,
                                "target_team_hint": str(task.get("target_team_hint") or "") or None,
                                "target_agent_hint": str(task.get("target_agent_hint") or "") or None,
                            },
                            "remaining_proposal_budget": (
                                dict(task.get("remaining_proposal_budget") or {})
                                if isinstance(task.get("remaining_proposal_budget"), dict)
                                else {}
                            ),
                            "organization_topology_refs": self._authorized_topology_refs(
                                topology_index=topology_index,
                                unit_id=str(mapping.unit_id or ""),
                                team_id=str(mapping.team_id or ""),
                                proposal_policy=proposal_policy,
                            ),
                            **(
                                {
                                    "organization_workflow_step_binding": runtime_contracts[plan_task_id][
                                        "workflow_binding"
                                    ]
                                }
                                if runtime_contracts[plan_task_id]["workflow_binding"] is not None
                                else {}
                            ),
                        },
                        verification_spec=runtime_contracts[plan_task_id]["verification_spec"],
                        history=[
                            {
                                "timestamp": time.time(),
                                "status": "todo" if not dependencies else "blocked_by_dependency",
                                "event_type": "organization_planning_task_materialized",
                                "actor": "hub:planning_task_materialization",
                                "details": {
                                    "track_revision_id": track.id,
                                    "plan_task_id": plan_task_id,
                                    "materialization_receipt_id": receipt_id,
                                },
                            }
                        ],
                    )
                    uow.session.add(runtime_task)
                    # The active Task is the runtime parent of its immutable
                    # planning mapping; flush it before the mapping insert.
                    uow.session.flush()
                    created_ids.append(runtime_task.id)
                if existing_mapping is None:
                    uow.planning.add_mapping(mapping)

            self._stage_cross_team_dependencies(
                uow=uow,
                track=track,
                tasks=tasks,
                mappings=mapping_by_plan_id,
            )

            receipt.details = {
                "materialized_task_ids": created_ids,
                "mapping_count": len(mapping_by_plan_id),
            }
            uow.session.add(receipt)
        return self._materialization_response(receipt, list(mapping_by_plan_id.values()))

    def claim_next(
        self,
        *,
        context: PlanningOperationContext,
        track_revision_id: str,
        plan_task_id: str,
        idempotency_key: str,
        requested_worker_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a durable dispatch intent; never materialize implicitly."""
        self._authorize(context)
        if not str(idempotency_key or "").strip():
            raise PlanningTransitionError("planning_idempotency_key_required")
        with planning_scope_lock(f"planning-dispatch:{track_revision_id}:{plan_task_id}"), self._uow_factory() as uow:
            assert uow.planning is not None and uow.session is not None
            uow.planning.acquire_scope_lock(f"planning-dispatch:{track_revision_id}:{plan_task_id}")
            track = uow.planning.get_revision(track_revision_id, for_update=True)
            if track is None or track.status != "adopted":
                raise PlanningTransitionError("planning_track_not_adopted")
            self._validate_scope(context=context, row=track)
            mapping = uow.planning.get_mapping(
                track_revision_id=track.id,
                plan_task_id=plan_task_id,
            )
            if mapping is None:
                raise PlanningTransitionError("plan_task_not_materialized")
            receipt = uow.planning.get_receipt(mapping.materialization_receipt_id)
            if (
                receipt is None
                or receipt.operation != "track_materialize"
                or receipt.artifact_revision_id != track.id
                or receipt.status != "committed"
            ):
                raise PlanningTransitionError("plan_task_not_materialized")
            prior = uow.planning.get_dispatch_by_idempotency(
                organization_id=track.organization_id,
                idempotency_key=idempotency_key,
            )
            if prior is not None:
                if prior.task_mapping_id != mapping.id:
                    raise PlanningTransitionError("planning_dispatch_idempotency_conflict")
                return self._dispatch_response(prior)
            task = uow.session.get(TaskDB, mapping.internal_task_id)
            if task is None:
                raise PlanningTransitionError("plan_task_not_materialized")
            if (
                str(task.tenant_id or "") != track.tenant_id
                or str(task.project_id or "") != track.project_id
                or str(task.organization_id or "") != track.organization_id
                or str(task.goal_id or "") != mapping.execution_goal_id
                or task.current_worker_job_id is not None
            ):
                raise PlanningTransitionError("plan_task_dispatch_binding_invalid")
            for dependency_id in list(task.depends_on or []):
                dependency = uow.session.get(TaskDB, str(dependency_id))
                if dependency is None or str(dependency.status) != "completed":
                    raise PlanningTransitionError("plan_task_dependencies_not_ready")
            routing = self._route_task(
                session=uow.session,
                track=track,
                task=task,
                target_agent_hint=requested_worker_id,
            )
            selected_worker_id = str(routing["selected_agent_id"])
            attempt = 1
            dispatch_id = self._stable_id("pdispatch", mapping.id, str(attempt))
            lease_id = self._stable_id("please", dispatch_id, idempotency_key)
            transition = uow.session.exec(
                sa_update(TaskDB)
                .where(
                    TaskDB.id == task.id,
                    TaskDB.tenant_id == track.tenant_id,
                    TaskDB.project_id == track.project_id,
                    TaskDB.organization_id == track.organization_id,
                    TaskDB.status.in_(("todo", "created", "blocked_by_dependency")),
                    TaskDB.current_worker_job_id.is_(None),
                )
                .values(
                    status="assigned",
                    status_reason_code="planning_dispatch_intent_created",
                    assigned_agent_url=selected_worker_id,
                    worker_execution_context={
                        **dict(task.worker_execution_context or {}),
                        "organization_routing": routing,
                        "planning_dispatch": {
                            "schema": "organization_planning_dispatch.v1",
                            "dispatch_intent_id": dispatch_id,
                            "lease_id": lease_id,
                            "attempt": attempt,
                            "track_revision_id": track.id,
                            "plan_task_id": plan_task_id,
                            "status": "pending_dispatch",
                        },
                    },
                    history=[
                        *list(task.history or []),
                        {
                            "timestamp": time.time(),
                            "status": "assigned",
                            "event_type": "organization_planning_dispatch_intent_created",
                            "actor": "hub:planning_task_materialization",
                            "details": {
                                "dispatch_intent_id": dispatch_id,
                                "lease_id": lease_id,
                                "attempt": attempt,
                                "track_revision_id": track.id,
                                "plan_task_id": plan_task_id,
                            },
                        },
                    ],
                    updated_at=time.time(),
                )
            )
            if int(getattr(transition, "rowcount", 0) or 0) != 1:
                raise PlanningTransitionError("plan_task_already_dispatched")
            dispatch = PlanningTaskDispatchDB(
                id=dispatch_id,
                tenant_id=track.tenant_id,
                project_id=track.project_id,
                organization_id=track.organization_id,
                goal_id=track.goal_id,
                track_revision_id=track.id,
                task_mapping_id=mapping.id,
                internal_task_id=mapping.internal_task_id,
                dispatch_intent_id=dispatch_id,
                idempotency_key=idempotency_key,
                attempt=attempt,
                lease_id=lease_id,
                requested_worker_id=selected_worker_id,
            )
            uow.planning.add_dispatch(dispatch)
        return self._dispatch_response(dispatch)

    def _route_task(
        self,
        *,
        session,
        track: Any,
        task: TaskDB,
        target_agent_hint: str | None,
    ) -> dict[str, Any]:
        """Resolve and lock the final persisted assignment before queue CAS."""

        slot = session.exec(
            select(OrganizationRoleSlotDB).where(
                OrganizationRoleSlotDB.id == str(task.role_slot_id or ""),
                OrganizationRoleSlotDB.tenant_id == track.tenant_id,
                OrganizationRoleSlotDB.project_id == track.project_id,
                OrganizationRoleSlotDB.organization_id == track.organization_id,
                OrganizationRoleSlotDB.unit_id == str(task.unit_id or ""),
                OrganizationRoleSlotDB.lifecycle == "active",
            )
        ).one_or_none()
        team = session.exec(
            select(OrganizationTeamLinkDB).where(
                OrganizationTeamLinkDB.tenant_id == track.tenant_id,
                OrganizationTeamLinkDB.project_id == track.project_id,
                OrganizationTeamLinkDB.organization_id == track.organization_id,
                OrganizationTeamLinkDB.unit_id == str(task.unit_id or ""),
                OrganizationTeamLinkDB.team_id == str(task.team_id or ""),
                OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")),
            )
        ).one_or_none()
        if slot is None or team is None:
            raise PlanningTransitionError("planning_routing_binding_invalid")

        statement = select(OrganizationRoleAssignmentDB).where(
            OrganizationRoleAssignmentDB.tenant_id == track.tenant_id,
            OrganizationRoleAssignmentDB.project_id == track.project_id,
            OrganizationRoleAssignmentDB.organization_id == track.organization_id,
            OrganizationRoleAssignmentDB.role_slot_id == slot.id,
            OrganizationRoleAssignmentDB.lifecycle == "active",
        )
        if self._supports_row_lock(session):
            statement = statement.with_for_update()
        assignments = list(session.exec(statement).all())
        agent_urls = sorted({row.agent_url for row in assignments if row.agent_url})
        agent_statement = select(AgentInfoDB).where(AgentInfoDB.url.in_(agent_urls))
        if self._supports_row_lock(session):
            agent_statement = agent_statement.with_for_update()
        agents = {row.url: row for row in (list(session.exec(agent_statement).all()) if agent_urls else [])}
        slot_policy = dict(slot.assignment_policy or {})
        required_capabilities = {
            str(value)
            for value in (list(task.required_capabilities or []) + list(slot_policy.get("required_capabilities") or []))
            if str(value)
        }
        forbidden_capabilities = {
            str(value) for value in list(slot_policy.get("forbidden_capabilities") or []) if str(value)
        }
        candidates: list[OrganizationRoutingCandidate] = []
        for assignment in assignments:
            agent = agents.get(assignment.agent_url)
            capacity_used = int(
                session.exec(
                    select(func.count())
                    .select_from(TaskDB)
                    .where(
                        TaskDB.assigned_agent_url == assignment.agent_url,
                        TaskDB.status.in_(("assigned", "in_progress")),
                    )
                ).one()
                or 0
            )
            eligibility = self._assignment_eligibility.evaluate(
                agent=agent,
                required_capabilities=required_capabilities,
                forbidden_capabilities=forbidden_capabilities,
                capacity_used=capacity_used,
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
                    team_id=str(task.team_id or ""),
                    role_slot_id=assignment.role_slot_id,
                    capabilities=eligibility.capabilities,
                    backend=str(metadata.get("backend") or limits.get("backend") or "native"),
                    runtime_target=str(metadata.get("runtime_target") or limits.get("runtime_target") or "default"),
                    max_risk_level=str(metadata.get("max_risk_level") or limits.get("max_risk_level") or "medium"),
                    capacity_used=eligibility.capacity_used,
                    capacity_limit=eligibility.capacity_limit,
                    assignment_status=("active" if eligibility.allowed else "ineligible"),
                    duties=infer_organization_assignment_duties(
                        slot_key=slot.slot_key,
                        role_template_key=slot.role_template_key,
                        assignment_metadata=metadata,
                    ),
                )
            )

        all_assignment_rows = list(
            session.exec(
                select(OrganizationRoleAssignmentDB).where(
                    OrganizationRoleAssignmentDB.tenant_id == track.tenant_id,
                    OrganizationRoleAssignmentDB.project_id == track.project_id,
                    OrganizationRoleAssignmentDB.organization_id == track.organization_id,
                    OrganizationRoleAssignmentDB.lifecycle == "active",
                )
            ).all()
        )
        slot_ids = sorted({row.role_slot_id for row in all_assignment_rows})
        slots = {
            row.id: row
            for row in (
                list(session.exec(select(OrganizationRoleSlotDB).where(OrganizationRoleSlotDB.id.in_(slot_ids))).all())
                if slot_ids
                else []
            )
        }
        team_by_unit = {
            row.unit_id: row.team_id
            for row in session.exec(
                select(OrganizationTeamLinkDB).where(
                    OrganizationTeamLinkDB.tenant_id == track.tenant_id,
                    OrganizationTeamLinkDB.project_id == track.project_id,
                    OrganizationTeamLinkDB.organization_id == track.organization_id,
                    OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")),
                )
            ).all()
        }
        current_duties = tuple(
            DutyAssignment(
                principal_id=row.agent_url,
                role_slot_id=row.role_slot_id,
                team_id=str(team_by_unit.get(getattr(slots.get(row.role_slot_id), "unit_id", "")) or ""),
                duties=infer_organization_assignment_duties(
                    slot_key=slots[row.role_slot_id].slot_key,
                    role_template_key=slots[row.role_slot_id].role_template_key,
                    assignment_metadata=dict(row.assignment_metadata or {}),
                ),
            )
            for row in all_assignment_rows
            if row.role_slot_id in slots
        )
        worker_context = dict(task.worker_execution_context or {})
        hints = dict(worker_context.get("routing_hints") or {})
        decision = self._routing.decide(
            request=OrganizationRoutingRequest(
                organization_id=track.organization_id,
                unit_id=str(task.unit_id or ""),
                task_id=task.id,
                task_kind=str(task.task_kind or "implementation"),
                role_slot_id=slot.id,
                required_capabilities=frozenset(required_capabilities),
                allowed_team_ids=frozenset({str(task.team_id or "")}),
                allowed_backends=frozenset(
                    str(value) for value in list(worker_context.get("allowed_backends") or []) if str(value)
                ),
                allowed_runtime_targets=frozenset(
                    str(value) for value in list(worker_context.get("allowed_runtime_targets") or []) if str(value)
                ),
                risk_level=str(worker_context.get("risk_level") or "medium"),
                effective_policy_hash=track.policy_hash,
                target_role_hint=str(hints.get("target_role_hint") or "") or None,
                target_team_hint=str(hints.get("target_team_hint") or "") or None,
                target_agent_hint=(str(target_agent_hint or hints.get("target_agent_hint") or "") or None),
            ),
            candidates=candidates,
            current_duty_assignments=current_duties,
            sod_policy=SeparationOfDutiesPolicy.enterprise_default(revision=track.policy_hash[:16]),
        )
        if decision.status != "routable" or not all(
            (
                decision.selected_agent_id,
                decision.selected_assignment_id,
                decision.selected_team_id,
                decision.selected_role_slot_id,
            )
        ):
            raise PlanningTransitionError(f"planning_routing_blocked:{decision.reason_code}")
        if decision.selected_team_id != str(task.team_id or "") or decision.selected_role_slot_id != str(
            task.role_slot_id or ""
        ):
            raise PlanningTransitionError("planning_routing_binding_invalid")
        return {
            "schema": "organization_routing_decision.v1",
            "effective_policy_hash": track.policy_hash,
            "decision_hash": decision.policy_hash,
            "reason_code": decision.reason_code,
            "selected_agent_id": decision.selected_agent_id,
            "selected_assignment_id": decision.selected_assignment_id,
            "selected_team_id": decision.selected_team_id,
            "selected_role_slot_id": decision.selected_role_slot_id,
            "candidate_evaluations": [
                {
                    "agent_id": row.agent_id,
                    "assignment_id": row.assignment_id,
                    "team_id": row.team_id,
                    "allowed": row.allowed,
                    "exclusion_reasons": list(row.exclusion_reasons),
                    "capacity_used": row.capacity_used,
                    "capacity_limit": row.capacity_limit,
                }
                for row in decision.candidates
            ],
        }

    @staticmethod
    def _supports_row_lock(session) -> bool:
        return str(getattr(getattr(session.get_bind(), "dialect", None), "name", "")) == "postgresql"

    @staticmethod
    def _resolve_dependencies(
        *,
        uow: PlanningControlUnitOfWork,
        track: Any,
        task: Mapping[str, Any],
        local_mappings: Mapping[str, PlanningTaskMappingDB],
    ) -> list[str]:
        assert uow.planning is not None
        resolved: list[str] = []
        for raw_ref in list(task.get("depends_on") or []):
            ref = str(raw_ref or "").strip()
            local_id = ref.split(":", 1)[-1]
            local = local_mappings.get(local_id)
            if local is not None:
                resolved.append(local.internal_task_id)
                continue
            matches = uow.planning.find_mappings_for_plan_task(goal_id=track.goal_id, plan_task_id=local_id)
            if len(matches) != 1:
                raise PlanningTransitionError("planning_dependency_mapping_unresolved")
            resolved.append(matches[0].internal_task_id)
        return list(dict.fromkeys(resolved))

    @classmethod
    def _stage_cross_team_dependencies(
        cls,
        *,
        uow: PlanningControlUnitOfWork,
        track: Any,
        tasks: list[dict[str, Any]],
        mappings: Mapping[str, PlanningTaskMappingDB],
    ) -> None:
        """Persist the cross-team subset of the authoritative Task DAG."""

        assert uow.session is not None and uow.planning is not None
        task_by_plan_id = {str(task.get("id") or ""): task for task in tasks if str(task.get("id") or "")}
        for target_plan_id, target_task in task_by_plan_id.items():
            target_mapping = mappings[target_plan_id]
            dependency_ids = cls._resolve_dependencies(
                uow=uow,
                track=track,
                task=target_task,
                local_mappings=mappings,
            )
            for source_task_id in dependency_ids:
                source_task = uow.session.get(TaskDB, source_task_id)
                if source_task is None:
                    raise PlanningTransitionError("planning_dependency_task_missing")
                if (
                    str(source_task.tenant_id or "") != track.tenant_id
                    or str(source_task.project_id or "") != track.project_id
                    or str(source_task.organization_id or "") != track.organization_id
                ):
                    raise PlanningTransitionError("planning_dependency_scope_mismatch")
                source_team_id = str(source_task.team_id or "")
                if not source_team_id:
                    raise PlanningTransitionError("planning_dependency_team_binding_missing")
                if source_team_id == target_mapping.team_id:
                    continue
                dependency_id = cls._stable_id(
                    "xdep",
                    track.organization_id,
                    source_task_id,
                    target_mapping.internal_task_id,
                )
                expected = CrossTeamTaskDependencyDB(
                    id=dependency_id,
                    tenant_id=track.tenant_id,
                    project_id=track.project_id,
                    organization_id=track.organization_id,
                    source_task_id=source_task_id,
                    target_task_id=target_mapping.internal_task_id,
                    source_team_id=source_team_id,
                    target_team_id=target_mapping.team_id,
                    owner_ref=target_mapping.role_slot_id,
                    gate_ref=(str(target_task.get("gate_ref") or "").strip() or None),
                    required_artifact_refs=sorted(
                        {
                            str(value).strip()
                            for value in list(target_task.get("required_artifact_refs") or [])
                            if str(value).strip()
                        }
                    ),
                    due_at=cls._optional_due_at(target_task.get("due_at") or target_task.get("due_date")),
                    status="pending",
                    blocking_reason="awaiting_source_task",
                    escalation_policy=(str(target_task.get("escalation_policy") or "hub").strip() or "hub"),
                )
                existing = uow.session.get(CrossTeamTaskDependencyDB, dependency_id)
                if existing is None:
                    uow.session.add(expected)
                    continue
                bindings = (
                    "tenant_id",
                    "project_id",
                    "organization_id",
                    "source_task_id",
                    "target_task_id",
                    "source_team_id",
                    "target_team_id",
                    "owner_ref",
                )
                if any(getattr(existing, key) != getattr(expected, key) for key in bindings):
                    raise PlanningTransitionError("planning_cross_team_dependency_binding_conflict")

    @staticmethod
    def _optional_due_at(value: object) -> float | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise PlanningTransitionError("planning_dependency_due_at_invalid")
        if isinstance(value, (int, float)):
            return float(value)
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (ValueError, OverflowError) as exc:
            raise PlanningTransitionError("planning_dependency_due_at_invalid") from exc

    @staticmethod
    def _task_binding(task: Mapping[str, Any]) -> dict[str, str]:
        nested = PlanningTaskMaterializationService._organization_binding(task)
        binding = {
            "unit_id": str(task.get("unit_id") or nested.get("unit_id") or "").strip(),
            "team_id": str(task.get("team_id") or nested.get("team_id") or "").strip(),
            "role_slot_id": str(task.get("role_slot_id") or nested.get("role_slot_id") or "").strip(),
        }
        if any(not value for value in binding.values()):
            raise PlanningTransitionError("planning_task_organization_binding_required")
        return binding

    @staticmethod
    def _organization_binding(task: Mapping[str, Any]) -> dict[str, Any]:
        value = task.get("organization_binding")
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _task_evidence_refs(
        *,
        task: Mapping[str, Any],
        field: str,
        track_refs: list[str],
    ) -> list[str]:
        """Apply an optional task-level restriction without allowing expansion."""

        authoritative = {str(value) for value in track_refs if str(value)}
        if field not in task:
            return sorted(authoritative)
        raw = task.get(field)
        if not isinstance(raw, list):
            raise PlanningTransitionError("planning_task_evidence_allowlist_invalid")
        requested = {str(value) for value in raw if str(value)}
        if not requested.issubset(authoritative):
            raise PlanningTransitionError("planning_task_evidence_scope_expansion")
        return sorted(requested)

    @staticmethod
    def _role_template_ref(
        *,
        uow: PlanningControlUnitOfWork,
        track: Any,
        task: Mapping[str, Any],
        role_slot_id: str,
    ) -> str:
        """Resolve the authoritative role revision from the bound Hub slot."""

        assert uow.session is not None
        slot = uow.session.get(OrganizationRoleSlotDB, role_slot_id)
        if (
            slot is None
            or slot.tenant_id != track.tenant_id
            or slot.project_id != track.project_id
            or slot.organization_id != track.organization_id
        ):
            raise PlanningTransitionError("planning_task_role_slot_binding_invalid")
        resolved = f"{slot.role_template_key}@{slot.role_template_version}"
        declared = str(
            task.get("role_template_ref")
            or PlanningTaskMaterializationService._organization_binding(task).get("role_template_ref")
            or ""
        ).strip()
        if declared and declared != resolved:
            raise PlanningTransitionError("planning_task_role_template_binding_conflict")
        return resolved

    @staticmethod
    def _organization_topology_index(
        *,
        session,
        track: Any,
    ) -> dict[str, Any]:
        """Read authoritative proposal-hint refs once per materialization."""

        links = list(
            session.exec(
                select(OrganizationTeamLinkDB).where(
                    OrganizationTeamLinkDB.tenant_id == track.tenant_id,
                    OrganizationTeamLinkDB.project_id == track.project_id,
                    OrganizationTeamLinkDB.organization_id == track.organization_id,
                    OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")),
                )
            ).all()
        )
        unit_to_team = {row.unit_id: row.team_id for row in links}
        slots = list(
            session.exec(
                select(OrganizationRoleSlotDB).where(
                    OrganizationRoleSlotDB.tenant_id == track.tenant_id,
                    OrganizationRoleSlotDB.project_id == track.project_id,
                    OrganizationRoleSlotDB.organization_id == track.organization_id,
                    OrganizationRoleSlotDB.unit_id.in_(sorted(unit_to_team)),
                    OrganizationRoleSlotDB.lifecycle == "active",
                )
            ).all()
        )
        slot_by_id = {row.id: row for row in slots}
        assignments = list(
            session.exec(
                select(OrganizationRoleAssignmentDB).where(
                    OrganizationRoleAssignmentDB.tenant_id == track.tenant_id,
                    OrganizationRoleAssignmentDB.project_id == track.project_id,
                    OrganizationRoleAssignmentDB.organization_id == track.organization_id,
                    OrganizationRoleAssignmentDB.role_slot_id.in_(sorted(slot_by_id)),
                    OrganizationRoleAssignmentDB.lifecycle == "active",
                )
            ).all()
        )
        return {
            "unit_to_team": unit_to_team,
            "slots": slots,
            "assignments": assignments,
        }

    @staticmethod
    def _authorized_topology_refs(
        *,
        topology_index: Mapping[str, Any],
        unit_id: str,
        team_id: str,
        proposal_policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Expose only Hub-known opaque refs allowed by the role target scope."""

        unit_to_team = dict(topology_index.get("unit_to_team") or {})
        target_scope = {str(value) for value in list(proposal_policy.get("target_scope") or []) if str(value)}
        if "same_organization" in target_scope:
            allowed_units = set(unit_to_team)
        elif "same_unit" in target_scope:
            allowed_units = {unit_id} if unit_id in unit_to_team else set()
        elif "same_team" in target_scope:
            allowed_units = {
                candidate_unit for candidate_unit, candidate_team in unit_to_team.items() if candidate_team == team_id
            }
        else:
            allowed_units = set()
        slots = [row for row in list(topology_index.get("slots") or []) if row.unit_id in allowed_units]
        allowed_slot_ids = {row.id for row in slots}
        assignments = [
            row for row in list(topology_index.get("assignments") or []) if row.role_slot_id in allowed_slot_ids
        ]
        return {
            "schema": "organization_topology_refs.v1",
            "role_refs": sorted({f"{row.role_template_key}@{row.role_template_version}" for row in slots}),
            "team_refs": sorted({unit_to_team[value] for value in allowed_units}),
            # Assignment IDs are stable, opaque Hub references.  Worker URLs
            # are deliberately never exposed as proposal target identifiers.
            "agent_refs": sorted({row.id for row in assignments}),
            "worker_addresses_included": False,
        }

    @staticmethod
    def _verify_existing_task(
        task: TaskDB | ArchivedTaskDB,
        mapping: PlanningTaskMappingDB,
        *,
        runtime_contract: Mapping[str, Any],
    ) -> None:
        if not PlanningTaskMaterializationService._runtime_task_binding_matches(
            task,
            mapping,
        ):
            raise PlanningTransitionError("planning_materialized_task_binding_conflict")
        expected_workflow_binding = runtime_contract.get("workflow_binding")
        actual_workflow_binding = dict(task.worker_execution_context or {}).get("organization_workflow_step_binding")
        if (
            (expected_workflow_binding is None and actual_workflow_binding is not None)
            or (
                expected_workflow_binding is not None
                and (
                    not isinstance(actual_workflow_binding, Mapping)
                    or dict(actual_workflow_binding) != dict(expected_workflow_binding)
                )
            )
            or dict(task.verification_spec or {}) != dict(runtime_contract.get("verification_spec") or {})
        ):
            raise PlanningTransitionError("planning_materialized_task_runtime_contract_conflict")

    @staticmethod
    def _runtime_task_binding_matches(
        task: TaskDB | ArchivedTaskDB,
        mapping: PlanningTaskMappingDB,
    ) -> bool:
        return not (
            str(task.tenant_id or "") != mapping.tenant_id
            or str(task.project_id or "") != mapping.project_id
            or str(task.organization_id or "") != mapping.organization_id
            or str(task.goal_id or "") != mapping.execution_goal_id
            or str(task.unit_id or "") != str(mapping.unit_id or "")
            or str(task.team_id or "") != str(mapping.team_id or "")
            or str(task.role_slot_id or "") != str(mapping.role_slot_id or "")
        )

    @staticmethod
    def _verify_committed_materialization(
        *,
        uow: PlanningControlUnitOfWork,
        receipt: PlanningOperationReceiptDB,
        mappings: list[PlanningTaskMappingDB],
        runtime_contracts: Mapping[str, Mapping[str, Any]],
        expected_plan_task_ids: set[str],
    ) -> None:
        assert uow.session is not None
        if (
            receipt.status != "committed"
            or {row.plan_task_id for row in mappings} != expected_plan_task_ids
            or any(row.materialization_receipt_id != receipt.id for row in mappings)
        ):
            raise PlanningTransitionError("planning_materialization_receipt_incomplete")
        for mapping in mappings:
            task = uow.session.get(TaskDB, mapping.internal_task_id)
            if task is None:
                task = uow.session.get(ArchivedTaskDB, mapping.internal_task_id)
            runtime_contract = runtime_contracts.get(mapping.plan_task_id)
            if task is None or runtime_contract is None:
                raise PlanningTransitionError("planning_materialization_receipt_incomplete")
            try:
                PlanningTaskMaterializationService._verify_existing_task(
                    task,
                    mapping,
                    runtime_contract=runtime_contract,
                )
            except PlanningTransitionError as exc:
                raise PlanningTransitionError("planning_materialization_receipt_incomplete") from exc

    @staticmethod
    def _mark_replaced_source_tasks(
        *,
        uow: PlanningControlUnitOfWork,
        replacement_track: Any,
        replan: Mapping[str, Any],
    ) -> None:
        assert uow.planning is not None and uow.session is not None
        replaced_ids = {str(value) for value in list(replan.get("replaced_plan_task_ids") or []) if str(value)}
        if not replaced_ids:
            return
        source_track_id = str(replan.get("source_track_artifact_revision_id") or "")
        mappings = {
            row.plan_task_id: row
            for row in uow.planning.list_mappings(source_track_id)
            if row.plan_task_id in replaced_ids
        }
        if set(mappings) != replaced_ids:
            raise PlanningTransitionError("planning_replan_replaced_mapping_incomplete")
        for plan_task_id, mapping in mappings.items():
            task = uow.session.get(TaskDB, mapping.internal_task_id)
            if task is None:
                raise PlanningTransitionError("planning_replan_replaced_task_missing")
            if str(task.status or "") == "completed":
                raise PlanningTransitionError("planning_replan_completed_task_replaced")
            replacement_marker = {
                "schema": "planning_task_replacement.v1",
                "source_track_artifact_revision_id": source_track_id,
                "replacement_track_artifact_revision_id": replacement_track.id,
                "replacement_track_digest": replacement_track.content_digest,
                "plan_task_id": plan_task_id,
            }
            task.worker_execution_context = {
                **dict(task.worker_execution_context or {}),
                "planning_replacement": replacement_marker,
            }
            if str(task.status or "") in {
                "todo",
                "created",
                "paused",
                "blocked",
                "blocked_by_dependency",
                "pending_approval",
            }:
                task.status = "cancelled"
                task.status_reason_code = "planning_replan_replaced"
            task.history = [
                *list(task.history or []),
                {
                    "timestamp": time.time(),
                    "status": task.status,
                    "event_type": "planning_task_replaced",
                    "actor": "hub:planning_task_materialization",
                    "details": replacement_marker,
                },
            ]
            task.updated_at = time.time()
            uow.session.add(task)
            dependencies = uow.session.exec(
                select(CrossTeamTaskDependencyDB).where(
                    CrossTeamTaskDependencyDB.tenant_id == replacement_track.tenant_id,
                    CrossTeamTaskDependencyDB.project_id == replacement_track.project_id,
                    CrossTeamTaskDependencyDB.organization_id == replacement_track.organization_id,
                    or_(
                        CrossTeamTaskDependencyDB.source_task_id == task.id,
                        CrossTeamTaskDependencyDB.target_task_id == task.id,
                    ),
                )
            ).all()
            for dependency in dependencies:
                if dependency.target_task_id == task.id:
                    dependency.status = "cancelled"
                    dependency.blocking_reason = "target_task_replaced"
                else:
                    dependency.status = "blocked"
                    dependency.blocking_reason = "source_task_replaced"
                dependency.updated_at = time.time()
                uow.session.add(dependency)

    @staticmethod
    def _description(task: Mapping[str, Any]) -> str:
        description = str(task.get("description") or "").strip()
        acceptance = [str(value) for value in list(task.get("acceptance_criteria") or []) if str(value)]
        return (description + ("\n\nAcceptance:\n- " + "\n- ".join(acceptance) if acceptance else "")).strip()

    @staticmethod
    def _authorize(context: PlanningOperationContext) -> None:
        if not context.hub_owned:
            raise PlanningTransitionError("planning_hub_authority_required")
        if "organization_admin" not in context.roles and "track_materialize" not in context.allowed_operations:
            raise PlanningTransitionError("planning_organization_admin_required")

    @staticmethod
    def _validate_scope(*, context: PlanningOperationContext, row: Any) -> None:
        if (
            row.tenant_id != context.tenant_id
            or row.project_id != context.project_id
            or row.organization_id != context.organization_id
        ):
            raise PlanningTransitionError("planning_scope_forbidden")

    @staticmethod
    def _stable_id(prefix: str, *values: str) -> str:
        digest = hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()[:24]
        return f"{prefix}-{digest}"

    @staticmethod
    def _materialization_response(
        receipt: PlanningOperationReceiptDB, mappings: list[PlanningTaskMappingDB]
    ) -> dict[str, Any]:
        return {
            "receipt_id": receipt.id,
            "track_revision_id": receipt.artifact_revision_id,
            "status": "materialized",
            "materialized_task_ids": [row.internal_task_id for row in mappings],
            "plan_task_to_internal_task": {row.plan_task_id: row.internal_task_id for row in mappings},
        }

    @staticmethod
    def _dispatch_response(dispatch: PlanningTaskDispatchDB) -> dict[str, Any]:
        return {
            "dispatch_intent_id": dispatch.dispatch_intent_id,
            "lease_id": dispatch.lease_id,
            "track_revision_id": dispatch.track_revision_id,
            "internal_task_id": dispatch.internal_task_id,
            "attempt": dispatch.attempt,
            "status": dispatch.status,
        }


__all__ = ["PlanningTaskMaterializationService"]
