from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import or_
from sqlalchemy import update as sa_update
from sqlmodel import select

from agent.db_models import (
    CrossTeamTaskDependencyDB,
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
    OrganizationRoutingService,
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
from agent.services.planning_cross_team_dependencies import (
    resolve_task_dependencies,
    stage_cross_team_dependencies,
)
from agent.services.planning_materialization_ids import stable_planning_id
from agent.services.planning_principal_identity_service import (
    planning_separation_of_duties_reason,
)
from agent.services.planning_task_dispatch_router import PlanningTaskDispatchRouter
from agent.services.planning_task_materialization_verification import (
    verify_committed_materialization,
    verify_existing_task,
)
from agent.services.planning_task_topology_bindings import (
    authorized_topology_refs,
    load_organization_topology_index,
    resolve_role_template_ref,
    resolve_task_binding,
    restricted_task_evidence_refs,
)
from agent.services.planning_track_contract_service import planning_contract_hash
from agent.services.planning_track_pipeline_service import (
    validate_planning_track_with_details,
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
        router: PlanningTaskDispatchRouter | None = None,
    ) -> None:
        self._uow_factory = uow_factory or PlanningControlUnitOfWork
        self._approvals = approval_service or ApprovalRequestService()
        self._organization_adapter = organization_adapter or OrganizationPlanningAdapter()
        self._routing = routing_service or OrganizationRoutingService()
        self._assignment_eligibility = assignment_eligibility or OrganizationAssignmentEligibilityService()
        self._workflow_task_bindings = workflow_task_bindings or OrganizationWorkflowTaskBindingService()
        self._router = router or PlanningTaskDispatchRouter(
            routing_service=self._routing,
            assignment_eligibility=self._assignment_eligibility,
        )

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
                verify_committed_materialization(
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

            receipt_id = stable_planning_id("pmat", intent, idempotency_key)
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
                    else stable_planning_id("ptask", track.id, plan_task_id)
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
                binding = resolve_task_binding(task)
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

            topology_index = load_organization_topology_index(
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
                dependencies = resolve_task_dependencies(
                    uow=uow,
                    track=track,
                    task=task,
                    local_mappings=mapping_by_plan_id,
                )
                existing_task = uow.session.get(TaskDB, mapping.internal_task_id)
                if existing_task is not None:
                    verify_existing_task(
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
                            "allowed_source_refs": restricted_task_evidence_refs(
                                task=task,
                                field="allowed_source_refs",
                                track_refs=list(track.allowed_source_refs or []),
                            ),
                            "allowed_run_refs": restricted_task_evidence_refs(
                                task=task,
                                field="allowed_run_refs",
                                track_refs=list(track.allowed_run_refs or []),
                            ),
                            "role_template_ref": resolve_role_template_ref(
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
                            "organization_topology_refs": authorized_topology_refs(
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

            stage_cross_team_dependencies(
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
            routing = self._router.route(
                session=uow.session,
                track=track,
                task=task,
                target_agent_hint=requested_worker_id,
            )
            selected_worker_id = str(routing["selected_agent_id"])
            attempt = 1
            dispatch_id = stable_planning_id("pdispatch", mapping.id, str(attempt))
            lease_id = stable_planning_id("please", dispatch_id, idempotency_key)
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
