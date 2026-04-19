from __future__ import annotations

import uuid
from typing import Any

from flask import current_app, has_app_context

from agent.common.api_envelope import unwrap_api_envelope
from agent.config import settings
from agent.models import TaskStatus
from agent.research_backend import resolve_research_backend_config
from agent.routes.tasks.orchestration_policy import (
    derive_required_capabilities,
    evaluate_worker_routing_policy,
    persist_policy_decision,
)
from agent.routes.tasks.orchestration_policy.read_model import build_orchestration_read_model
from agent.services.copilot_routing_advisor import (
    build_copilot_routing_prompt,
    extract_copilot_routing_hint,
    get_copilot_routing_advisor,
)
from agent.services.task_context_policy_service import get_task_context_policy_service
from agent.services.task_execution_policy_service import normalize_allowed_tools
from agent.services.repository_registry import get_repository_registry
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.services.task_runtime_service import forward_to_worker, get_local_task_status, update_local_task_status
from agent.services.workspace_scope_builder import build_worker_workspace, derive_workspace_scope

__all__ = [
    "TaskOrchestrationService",
    "get_task_orchestration_service",
    "build_copilot_routing_prompt",
    "extract_copilot_routing_hint",
]


class TaskOrchestrationService:
    """Hub-owned Orchestrierungs-Fassade für Delegation, Completion und Read-Model.

    Fachliche Details sind in dedizierten Services gekapselt:

    * :mod:`agent.services.copilot_routing_advisor` – Copilot-Routing-Hints (Prompt/LLM/Parsing).
    * :mod:`agent.services.task_context_policy_service` – Context-Bundle-Policy,
      Retrieval-Hints und Task-Nachbarschaft.
    * :mod:`agent.services.workspace_scope_builder` – Workspace-Scope-Ableitung und Worker-Workspace.
    """

    def delegate_task(
        self,
        *,
        task_id: str,
        data: Any,
        worker_job_service,
        worker_contract_service,
        agent_registry_service,
        result_memory_service,
        verification_service,
    ) -> dict[str, Any]:
        parent_task = get_local_task_status(task_id)
        if not parent_task:
            return {"error": "parent_task_not_found", "code": 404}

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
        preferred_backend = (
            resolve_research_backend_config(
                agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {}
            ).get("provider")
            if str(effective_task_kind or "").strip().lower() == "research"
            else None
        )
        if not agent_url:
            repos = get_repository_registry()
            available_workers = [
                agent_registry_service.build_directory_entry(agent=worker, timeout=300)
                for worker in repos.agent_repo.get_all()
            ]
            routing_hint = get_copilot_routing_advisor().resolve_routing_hint(
                task=parent_task,
                workers=available_workers,
                task_kind=effective_task_kind,
                required_capabilities=effective_required_capabilities,
            )
            selection, _decision = evaluate_worker_routing_policy(
                task=parent_task,
                workers=available_workers,
                decision_type="delegation",
                task_kind=effective_task_kind,
                required_capabilities=effective_required_capabilities,
                task_id=task_id,
                extra_details={"copilot_routing_hint": routing_hint} if routing_hint else None,
            )
            policy_decision = _decision
            agent_url = selection.worker_url
            selected_by_policy = True
            if not agent_url:
                return {
                    "error": "no_worker_available",
                    "code": 409,
                    "data": {"reasons": selection.reasons},
                }

        subtask_id = f"sub-{uuid.uuid4()}"
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        callback_url = f"{my_url.rstrip('/')}/tasks/{task_id}/subtask-callback"
        context_query = str(data.context_query or "").strip() or " ".join(
            item
            for item in [
                str(parent_task.get("title") or "").strip(),
                str(parent_task.get("description") or "").strip(),
                str(data.subtask_description or "").strip(),
            ]
            if item
        )
        context_policy, retrieval_hints, task_neighborhood = (
            get_task_context_policy_service().build_context_policy(
                parent_task=parent_task,
                data=data,
                effective_task_kind=effective_task_kind,
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
        routing_decision = worker_contract_service.build_routing_decision(
            agent_url=agent_url,
            selected_by_policy=selected_by_policy,
            task_kind=effective_task_kind,
            required_capabilities=effective_required_capabilities,
            selection=selection,
            preferred_backend=preferred_backend,
        )
        if routing_hint:
            routing_decision["copilot_hint"] = dict(routing_hint)
        worker_job = worker_job_service.create_worker_job(
            parent_task_id=task_id,
            subtask_id=subtask_id,
            worker_url=agent_url,
            context_bundle_id=context_bundle.id,
            allowed_tools=allowed_tools,
            expected_output_schema=expected_output_schema,
            metadata=worker_contract_service.build_job_metadata(
                routing_decision=routing_decision,
                task_kind=effective_task_kind,
                required_capabilities=effective_required_capabilities,
                context_policy=context_policy,
                extra_metadata={"selected_by_policy": selected_by_policy},
            ),
        )
        workspace_scope = derive_workspace_scope(
            parent_task=parent_task,
            subtask_id=subtask_id,
            worker_job_id=worker_job.id,
            agent_url=agent_url,
        )
        worker_workspace = build_worker_workspace(
            scope=workspace_scope,
            parent_task_id=task_id,
            subtask_id=subtask_id,
            worker_job_id=worker_job.id,
            agent_url=agent_url,
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
            routing_decision=routing_decision,
        )

        delegation_payload = {
            "id": subtask_id,
            "title": data.subtask_description[:200],
            "description": data.subtask_description,
            "parent_task_id": task_id,
            "priority": data.priority,
            "team_id": parent_task.get("team_id"),
            "goal_id": parent_task.get("goal_id"),
            "goal_trace_id": parent_task.get("goal_trace_id"),
            "task_kind": effective_task_kind,
            "retrieval_intent": retrieval_hints["retrieval_intent"],
            "required_context_scope": retrieval_hints["required_context_scope"],
            "preferred_bundle_mode": retrieval_hints["preferred_bundle_mode"],
            "required_capabilities": effective_required_capabilities,
            "context_bundle_id": context_bundle.id,
            "worker_execution_context": worker_execution_context,
            "callback_url": callback_url,
            "callback_token": settings.agent_token or "",
            "source": "agent",
            "created_by": settings.agent_name or "hub",
            "context_bundle_policy": dict(context_policy),
        }
        try:
            if not selected_by_policy:
                policy_decision = persist_policy_decision(
                    decision_type="delegation",
                    status="approved",
                    policy_name="worker_capability_routing",
                    policy_version="worker-routing-v2",
                    reasons=(selection.reasons if selection else ["manual_override"]),
                    details={
                        "task_kind": data.task_kind,
                        "required_capabilities": effective_required_capabilities,
                        "manual_override": True,
                        "copilot_routing_hint": routing_hint,
                        "context_bundle_policy": context_policy,
                        "retrieval_hints": retrieval_hints,
                        "task_neighborhood": task_neighborhood,
                    },
                    task_id=task_id,
                    worker_url=agent_url,
                )
            response = unwrap_api_envelope(
                forward_to_worker(agent_url, "/tasks", delegation_payload, token=data.agent_token or "")
            )
        except Exception as exc:
            return {"error": "delegation_failed", "code": 502, "data": {"details": str(exc)}}

        subtasks = list(parent_task.get("subtasks") or [])
        subtasks.append(
            {
                "id": subtask_id,
                "agent_url": agent_url,
                "description": data.subtask_description,
                "status": "created",
            }
        )
        update_local_task_status(
            task_id,
            parent_task.get("status", "in_progress"),
            context_bundle_id=context_bundle.id,
            current_worker_job_id=worker_job.id,
            worker_execution_context=worker_execution_context,
            subtasks=subtasks,
            event_type="task_delegated",
            event_actor="hub",
            event_details={
                "delegated_to": agent_url,
                "subtask_id": subtask_id,
                "context_bundle_id": context_bundle.id,
                "worker_job_id": worker_job.id,
                "policy": "hub_central_queue",
                "selected_by_policy": selected_by_policy,
                "copilot_routing_hint": routing_hint,
                "context_bundle_policy": context_policy,
                "retrieval_hints": retrieval_hints,
                "task_neighborhood": task_neighborhood,
                "workspace_scope": workspace_scope,
                "policy_decision_id": getattr(policy_decision, "id", None),
            },
        )
        return {
            "data": {
                "status": "delegated",
                "subtask_id": subtask_id,
                "agent_url": agent_url,
                "response": response,
                "selected_by_policy": selected_by_policy,
                "selection_reasons": selection.reasons if selection else ["manual_override"],
                "worker_selection": routing_decision,
                "copilot_routing_hint": routing_hint,
                "policy_decision_id": getattr(policy_decision, "id", None),
                "context_bundle_id": context_bundle.id,
                "worker_job_id": worker_job.id,
                "context_bundle_policy": context_policy,
                "retrieval_hints": retrieval_hints,
                "task_neighborhood": task_neighborhood,
                "workspace_scope": workspace_scope,
            }
        }

    def complete_task(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        verification_service,
        worker_job_service,
        result_memory_service,
        evolution_service=None,
    ) -> dict[str, Any]:
        """Complete-Task-Orchestrator: Verifikation → Worker-Result → Result-Memory →
        Status-Update → Evolution-Trigger. Jeder Teilschritt lebt in einer eigenen,
        explizit benannten Methode, damit Fehler klar zuordbar bleiben und die
        Seiteneffekt-Reihenfolge nachvollziehbar ist.
        """

        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}

        gate, all_passed, final_status = self._derive_completion_outcome(payload)
        output = str(payload.get("output") or "")
        actor = str(payload.get("actor") or "system")
        worker_job_id = self._resolve_worker_job_id(task, payload)
        trace_id = payload.get("trace_id") or task.get("goal_trace_id")

        record = self._persist_verification_record(
            verification_service=verification_service,
            task_id=task_id,
            trace_id=payload.get("trace_id"),
            output=output,
            all_passed=all_passed,
            gate=gate,
        )
        self._record_worker_result_if_present(
            worker_job_service=worker_job_service,
            worker_job_id=worker_job_id,
            task_id=task_id,
            actor=actor,
            final_status=final_status,
            output=output,
            gate=gate,
            trace_id=payload.get("trace_id"),
        )
        memory_entry = self._record_worker_result_memory(
            result_memory_service=result_memory_service,
            task=task,
            task_id=task_id,
            trace_id=trace_id,
            worker_job_id=worker_job_id,
            output=output,
            final_status=final_status,
            gate=gate,
            actor=actor,
            payload_artifacts=payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else None,
        )
        verification_status = self._build_verification_status(
            record=record,
            all_passed=all_passed,
            memory_entry=memory_entry,
        )
        self._apply_completion_status_update(
            task_id=task_id,
            final_status=final_status,
            output=output,
            all_passed=all_passed,
            verification_status=verification_status,
            actor=actor,
            gate=gate,
            trace_id=payload.get("trace_id"),
            worker_job_id=worker_job_id,
        )
        evolution_trigger = self._trigger_evolution_if_configured(
            task_id=task_id,
            evolution_service=evolution_service,
        )
        return {
            "data": {
                "task_id": task_id,
                "status": final_status,
                "gates_passed": all_passed,
                "verification_status": verification_status,
                "evolution_trigger": evolution_trigger,
            }
        }

    @staticmethod
    def _derive_completion_outcome(payload: dict[str, Any]) -> tuple[dict[str, Any], bool, str]:
        gate = payload.get("gate_results") or {}
        all_passed = bool(gate.get("passed", False))
        final_status = TaskStatus.COMPLETED.value if all_passed else TaskStatus.VERIFICATION_FAILED.value
        return gate, all_passed, final_status

    @staticmethod
    def _resolve_worker_job_id(task: dict[str, Any], payload: dict[str, Any]) -> str | None:
        return (
            str(payload.get("worker_job_id") or task.get("current_worker_job_id") or "").strip()
            or None
        )

    @staticmethod
    def _persist_verification_record(
        *,
        verification_service,
        task_id: str,
        trace_id: Any,
        output: str,
        all_passed: bool,
        gate: dict[str, Any],
    ):
        return verification_service.create_or_update_record(
            task_id,
            trace_id=trace_id,
            output=output,
            exit_code=0 if all_passed else 1,
            gate_results=gate,
        )

    @staticmethod
    def _record_worker_result_if_present(
        *,
        worker_job_service,
        worker_job_id: str | None,
        task_id: str,
        actor: str,
        final_status: str,
        output: str,
        gate: dict[str, Any],
        trace_id: Any,
    ) -> None:
        if not worker_job_id:
            return
        worker_job_service.record_worker_result(
            worker_job_id=worker_job_id,
            task_id=task_id,
            worker_url=actor,
            status=final_status,
            output=output,
            metadata={"gate_results": gate, "trace_id": trace_id},
        )

    @staticmethod
    def _record_worker_result_memory(
        *,
        result_memory_service,
        task: dict[str, Any],
        task_id: str,
        trace_id: Any,
        worker_job_id: str | None,
        output: str,
        final_status: str,
        gate: dict[str, Any],
        actor: str,
        payload_artifacts: list | None,
    ):
        retrieval_tags = [
            value
            for value in [
                str(task.get("task_kind") or "").strip(),
                str(task.get("goal_id") or "").strip(),
                str(final_status).strip(),
            ]
            if value
        ]
        return result_memory_service.record_worker_result_memory(
            task_id=task_id,
            goal_id=task.get("goal_id"),
            trace_id=trace_id,
            worker_job_id=worker_job_id,
            title=task.get("title"),
            output=output,
            artifact_refs=list(
                payload_artifacts
                or [{"kind": "task_output", "task_id": task_id, "worker_job_id": worker_job_id}]
            ),
            retrieval_tags=retrieval_tags,
            metadata={"gate_results": gate, "actor": actor},
        )

    @staticmethod
    def _build_verification_status(*, record, all_passed: bool, memory_entry) -> dict[str, Any]:
        return {
            "record_id": record.id if record else None,
            "status": record.status if record else ("passed" if all_passed else "failed"),
            "retry_count": record.retry_count if record else 0,
            "repair_attempts": record.repair_attempts if record else 0,
            "escalation_reason": record.escalation_reason if record else None,
            "memory_entry_id": memory_entry.id if memory_entry else None,
        }

    @staticmethod
    def _apply_completion_status_update(
        *,
        task_id: str,
        final_status: str,
        output: str,
        all_passed: bool,
        verification_status: dict[str, Any],
        actor: str,
        gate: dict[str, Any],
        trace_id: Any,
        worker_job_id: str | None,
    ) -> None:
        update_local_task_status(
            task_id,
            final_status,
            last_output=output,
            last_exit_code=0 if all_passed else 1,
            verification_status=verification_status,
            event_type="task_completed_with_gates",
            event_actor=actor,
            event_details={
                "gate_results": gate,
                "trace_id": trace_id,
                "worker_job_id": worker_job_id,
            },
        )

    def _trigger_evolution_if_configured(
        self,
        *,
        task_id: str,
        evolution_service,
    ) -> dict[str, Any]:
        updated = get_local_task_status(task_id) or {}
        return self._maybe_trigger_evolution_analysis(
            task=dict(updated or {}),
            config=current_app.config.get("AGENT_CONFIG", {}) if has_app_context() else {},
            evolution_service=evolution_service,
        )

    def _maybe_trigger_evolution_analysis(
        self,
        *,
        task: dict[str, Any],
        config: dict[str, Any],
        evolution_service,
    ) -> dict[str, Any]:
        if evolution_service is None:
            return {"status": "skipped", "reason": "evolution_service_unavailable"}
        try:
            decision = evolution_service.evaluate_auto_trigger(task, config=config)
            if not decision.allowed or decision.trigger is None:
                return {"status": "skipped", "reasons": decision.reasons}
            result = evolution_service.analyze_task(
                str(task.get("id") or ""),
                provider_name=None,
                config=config,
                trigger=decision.trigger,
            )
            return {
                "status": "triggered",
                "run_id": result.run_id,
                "provider_name": result.provider_name,
                "proposal_ids": list(result.proposal_ids),
                "reasons": decision.reasons,
            }
        except Exception as exc:
            if has_app_context():
                current_app.logger.warning("Evolution auto-trigger failed for task %s: %s", task.get("id"), exc)
            return {"status": "failed", "reason": str(exc)}

    def orchestration_read_model(self) -> dict[str, Any]:
        repos = get_repository_registry()
        model = build_orchestration_read_model([task.model_dump() for task in repos.task_repo.get_all()])
        model["recent_policy_decisions"] = [item.model_dump() for item in repos.policy_decision_repo.get_all(limit=50)]
        model["worker_execution_reconciliation"] = get_task_execution_tracking_service().build_execution_reconciliation_snapshot()
        return model


task_orchestration_service = TaskOrchestrationService()


def get_task_orchestration_service() -> TaskOrchestrationService:
    return task_orchestration_service
