"""Hub-side construction of one worker execution bundle for a delegation.

``WorkerExecutionContextFactory`` turns a routed ``TaskDelegationPlan`` into a
context bundle, worker job, workspace scope, worker execution context, todo
contract and the signed delegation payload. It performs no routing and no
forwarding; ``TaskDelegationPlanner`` and ``TaskDelegationResultWriter`` in
``agent.services.task_delegation_services`` own those steps.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from typing import Any

from flask import current_app, has_app_context

from agent.config import settings
from agent.providers.registry import GenericProviderRegistry
from agent.providers.worker_execution import (
    WorkerExecutionRequest,
    WorkerExecutorDispatchBridge,
    register_default_worker_execution_descriptors,
)
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.organization_research_delegation_policy_service import (
    OrganizationResearchDelegationPolicyError,
    OrganizationResearchDelegationPolicyService,
    get_organization_research_delegation_policy_service,
)
from agent.services.organization_research_dispatch_capability_service import (
    OrganizationResearchDispatchCapabilityError,
    OrganizationResearchDispatchCapabilityIssuer,
    get_organization_research_dispatch_capability_issuer,
)
from agent.services.task_delegation_contracts import (
    DelegationRequest,
    RoutingDecision,
    TaskDelegationPlan,
    WorkerExecutionBundle,
)
from agent.services.task_execution_policy_service import normalize_allowed_tools
from agent.services.worker_execution_profile_service import normalize_worker_execution_profile
from agent.services.worker_result_capability_service import WorkerResultCapabilityService
from agent.services.worker_task_proposal_policy_service import (
    WorkerTaskProposalPolicyService,
)
from agent.services.worker_todo_planner_service import get_worker_todo_planner_service
from agent.services.workspace_scope_builder import build_worker_workspace, derive_workspace_scope

# Planning research can outlive the least-privilege default callback TTL.
PLANNING_RESEARCH_CALLBACK_TTL_SECONDS = 3600


class WorkerExecutionContextFactory:
    """Builds context bundle, workspace scope, worker job and worker task payload."""

    def __init__(
        self,
        dependencies,
        *,
        research_delegation_policy: (
            OrganizationResearchDelegationPolicyService | None
        ) = None,
        research_dispatch_issuer: (
            OrganizationResearchDispatchCapabilityIssuer | None
        ) = None,
        callback_capability_service_factory: Callable[[], Any] = WorkerResultCapabilityService,
        hub_settings: Any = settings,
    ) -> None:
        self.dependencies = dependencies
        self._research_delegation_policy = (
            research_delegation_policy
            or get_organization_research_delegation_policy_service()
        )
        self._research_dispatch_issuer = research_dispatch_issuer
        self._callback_capability_service_factory = callback_capability_service_factory
        self._hub_settings = hub_settings

    def build(
        self,
        *,
        request: DelegationRequest,
        plan: TaskDelegationPlan,
        worker_job_service,
        worker_contract_service,
    ) -> WorkerExecutionBundle:
        task_id = request.task_id
        parent_task = request.parent_task
        parent_wec = dict(parent_task.get("worker_execution_context") or {})
        data = request.data
        parent_dispatch = dict(
            parent_wec.get("planning_dispatch")
            or {}
        )
        dispatch_intent_id = str(
            parent_dispatch.get("dispatch_intent_id") or ""
        ).strip()
        subtask_id = (
            "sub-"
            + hashlib.sha256(dispatch_intent_id.encode("utf-8")).hexdigest()[:32]
            if parent_dispatch.get("schema")
            == "organization_planning_dispatch.v1"
            and dispatch_intent_id
            else f"sub-{uuid.uuid4()}"
        )
        context_query = self._context_query(parent_task=parent_task, data=data)
        context_policy, retrieval_hints, task_neighborhood = (
            self.dependencies.context_policy_service().build_context_policy(
                parent_task=parent_task,
                data=data,
                effective_task_kind=plan.effective_task_kind,
            )
        )
        authoritative_context = (
            self._research_delegation_policy.resolve_context(parent_task)
        )
        if authoritative_context is None:
            context_bundle = worker_job_service.create_context_bundle(
                query=context_query,
                parent_task_id=task_id,
                goal_id=parent_task.get("goal_id"),
                context_policy=context_policy,
            )
        else:
            context_bundle = authoritative_context.bundle
            context_policy = {
                **dict(context_policy or {}),
                "mode": "authoritative_source_catalog_bundle",
                "llm_scope": "local_only",
                "authoritative_context": dict(
                    authoritative_context.context_policy
                ),
            }
            retrieval_hints = {
                **dict(retrieval_hints or {}),
                "retrieval_intent": "authoritative_source_catalog",
                "required_context_scope": "exact_task_context_bundle",
                "preferred_bundle_mode": "authoritative",
            }
        resolved_profile, profile_source = self._resolve_execution_profile(
            parent_task=parent_task,
            request_data=data,
        )
        context_policy = {
            **dict(context_policy or {}),
            "worker_profile": resolved_profile,
            "worker_profile_source": profile_source,
        }
        if authoritative_context is None:
            expected_output_schema = dict(data.expected_output_schema or {})
            allowed_tools = normalize_allowed_tools(data.allowed_tools)
        else:
            expected_output_schema = dict(
                parent_wec.get("expected_output_schema") or {}
            )
            allowed_tools = normalize_allowed_tools(
                parent_wec.get("allowed_tools")
            )
        selected_runtime_target_id, selected_runtime_kind = (
            self._runtime_selection_coordinates(plan)
        )
        research_destination_binding = (
            self._research_delegation_policy.resolve_destination_binding(
                task=parent_task,
                worker_url=plan.agent_url,
                selected_runtime_target_id=selected_runtime_target_id,
                selected_runtime_kind=selected_runtime_kind,
                preferred_provider=plan.preferred_backend,
            )
        )
        if research_destination_binding is not None:
            context_policy["research_destination_binding"] = dict(
                research_destination_binding
            )
        routing_decision_payload = worker_contract_service.build_routing_decision(
            agent_url=plan.agent_url,
            selected_by_policy=plan.selected_by_policy,
            task_kind=plan.effective_task_kind,
            required_capabilities=plan.effective_required_capabilities,
            selection=plan.selection,
            preferred_backend=plan.preferred_backend,
        )
        organization_routing = dict(
            dict(parent_task.get("worker_execution_context") or {}).get("organization_routing") or {}
        )
        if (
            organization_routing.get("schema") == "organization_routing_decision.v1"
            and str(organization_routing.get("selected_agent_id") or "") == plan.agent_url
        ):
            routing_decision_payload.update(
                {
                    "strategy": "organization_routing_decision",
                    "reasons": ["hub_planning_routing_binding"],
                    "organization_routing_decision_hash": str(organization_routing.get("decision_hash") or ""),
                    "organization_assignment_id": str(organization_routing.get("selected_assignment_id") or ""),
                    "organization_role_slot_id": str(organization_routing.get("selected_role_slot_id") or ""),
                    "organization_team_id": str(organization_routing.get("selected_team_id") or ""),
                }
            )
        scoped_resolution = get_goal_config_runtime_service().get_effective_config(
            goal_id=str(parent_task.get("goal_id") or "").strip() or None,
            task_id=task_id,
        )
        routing_decision_payload["goal_config_source"] = scoped_resolution.source
        routing_decision_payload["worker_profile"] = resolved_profile
        routing_decision_payload["profile_source"] = profile_source
        if research_destination_binding is not None:
            routing_decision_payload["research_destination"] = dict(
                research_destination_binding
            )
        if plan.routing_hint:
            routing_decision_payload["copilot_hint"] = dict(plan.routing_hint)
        routing_decision = RoutingDecision(routing_decision_payload)
        selection_decision = (
            plan.worker_runtime_decision.model_dump(mode="json")
            if plan.worker_runtime_decision
            and hasattr(plan.worker_runtime_decision, "model_dump")
            else None
        )
        if selection_decision is None and research_destination_binding is not None:
            selection_decision = {
                "selected_worker_id": plan.agent_url,
                "selected_worker_kind": research_destination_binding.get(
                    "worker_kind"
                ),
                "selected_runtime_target_id": (
                    research_destination_binding.get("runtime_target_id")
                ),
                "selected_runtime_kind": research_destination_binding.get(
                    "runtime_kind"
                ),
                "selection_mode": "organization_research_destination",
                "policy_decision_ref": research_destination_binding.get(
                    "binding_digest"
                ),
            }
        worker_job = worker_job_service.create_worker_job(
            parent_task_id=task_id,
            subtask_id=subtask_id,
            worker_url=plan.agent_url,
            context_bundle_id=context_bundle.id,
            allowed_tools=allowed_tools,
            expected_output_schema=expected_output_schema,
            metadata=worker_contract_service.build_job_metadata(
                routing_decision=routing_decision.as_dict(),
                task_kind=plan.effective_task_kind,
                required_capabilities=plan.effective_required_capabilities,
                context_policy=context_policy,
                extra_metadata={"selected_by_policy": plan.selected_by_policy},
            ),
            selection_decision=selection_decision,
        )
        workspace_scope = derive_workspace_scope(
            parent_task=parent_task,
            subtask_id=subtask_id,
            worker_job_id=worker_job.id,
            agent_url=plan.agent_url,
        )
        output_dir = self._resolve_output_dir(parent_task)
        worker_workspace = build_worker_workspace(
            scope=workspace_scope,
            parent_task_id=task_id,
            subtask_id=subtask_id,
            worker_job_id=worker_job.id,
            agent_url=plan.agent_url,
            output_dir=output_dir,
        )
        artifact_sync = {
            "enabled": True,
            "sync_to_hub": True,
            "collection_name": "task-execution-results",
            "max_changed_files": 30,
            "max_file_size_bytes": 2 * 1024 * 1024,
        }
        worker_execution_context = worker_contract_service.build_execution_context(
            instructions=data.subtask_description,
            context_bundle=context_bundle,
            context_policy=context_policy,
            workspace=worker_workspace,
            artifact_sync=artifact_sync,
            allowed_tools=allowed_tools,
            expected_output_schema=expected_output_schema,
            routing_decision=routing_decision.as_dict(),
        )
        # Preserve the Hub-issued task contract (planning lineage, source/run
        # allowlists, role binding, budgets, and persisted routing decision)
        # while the execution factory adds assignment-specific context.
        worker_execution_context = {
            **parent_wec,
            **dict(worker_execution_context or {}),
        }
        if research_destination_binding is not None:
            worker_execution_context["research_destination_binding"] = dict(
                research_destination_binding
            )
        if authoritative_context is not None:
            worker_execution_context[
                "source_context_bundle_manifest"
            ] = {
                "schema": "organization_research_context_manifest.v1",
                "id": str(getattr(context_bundle, "id", "") or ""),
                "retrieval_run_id": str(
                    getattr(context_bundle, "retrieval_run_id", "") or ""
                ),
                "task_id": str(
                    getattr(context_bundle, "task_id", "") or ""
                ),
                "bundle_type": str(
                    getattr(context_bundle, "bundle_type", "") or ""
                ),
            }
        parent_foundation = parent_wec.get("deterministic_repair_foundation")
        if isinstance(parent_foundation, dict):
            worker_execution_context["deterministic_repair_foundation"] = parent_foundation
        worker_todo_contract_bundle = self._build_worker_todo_contract(
            request=request,
            plan=plan,
            subtask_id=subtask_id,
            context_bundle=context_bundle,
            worker_profile=resolved_profile,
            profile_source=profile_source,
            worker_workspace=worker_workspace,
            worker_contract_service=worker_contract_service,
            allowed_tools=allowed_tools,
            expected_output_schema=expected_output_schema,
        )
        if worker_todo_contract_bundle:
            worker_execution_context["todo_contract"] = dict(worker_todo_contract_bundle.get("contract") or {})
            worker_execution_context["todo_contract_generation"] = dict(
                worker_todo_contract_bundle.get("generation") or {}
            )
        raw_proposal_policy = parent_wec.get("task_proposal_policy")
        policy_result = WorkerTaskProposalPolicyService().validate_policy(
            raw_proposal_policy if isinstance(raw_proposal_policy, dict) else None
        )
        dispatch_lease_id = str(worker_job.id)
        raw_organization_binding = parent_wec.get("organization_binding")
        organization_binding = dict(raw_organization_binding) if isinstance(raw_organization_binding, dict) else {}
        role_template_ref = str(
            parent_wec.get("role_template_ref") or organization_binding.get("role_template_ref") or "unassigned_role@1"
        )
        worker_execution_context["task_proposal_binding"] = {
            "schema": "worker_task_proposal_binding.v1",
            "organization_id": str(parent_task.get("organization_id") or ""),
            "unit_id": str(parent_task.get("unit_id") or ""),
            "team_id": str(parent_task.get("team_id") or ""),
            "role_slot_id": str(parent_task.get("role_slot_id") or ""),
            "role_template_ref": role_template_ref,
            "assignment_id": subtask_id,
            "dispatch_lease_id": dispatch_lease_id,
            "worker_id": plan.agent_url,
            "proposal_policy": dict(policy_result["policy"]),
            "proposal_policy_hash": str(policy_result["policy_hash"]),
        }
        delegation_payload = self._delegation_payload(
            request=request,
            plan=plan,
            subtask_id=subtask_id,
            context_bundle_id=context_bundle.id,
            retrieval_hints=retrieval_hints,
            context_policy=context_policy,
            worker_execution_context=worker_execution_context,
        )
        if authoritative_context is not None:
            source_context_policy = dict(
                worker_execution_context.get("source_context_policy") or {}
            )
            destination_binding = dict(
                worker_execution_context.get(
                    "research_destination_binding"
                )
                or {}
            )
            try:
                delegation_payload["hub_dispatch_capability"] = (
                    self._research_dispatch_capability_issuer().issue(
                        payload=delegation_payload,
                        worker_url=plan.agent_url,
                        source_context_bundle_digest=str(
                            source_context_policy.get(
                                "context_bundle_digest"
                            )
                            or ""
                        ),
                        destination_binding_digest=str(
                            destination_binding.get("binding_digest") or ""
                        ),
                        worker_job_id=str(worker_job.id),
                    )
                )
            except OrganizationResearchDispatchCapabilityError as exc:
                fail_dispatch = getattr(
                    worker_job_service,
                    "fail_dispatch",
                    None,
                )
                if callable(fail_dispatch):
                    fail_dispatch(
                        worker_job_id=str(worker_job.id),
                        reason_code=exc.reason_code,
                        rejected=True,
                    )
                raise OrganizationResearchDelegationPolicyError(
                    exc.reason_code
                ) from exc
        return WorkerExecutionBundle(
            subtask_id=subtask_id,
            context_bundle=context_bundle,
            context_policy=dict(context_policy),
            retrieval_hints=dict(retrieval_hints),
            task_neighborhood=dict(task_neighborhood),
            expected_output_schema=expected_output_schema,
            allowed_tools=allowed_tools,
            routing_decision=routing_decision,
            worker_job=worker_job,
            workspace_scope=workspace_scope,
            worker_execution_context=worker_execution_context,
            delegation_payload=delegation_payload,
        )

    def _research_dispatch_capability_issuer(
        self,
    ) -> OrganizationResearchDispatchCapabilityIssuer:
        if self._research_dispatch_issuer is None:
            self._research_dispatch_issuer = (
                get_organization_research_dispatch_capability_issuer()
            )
        return self._research_dispatch_issuer

    @staticmethod
    def _runtime_selection_coordinates(
        plan: TaskDelegationPlan,
    ) -> tuple[str | None, str | None]:
        decision = plan.worker_runtime_decision
        if decision is None:
            return None, None
        target_id = str(
            getattr(decision, "selected_runtime_target_id", "") or ""
        ).strip()
        raw_kind = getattr(decision, "selected_runtime_kind", None)
        runtime_kind = str(
            getattr(raw_kind, "value", raw_kind or "")
        ).strip()
        return target_id or None, runtime_kind or None

    def _resolve_output_dir(self, parent_task: dict[str, Any]) -> str:
        goal_id = str(parent_task.get("goal_id") or "").strip()
        if not goal_id:
            return ""
        try:
            repos = self.dependencies.repository_registry()
            goal = repos.goal_repo.get_by_id(goal_id)
            if goal:
                return str((goal.execution_preferences or {}).get("output_dir") or "").strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _resolve_execution_profile(*, parent_task: dict[str, Any], request_data: Any) -> tuple[str, str]:
        requested = str(
            getattr(request_data, "worker_profile", None) or getattr(request_data, "execution_profile", None) or ""
        ).strip()
        if requested:
            return normalize_worker_execution_profile(requested), "task_override"
        parent_context = dict(parent_task.get("worker_execution_context") or {})
        parent_profile = str(
            parent_context.get("worker_profile") or parent_context.get("execution_profile") or ""
        ).strip()
        if parent_profile:
            source = str(parent_context.get("profile_source") or "task_context").strip().lower() or "task_context"
            return normalize_worker_execution_profile(parent_profile), source
        agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
        runtime_cfg = agent_cfg.get("worker_runtime") if isinstance(agent_cfg.get("worker_runtime"), dict) else {}
        return normalize_worker_execution_profile(runtime_cfg.get("default_execution_profile")), "agent_default"

    @staticmethod
    def _context_query(*, parent_task: dict[str, Any], data: Any) -> str:
        return str(data.context_query or "").strip() or " ".join(
            item
            for item in [
                str(parent_task.get("title") or "").strip(),
                str(parent_task.get("description") or "").strip(),
                str(data.subtask_description or "").strip(),
            ]
            if item
        )

    @staticmethod
    def _build_worker_todo_contract(
        *,
        request: DelegationRequest,
        plan: TaskDelegationPlan,
        subtask_id: str,
        context_bundle,
        worker_profile: str,
        profile_source: str,
        worker_workspace: dict[str, Any],
        worker_contract_service,
        allowed_tools: list[str],
        expected_output_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        parent_task = request.parent_task
        data = request.data
        workspace_dir = None
        if isinstance(worker_workspace, dict):
            workspace_dir = str(worker_workspace.get("workspace_dir") or "").strip() or None
        todo_contract_bundle = get_worker_todo_planner_service().build_delegation_todo_contract(
            worker_contract_service=worker_contract_service,
            subtask_id=subtask_id,
            parent_task=parent_task,
            subtask_description=str(data.subtask_description or "").strip(),
            task_kind=plan.effective_task_kind,
            required_capabilities=plan.effective_required_capabilities,
            worker_profile=worker_profile,
            profile_source=profile_source,
            allowed_tools=allowed_tools,
            expected_output_schema=expected_output_schema,
            target_worker=plan.agent_url,
            context_bundle_id=getattr(context_bundle, "id", None),
            workspace_dir=workspace_dir,
        )
        if not isinstance(todo_contract_bundle, dict):
            return todo_contract_bundle
        contract = dict(todo_contract_bundle.get("contract") or {})
        generation = dict(todo_contract_bundle.get("generation") or {})
        executor_kind = (
            str(((contract.get("worker") or {}).get("executor_kind") or "custom")).strip().lower() or "custom"
        )
        registry = GenericProviderRegistry()
        register_default_worker_execution_descriptors(registry)
        bridge = WorkerExecutorDispatchBridge(registry)
        dispatch_result = bridge.dispatch(
            executor_kind=executor_kind,
            request=WorkerExecutionRequest(
                task_id=subtask_id,
                worker_job={},
                context_bundle={},
                allowed_tools=list(allowed_tools or []),
                expected_output_schema=dict(expected_output_schema or {}),
                policy_context={},
                executor_kind=executor_kind,
            ),
            enable_provider=False,
        )
        generation["executor_dispatch"] = {
            "status": dispatch_result.status,
            "reason": dispatch_result.reason,
            "executor_kind": executor_kind,
            "provider_id": str((dispatch_result.result_payload or {}).get("provider_id") or executor_kind),
        }
        return {"contract": contract, "generation": generation}

    def _delegation_payload(
        self,
        *,
        request: DelegationRequest,
        plan: TaskDelegationPlan,
        subtask_id: str,
        context_bundle_id: str,
        retrieval_hints: dict[str, Any],
        context_policy: dict[str, Any],
        worker_execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = request.task_id
        parent_task = request.parent_task
        data = request.data
        my_url = self._hub_settings.agent_url or f"http://localhost:{self._hub_settings.port}"
        callback_url = f"{my_url.rstrip('/')}/tasks/{task_id}/subtask-callback"
        dispatch_lease_id = str(
            dict(worker_execution_context.get("task_proposal_binding") or {}).get("dispatch_lease_id") or ""
        )
        callback_capability_kwargs = {
            "worker_id": plan.agent_url,
            "source_task_id": task_id,
            "assignment_id": subtask_id,
            "dispatch_lease_id": dispatch_lease_id,
        }
        if plan.effective_task_kind == "planning_research":
            callback_capability_kwargs["ttl_seconds"] = (
                PLANNING_RESEARCH_CALLBACK_TTL_SECONDS
            )
        callback_capability = self._callback_capability_service_factory().issue(
            **callback_capability_kwargs,
        )
        return {
            "id": subtask_id,
            "title": data.subtask_description[:200],
            "description": data.subtask_description,
            "parent_task_id": task_id,
            "priority": data.priority,
            "team_id": parent_task.get("team_id"),
            "goal_id": parent_task.get("goal_id"),
            "goal_trace_id": parent_task.get("goal_trace_id"),
            "task_kind": plan.effective_task_kind,
            "retrieval_intent": retrieval_hints["retrieval_intent"],
            "required_context_scope": retrieval_hints["required_context_scope"],
            "preferred_bundle_mode": retrieval_hints["preferred_bundle_mode"],
            "required_capabilities": plan.effective_required_capabilities,
            "context_bundle_id": context_bundle_id,
            "worker_execution_context": worker_execution_context,
            "callback_url": callback_url,
            "callback_token": callback_capability,
            "assignment_id": subtask_id,
            "dispatch_lease_id": dispatch_lease_id,
            "source": "agent",
            "created_by": self._hub_settings.agent_name or "hub",
            "context_bundle_policy": dict(context_policy),
        }
