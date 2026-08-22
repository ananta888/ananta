from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from flask import current_app, has_app_context

from agent.common.api_envelope import unwrap_api_envelope
from agent.config import settings
from agent.providers.registry import GenericProviderRegistry
from agent.providers.worker_execution import (
    WorkerExecutionRequest,
    WorkerExecutorDispatchBridge,
    register_default_worker_execution_descriptors,
)
from agent.research_backend import resolve_research_backend_config
from agent.routes.tasks.orchestration_policy import (
    derive_required_capabilities,
    evaluate_worker_routing_policy,
    persist_policy_decision,
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
from agent.services.task_execution_policy_service import normalize_allowed_tools
from agent.services.worker_execution_profile_service import normalize_worker_execution_profile
from agent.services.worker_result_capability_service import WorkerResultCapabilityService
from agent.services.worker_runtime_selection_service import WorkerRuntimeSelectionRequest, WorkerRuntimeSelectionService
from agent.services.worker_runtime_target_service import WorkerRuntimeTargetService
from agent.services.worker_selection_policy_service import WorkerSelectionPolicyService
from agent.services.worker_task_proposal_policy_service import (
    WorkerTaskProposalPolicyService,
)
from agent.services.worker_todo_planner_service import get_worker_todo_planner_service
from agent.services.workspace_scope_builder import build_worker_workspace, derive_workspace_scope
from worker.core.runtime_target import WorkerCandidate, WorkerKind


@dataclass(frozen=True)
class DelegationRequest:
    task_id: str
    parent_task: dict[str, Any]
    data: Any


@dataclass(frozen=True)
class RoutingDecision:
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class TaskDelegationPlan:
    agent_url: str
    selected_by_policy: bool
    selection: Any
    policy_decision: Any
    routing_hint: dict[str, Any] | None
    effective_task_kind: str | None
    effective_required_capabilities: list[str]
    preferred_backend: str | None
    worker_runtime_decision: Any = None


@dataclass(frozen=True)
class WorkerExecutionBundle:
    subtask_id: str
    context_bundle: Any
    context_policy: dict[str, Any]
    retrieval_hints: dict[str, Any]
    task_neighborhood: dict[str, Any]
    expected_output_schema: dict[str, Any]
    allowed_tools: list[str]
    routing_decision: RoutingDecision
    worker_job: Any
    workspace_scope: dict[str, Any]
    worker_execution_context: dict[str, Any]
    delegation_payload: dict[str, Any]


class TaskDelegationPlanner:
    """Prepares hub-owned worker selection, routing hints and policy metadata."""

    def __init__(self, dependencies, *, organization_binding_resolver=None) -> None:
        self.dependencies = dependencies
        if organization_binding_resolver is None:
            from agent.services.organization_dispatch_binding_service import (
                OrganizationDispatchBindingResolver,
            )

            organization_binding_resolver = OrganizationDispatchBindingResolver()
        self._organization_bindings = organization_binding_resolver

    def plan(
        self,
        *,
        request: DelegationRequest,
        agent_registry_service,
    ) -> TaskDelegationPlan | dict[str, Any]:
        task_id = request.task_id
        parent_task = request.parent_task
        data = request.data
        agent_url = data.agent_url
        from agent.services.organization_planning_adapter import (
            organization_id_from_task,
        )

        organization_bound = bool(organization_id_from_task(parent_task))
        hub_routing_binding = False
        if organization_bound:
            # A caller/Worker target is only a hint for organization work.
            # The Planning control plane may, however, already have persisted
            # the final Hub routing decision atomically with its dispatch
            # intent.  Only that closed binding is authoritative here.
            agent_url = self._organization_bindings.resolve(parent_task)
            hub_routing_binding = bool(agent_url)
            if not hub_routing_binding:
                return {
                    "error": "organization_planning_dispatch_binding_required",
                    "code": 409,
                    "data": {},
                }
        selected_by_policy = hub_routing_binding
        selection = None
        policy_decision = None
        routing_hint = None
        authoritative_research_task = bool(
            organization_bound
            and str(parent_task.get("task_kind") or "").strip()
            == "planning_research"
        )
        effective_task_kind = (
            "planning_research"
            if authoritative_research_task
            else data.task_kind or parent_task.get("task_kind")
        )
        if authoritative_research_task:
            effective_required_capabilities = list(
                parent_task.get("required_capabilities") or []
            )
        elif getattr(data, "required_capabilities", None) is not None:
            effective_required_capabilities = list(data.required_capabilities or [])
        else:
            effective_required_capabilities = parent_task.get("required_capabilities") or derive_required_capabilities(
                parent_task, effective_task_kind
            )
        preferred_backend = self._preferred_backend(
            effective_task_kind,
            goal_id=str(parent_task.get("goal_id") or "").strip() or None,
        )
        worker_runtime_decision = None

        # DRR-T053/T048: Try new worker/runtime selection if policy exists
        repos = self.dependencies.repository_registry()
        if isinstance(data, dict):
            policy_data = data.get("worker_selection_policy")
        else:
            policy_data = getattr(data, "worker_selection_policy", None)
        policy_data = policy_data or parent_task.get("worker_selection")
        if policy_data and not hub_routing_binding:
            policy = WorkerSelectionPolicyService().from_config(policy_data)
            agents = repos.agent_repo.get_all()
            candidates = []
            for a in agents:
                if a.status != "online":
                    continue
                # Simplified mapping, should be more robust in real impl
                kind = WorkerKind.native_ananta_worker
                if "opencode" in (a.name or "").lower():
                    kind = WorkerKind.opencode
                elif "hermes" in (a.name or "").lower():
                    kind = WorkerKind.hermes

                candidates.append(
                    WorkerCandidate(
                        worker_id=a.url,
                        worker_kind=kind,
                        capabilities=list(a.capabilities or []),
                        worker_roles=list(a.worker_roles or []),
                        priority=100,
                    )
                )

            rt_service = WorkerRuntimeTargetService()
            runtime_targets = [rt_service.local_process_default(), rt_service.docker_default()]

            sel_request = WorkerRuntimeSelectionRequest(
                policy=policy,
                workers=candidates,
                runtime_targets=runtime_targets,
                required_capabilities=effective_required_capabilities,
                execution_mode="task_delegation",
            )
            worker_runtime_decision = WorkerRuntimeSelectionService().select(sel_request)

            if worker_runtime_decision.selected_worker_id:
                agent_url = worker_runtime_decision.selected_worker_id
                selected_by_policy = True
                selection = worker_runtime_decision  # Mapping for compatibility

        if not agent_url:
            available_workers = [
                agent_registry_service.build_directory_entry(agent=worker, timeout=300)
                for worker in repos.agent_repo.get_all()
            ]
            routing_hint = self.dependencies.routing_advisor().resolve_routing_hint(
                task=parent_task,
                workers=available_workers,
                task_kind=effective_task_kind,
                required_capabilities=effective_required_capabilities,
            )
            selection, policy_decision = evaluate_worker_routing_policy(
                task=parent_task,
                workers=available_workers,
                decision_type="delegation",
                task_kind=effective_task_kind,
                required_capabilities=effective_required_capabilities,
                task_id=task_id,
                extra_details={"copilot_routing_hint": routing_hint} if routing_hint else None,
            )
            agent_url = selection.worker_url
            selected_by_policy = True
            if not agent_url:
                return {
                    "error": "no_worker_available",
                    "code": 409,
                    "data": {"reasons": selection.reasons},
                }

        return TaskDelegationPlan(
            agent_url=agent_url,
            selected_by_policy=selected_by_policy,
            selection=selection,
            policy_decision=policy_decision,
            routing_hint=routing_hint,
            effective_task_kind=effective_task_kind,
            effective_required_capabilities=list(effective_required_capabilities or []),
            preferred_backend=preferred_backend,
            worker_runtime_decision=worker_runtime_decision,
        )

    PLANNING_RESEARCH_CALLBACK_TTL_SECONDS = 3600

    @staticmethod
    def _preferred_backend(effective_task_kind: str | None, goal_id: str | None = None) -> str | None:
        scoped = get_goal_config_runtime_service().get_effective_config(goal_id=goal_id, task_id=None)
        cfg = dict(scoped.config or {})
        normalized_kind = str(effective_task_kind or "").strip().lower()
        routing_cfg = cfg.get("sgpt_routing") if isinstance(cfg.get("sgpt_routing"), dict) else {}
        backend_map = (
            routing_cfg.get("task_kind_backend") if isinstance(routing_cfg.get("task_kind_backend"), dict) else {}
        )
        mapped = str(backend_map.get(normalized_kind) or backend_map.get("*") or "").strip().lower()
        if mapped:
            return mapped
        if normalized_kind != "research":
            return None
        return resolve_research_backend_config(agent_cfg=cfg).get("provider")


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
    ) -> None:
        self.dependencies = dependencies
        self._research_delegation_policy = (
            research_delegation_policy
            or get_organization_research_delegation_policy_service()
        )
        self._research_dispatch_issuer = research_dispatch_issuer

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

    @staticmethod
    def _delegation_payload(
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
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
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
                TaskDelegationPlanner.PLANNING_RESEARCH_CALLBACK_TTL_SECONDS
            )
        callback_capability = WorkerResultCapabilityService().issue(
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
            "created_by": settings.agent_name or "hub",
            "context_bundle_policy": dict(context_policy),
        }


class TaskDelegationResultWriter:
    """Persists delegation side effects and builds the API response model."""

    def __init__(
        self,
        dependencies,
        *,
        research_delegation_policy: (
            OrganizationResearchDelegationPolicyService | None
        ) = None,
        organization_binding_resolver=None,
    ) -> None:
        self.dependencies = dependencies
        self._research_delegation_policy = (
            research_delegation_policy
            or get_organization_research_delegation_policy_service()
        )
        if organization_binding_resolver is None:
            from agent.services.organization_dispatch_binding_service import (
                OrganizationDispatchBindingResolver,
            )

            organization_binding_resolver = (
                OrganizationDispatchBindingResolver()
            )
        self._organization_bindings = organization_binding_resolver

    def forward_and_write(
        self,
        *,
        request: DelegationRequest,
        plan: TaskDelegationPlan,
        bundle: WorkerExecutionBundle,
        worker_job_service=None,
    ) -> dict[str, Any]:
        from agent.services.recovery_dispatch_gate_service import (
            get_recovery_dispatch_gate_service,
        )
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repos = get_repository_registry()
        gate = get_recovery_dispatch_gate_service()
        authoritative = repos.task_repo.get_by_id(request.task_id)
        if gate.is_recovery_child(authoritative):
            with gate.dispatch_guard(request.task_id) as gate_decision:
                reason_code = (
                    gate_decision.reason_code
                    if not gate_decision.allowed
                    else "recovery_child_delegation_not_supported"
                )
                self._terminalize_dispatch(
                    worker_job_service,
                    bundle=bundle,
                    reason_code=reason_code,
                    rejected=True,
                )
                return {
                    "error": reason_code,
                    "code": 409,
                    "data": {
                        "source_task_id": (gate_decision.source_task_id),
                        "plan_id": gate_decision.plan_id,
                    },
                }
        if authoritative is None or str(getattr(authoritative, "status", "") or "").strip().lower() in {
            "completed",
            "failed",
            "cancelled",
            "verification_failed",
            "skipped",
            "aborted",
            "timeout",
            "archived",
        }:
            self._terminalize_dispatch(
                worker_job_service,
                bundle=bundle,
                reason_code="task_not_dispatchable",
                rejected=True,
            )
            return {
                "error": "task_not_dispatchable",
                "code": 409,
                "data": {},
            }
        worker = repos.agent_repo.get_by_url(plan.agent_url)
        worker_token = str(getattr(worker, "token", "") or "").strip()
        if worker is None or not worker_token:
            self._terminalize_dispatch(
                worker_job_service,
                bundle=bundle,
                reason_code="worker_auth_unavailable",
                rejected=True,
            )
            return {
                "error": "worker_auth_unavailable",
                "code": 409,
                "data": {"worker_url": plan.agent_url},
            }
        try:
            policy_decision = plan.policy_decision or self._persist_manual_policy(
                request=request,
                plan=plan,
                bundle=bundle,
            )
            authoritative_task = (
                authoritative.model_dump()
                if hasattr(authoritative, "model_dump")
                else dict(authoritative)
                if isinstance(authoritative, Mapping)
                else vars(authoritative)
            )
            selected_runtime_target_id, selected_runtime_kind = (
                WorkerExecutionContextFactory._runtime_selection_coordinates(
                    plan
                )
            )
            self._research_delegation_policy.verify_forward(
                task=authoritative_task,
                worker_url=plan.agent_url,
                selected_runtime_target_id=selected_runtime_target_id,
                selected_runtime_kind=selected_runtime_kind,
                preferred_provider=plan.preferred_backend,
                expected_context_bundle_id=str(
                    getattr(bundle.context_bundle, "id", "") or ""
                ),
                expected_destination_binding=(
                    bundle.context_policy.get(
                        "research_destination_binding"
                    )
                    if isinstance(bundle.context_policy, Mapping)
                    else None
                ),
                expected_worker_job_id=str(bundle.worker_job.id),
                expected_subtask_id=bundle.subtask_id,
            )
            if str(
                authoritative_task.get("organization_id") or ""
            ).strip():
                current_worker = self._organization_bindings.resolve(
                    authoritative_task
                )
                if not current_worker:
                    raise OrganizationResearchDelegationPolicyError(
                        "organization_planning_dispatch_binding_required"
                    )
                if str(current_worker) != str(plan.agent_url):
                    raise OrganizationResearchDelegationPolicyError(
                        "organization_planning_dispatch_binding_changed_before_forward"
                    )
            endpoint = (
                "/internal/tasks/organization-planning-research"
                if str(
                    bundle.delegation_payload.get(
                        "hub_dispatch_capability"
                    )
                    or ""
                )
                else "/tasks"
            )
            raw_response = self.dependencies.forward_task_to_worker(
                plan.agent_url,
                endpoint,
                bundle.delegation_payload,
                token=worker_token,
            )
            response = self._accepted_worker_response(
                raw_response,
                research_dispatch=bool(
                    bundle.delegation_payload.get(
                        "hub_dispatch_capability"
                    )
                ),
                expected_task_id=bundle.subtask_id,
            )
            if response is None:
                self._terminalize_dispatch(
                    worker_job_service,
                    bundle=bundle,
                    reason_code="worker_transport_failed",
                    rejected=False,
                )
                return {
                    "error": "delegation_failed",
                    "code": 502,
                    "data": {
                        "reason_code": "worker_transport_failed"
                    },
                }
        except OrganizationResearchDelegationPolicyError as exc:
            self._terminalize_dispatch(
                worker_job_service,
                bundle=bundle,
                reason_code=exc.reason_code,
                rejected=True,
            )
            return {
                "error": exc.reason_code,
                "code": 409,
                "data": {},
            }
        except Exception:
            self._terminalize_dispatch(
                worker_job_service,
                bundle=bundle,
                reason_code="worker_transport_failed",
                rejected=False,
            )
            return {
                "error": "delegation_failed",
                "code": 502,
                "data": {"reason_code": "worker_transport_failed"},
            }

        self._update_parent_task(
            request=request,
            plan=plan,
            bundle=bundle,
            policy_decision=policy_decision,
        )
        return self._response_model(
            response=response,
            request=request,
            plan=plan,
            bundle=bundle,
            policy_decision=policy_decision,
        )

    @staticmethod
    def _terminalize_dispatch(
        worker_job_service,
        *,
        bundle: WorkerExecutionBundle,
        reason_code: str,
        rejected: bool,
    ) -> None:
        if worker_job_service is None:
            return
        worker_job_service.fail_dispatch(
            worker_job_id=str(bundle.worker_job.id),
            reason_code=reason_code,
            rejected=rejected,
        )

    @staticmethod
    def _accepted_worker_response(
        raw_response: Any,
        *,
        research_dispatch: bool,
        expected_task_id: str,
    ) -> dict[str, Any] | None:
        """Accept only a non-error Worker acknowledgement.

        The production HTTP gateway historically returns error dictionaries
        instead of raising. Treating such a value as a normal response would
        advance the Hub parent while no Worker owns the Task.
        """

        if not isinstance(raw_response, Mapping):
            return None
        outer = dict(raw_response)
        response = unwrap_api_envelope(outer)
        if TaskDelegationResultWriter._is_worker_error(outer):
            return None
        if TaskDelegationResultWriter._is_worker_error(response):
            return None
        if research_dispatch:
            if response.get("accepted") is not True:
                return None
            response_task_id = str(response.get("task_id") or "").strip()
            if response_task_id != str(expected_task_id or "").strip():
                return None
        return response

    @staticmethod
    def _is_worker_error(payload: Mapping[str, Any]) -> bool:
        status = str(payload.get("status") or "").strip().lower()
        if status in {"error", "failed", "failure", "rejected"}:
            return True
        if payload.get("success") is False or payload.get("ok") is False:
            return True
        if payload.get("accepted") is False:
            return True
        raw_code = payload.get("status_code", payload.get("code"))
        if isinstance(raw_code, bool):
            return True
        try:
            return raw_code is not None and int(raw_code) >= 400
        except (TypeError, ValueError, OverflowError):
            return raw_code is not None

    def _persist_manual_policy(
        self,
        *,
        request: DelegationRequest,
        plan: TaskDelegationPlan,
        bundle: WorkerExecutionBundle,
    ):
        if plan.selected_by_policy and plan.policy_decision is not None:
            return plan.policy_decision
        planning_routing = dict(
            dict(request.parent_task.get("worker_execution_context") or {}).get("organization_routing") or {}
        )
        routing_reason = (
            "hub_planning_routing_binding"
            if planning_routing.get("schema") == "organization_routing_decision.v1"
            else "manual_override"
        )
        return persist_policy_decision(
            decision_type="delegation",
            status="approved",
            policy_name="worker_capability_routing",
            policy_version="worker-routing-v2",
            reasons=(plan.selection.reasons if plan.selection else [routing_reason]),
            details={
                "task_kind": request.data.task_kind,
                "required_capabilities": plan.effective_required_capabilities,
                "manual_override": routing_reason == "manual_override",
                "organization_routing_decision_hash": planning_routing.get("decision_hash"),
                "copilot_routing_hint": plan.routing_hint,
                "context_bundle_policy": bundle.context_policy,
                "retrieval_hints": bundle.retrieval_hints,
                "task_neighborhood": bundle.task_neighborhood,
            },
            task_id=request.task_id,
            worker_url=plan.agent_url,
        )

    def _update_parent_task(
        self,
        *,
        request: DelegationRequest,
        plan: TaskDelegationPlan,
        bundle: WorkerExecutionBundle,
        policy_decision: Any,
    ) -> None:
        task_id = request.task_id
        parent_task = request.parent_task
        data = request.data
        subtasks = list(parent_task.get("subtasks") or [])
        subtasks.append(
            {
                "id": bundle.subtask_id,
                "agent_url": plan.agent_url,
                "description": data.subtask_description,
                "status": "created",
            }
        )
        self.dependencies.update_task_status(
            task_id,
            parent_task.get("status", "in_progress"),
            context_bundle_id=bundle.context_bundle.id,
            current_worker_job_id=bundle.worker_job.id,
            worker_execution_context=bundle.worker_execution_context,
            subtasks=subtasks,
            event_type="task_delegated",
            event_actor="hub",
            event_details={
                "delegated_to": plan.agent_url,
                "subtask_id": bundle.subtask_id,
                "context_bundle_id": bundle.context_bundle.id,
                "worker_job_id": bundle.worker_job.id,
                "policy": "hub_central_queue",
                "selected_by_policy": plan.selected_by_policy,
                "copilot_routing_hint": plan.routing_hint,
                "context_bundle_policy": bundle.context_policy,
                "retrieval_hints": bundle.retrieval_hints,
                "task_neighborhood": bundle.task_neighborhood,
                "workspace_scope": bundle.workspace_scope,
                "policy_decision_id": getattr(policy_decision, "id", None),
            },
        )

    @staticmethod
    def _response_model(
        *,
        response: Any,
        request: DelegationRequest,
        plan: TaskDelegationPlan,
        bundle: WorkerExecutionBundle,
        policy_decision: Any,
    ) -> dict[str, Any]:
        organization_routing = dict(
            dict(request.parent_task.get("worker_execution_context") or {}).get("organization_routing") or {}
        )
        selection_reasons = (
            list(plan.selection.reasons)
            if plan.selection
            else [
                "hub_planning_routing_binding"
                if organization_routing.get("schema") == "organization_routing_decision.v1"
                else "manual_override"
            ]
        )
        return {
            "data": {
                "status": "delegated",
                "subtask_id": bundle.subtask_id,
                "agent_url": plan.agent_url,
                "response": response,
                "selected_by_policy": plan.selected_by_policy,
                "selection_reasons": selection_reasons,
                "worker_selection": bundle.routing_decision.as_dict(),
                "copilot_routing_hint": plan.routing_hint,
                "policy_decision_id": getattr(policy_decision, "id", None),
                "context_bundle_id": bundle.context_bundle.id,
                "worker_job_id": bundle.worker_job.id,
                "context_bundle_policy": bundle.context_policy,
                "retrieval_hints": bundle.retrieval_hints,
                "task_neighborhood": bundle.task_neighborhood,
                "workspace_scope": bundle.workspace_scope,
            }
        }
