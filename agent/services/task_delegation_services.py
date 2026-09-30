from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.common.api_envelope import unwrap_api_envelope
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
from agent.services.task_delegation_contracts import (
    DelegationRequest as DelegationRequest,
)
from agent.services.task_delegation_contracts import (
    RoutingDecision as RoutingDecision,
)
from agent.services.task_delegation_contracts import (
    TaskDelegationPlan as TaskDelegationPlan,
)
from agent.services.task_delegation_contracts import (
    WorkerExecutionBundle as WorkerExecutionBundle,
)
from agent.services.worker_execution_context_factory import (
    PLANNING_RESEARCH_CALLBACK_TTL_SECONDS,
)
from agent.services.worker_execution_context_factory import (
    WorkerExecutionContextFactory as WorkerExecutionContextFactory,
)
from agent.services.worker_runtime_selection_service import WorkerRuntimeSelectionRequest, WorkerRuntimeSelectionService
from agent.services.worker_runtime_target_service import WorkerRuntimeTargetService
from agent.services.worker_selection_policy_service import WorkerSelectionPolicyService
from worker.core.runtime_target import WorkerCandidate, WorkerKind


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

    PLANNING_RESEARCH_CALLBACK_TTL_SECONDS = PLANNING_RESEARCH_CALLBACK_TTL_SECONDS

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
