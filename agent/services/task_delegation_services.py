from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from flask import current_app

from agent.common.api_envelope import unwrap_api_envelope
from agent.config import settings
from agent.research_backend import resolve_research_backend_config
from agent.routes.tasks.orchestration_policy import (
    derive_required_capabilities,
    evaluate_worker_routing_policy,
    persist_policy_decision,
)
from agent.services.task_execution_policy_service import normalize_allowed_tools
from agent.services.workspace_scope_builder import build_worker_workspace, derive_workspace_scope


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

    def __init__(self, dependencies) -> None:
        self.dependencies = dependencies

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
        selected_by_policy = False
        selection = None
        policy_decision = None
        routing_hint = None
        effective_task_kind = data.task_kind or parent_task.get("task_kind")
        effective_required_capabilities = (
            data.required_capabilities
            or parent_task.get("required_capabilities")
            or derive_required_capabilities(parent_task, effective_task_kind)
        )
        preferred_backend = self._preferred_backend(effective_task_kind)

        if not agent_url:
            repos = self.dependencies.repository_registry()
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
        )

    @staticmethod
    def _preferred_backend(effective_task_kind: str | None) -> str | None:
        if str(effective_task_kind or "").strip().lower() != "research":
            return None
        return resolve_research_backend_config(
            agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {}
        ).get("provider")


class WorkerExecutionContextFactory:
    """Builds context bundle, workspace scope, worker job and worker task payload."""

    def __init__(self, dependencies) -> None:
        self.dependencies = dependencies

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
        data = request.data
        subtask_id = f"sub-{uuid.uuid4()}"
        context_query = self._context_query(parent_task=parent_task, data=data)
        context_policy, retrieval_hints, task_neighborhood = (
            self.dependencies.context_policy_service().build_context_policy(
                parent_task=parent_task,
                data=data,
                effective_task_kind=plan.effective_task_kind,
            )
        )
        context_bundle = worker_job_service.create_context_bundle(
            query=context_query,
            parent_task_id=task_id,
            goal_id=parent_task.get("goal_id"),
            context_policy=context_policy,
        )
        expected_output_schema = dict(data.expected_output_schema or {})
        allowed_tools = normalize_allowed_tools(data.allowed_tools)
        routing_decision_payload = worker_contract_service.build_routing_decision(
            agent_url=plan.agent_url,
            selected_by_policy=plan.selected_by_policy,
            task_kind=plan.effective_task_kind,
            required_capabilities=plan.effective_required_capabilities,
            selection=plan.selection,
            preferred_backend=plan.preferred_backend,
        )
        if plan.routing_hint:
            routing_decision_payload["copilot_hint"] = dict(plan.routing_hint)
        routing_decision = RoutingDecision(routing_decision_payload)
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
        )
        workspace_scope = derive_workspace_scope(
            parent_task=parent_task,
            subtask_id=subtask_id,
            worker_job_id=worker_job.id,
            agent_url=plan.agent_url,
        )
        worker_workspace = build_worker_workspace(
            scope=workspace_scope,
            parent_task_id=task_id,
            subtask_id=subtask_id,
            worker_job_id=worker_job.id,
            agent_url=plan.agent_url,
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
        delegation_payload = self._delegation_payload(
            request=request,
            plan=plan,
            subtask_id=subtask_id,
            context_bundle_id=context_bundle.id,
            retrieval_hints=retrieval_hints,
            context_policy=context_policy,
            worker_execution_context=worker_execution_context,
        )
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
            "callback_token": settings.agent_token or "",
            "source": "agent",
            "created_by": settings.agent_name or "hub",
            "context_bundle_policy": dict(context_policy),
        }


class TaskDelegationResultWriter:
    """Persists delegation side effects and builds the API response model."""

    def __init__(self, dependencies) -> None:
        self.dependencies = dependencies

    def forward_and_write(
        self,
        *,
        request: DelegationRequest,
        plan: TaskDelegationPlan,
        bundle: WorkerExecutionBundle,
    ) -> dict[str, Any]:
        try:
            policy_decision = plan.policy_decision or self._persist_manual_policy(
                request=request,
                plan=plan,
                bundle=bundle,
            )
            response = unwrap_api_envelope(
                self.dependencies.forward_task_to_worker(
                    plan.agent_url,
                    "/tasks",
                    bundle.delegation_payload,
                    token=request.data.agent_token or "",
                )
            )
        except Exception as exc:
            return {"error": "delegation_failed", "code": 502, "data": {"details": str(exc)}}

        self._update_parent_task(
            request=request,
            plan=plan,
            bundle=bundle,
            policy_decision=policy_decision,
        )
        return self._response_model(
            response=response,
            plan=plan,
            bundle=bundle,
            policy_decision=policy_decision,
        )

    def _persist_manual_policy(
        self,
        *,
        request: DelegationRequest,
        plan: TaskDelegationPlan,
        bundle: WorkerExecutionBundle,
    ):
        if plan.selected_by_policy:
            return plan.policy_decision
        return persist_policy_decision(
            decision_type="delegation",
            status="approved",
            policy_name="worker_capability_routing",
            policy_version="worker-routing-v2",
            reasons=(plan.selection.reasons if plan.selection else ["manual_override"]),
            details={
                "task_kind": request.data.task_kind,
                "required_capabilities": plan.effective_required_capabilities,
                "manual_override": True,
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
        plan: TaskDelegationPlan,
        bundle: WorkerExecutionBundle,
        policy_decision: Any,
    ) -> dict[str, Any]:
        return {
            "data": {
                "status": "delegated",
                "subtask_id": bundle.subtask_id,
                "agent_url": plan.agent_url,
                "response": response,
                "selected_by_policy": plan.selected_by_policy,
                "selection_reasons": plan.selection.reasons if plan.selection else ["manual_override"],
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
