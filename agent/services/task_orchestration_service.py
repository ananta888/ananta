from __future__ import annotations

import uuid
from typing import Any

from agent.common.api_envelope import unwrap_api_envelope
from agent.config import settings
from agent.routes.tasks.orchestration_policy import (
    evaluate_worker_routing_policy,
    persist_policy_decision,
)
from agent.routes.tasks.orchestration_policy.read_model import build_orchestration_read_model
from agent.services.repository_registry import get_repository_registry
from agent.services.task_runtime_service import forward_to_worker, get_local_task_status, update_local_task_status


class TaskOrchestrationService:
    """Hub-owned orchestration use-cases for delegation, completion, and orchestration read models."""

    def delegate_task(self, *, task_id: str, data: Any, worker_job_service, result_memory_service, verification_service) -> dict[str, Any]:
        parent_task = get_local_task_status(task_id)
        if not parent_task:
            return {"error": "parent_task_not_found", "code": 404}

        agent_url = data.agent_url
        selected_by_policy = False
        selection = None
        if not agent_url:
            repos = get_repository_registry()
            selection, _decision = evaluate_worker_routing_policy(
                task=parent_task,
                workers=[worker.model_dump() for worker in repos.agent_repo.get_all()],
                decision_type="delegation",
                task_kind=data.task_kind,
                required_capabilities=data.required_capabilities,
                task_id=task_id,
            )
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
        context_bundle = worker_job_service.create_context_bundle(
            query=context_query,
            parent_task_id=task_id,
            goal_id=parent_task.get("goal_id"),
        )
        expected_output_schema = dict(data.expected_output_schema or {})
        allowed_tools = list(data.allowed_tools or [])
        worker_execution_context = {
            "instructions": data.subtask_description,
            "context_bundle_id": context_bundle.id,
            "context": {
                "context_text": context_bundle.context_text,
                "chunks": context_bundle.chunks,
                "token_estimate": context_bundle.token_estimate,
                "bundle_metadata": context_bundle.bundle_metadata,
            },
            "allowed_tools": allowed_tools,
            "expected_output_schema": expected_output_schema,
        }

        delegation_payload = {
            "id": subtask_id,
            "title": data.subtask_description[:200],
            "description": data.subtask_description,
            "parent_task_id": task_id,
            "priority": data.priority,
            "team_id": parent_task.get("team_id"),
            "goal_id": parent_task.get("goal_id"),
            "goal_trace_id": parent_task.get("goal_trace_id"),
            "task_kind": data.task_kind or parent_task.get("task_kind"),
            "required_capabilities": data.required_capabilities or parent_task.get("required_capabilities") or [],
            "context_bundle_id": context_bundle.id,
            "worker_execution_context": worker_execution_context,
            "callback_url": callback_url,
            "callback_token": settings.agent_token or "",
            "source": "agent",
            "created_by": settings.agent_name or "hub",
        }
        worker_job = worker_job_service.create_worker_job(
            parent_task_id=task_id,
            subtask_id=subtask_id,
            worker_url=agent_url,
            context_bundle_id=context_bundle.id,
            allowed_tools=allowed_tools,
            expected_output_schema=expected_output_schema,
            metadata={
                "selected_by_policy": selected_by_policy,
                "task_kind": data.task_kind or parent_task.get("task_kind"),
                "required_capabilities": data.required_capabilities or parent_task.get("required_capabilities") or [],
            },
        )
        try:
            persist_policy_decision(
                decision_type="delegation",
                status="approved",
                policy_name="worker_capability_routing",
                policy_version="worker-routing-v1",
                reasons=(selection.reasons if selection else ["manual_override"]),
                details={
                    "task_kind": data.task_kind,
                    "required_capabilities": data.required_capabilities,
                    "manual_override": not selected_by_policy,
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
                "context_bundle_id": context_bundle.id,
                "worker_job_id": worker_job.id,
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
    ) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}

        gate = payload.get("gate_results") or {}
        all_passed = bool(gate.get("passed", False))
        final_status = "completed" if all_passed else "failed"
        record = verification_service.create_or_update_record(
            task_id,
            trace_id=payload.get("trace_id"),
            output=str(payload.get("output") or ""),
            exit_code=0 if all_passed else 1,
            gate_results=gate,
        )
        worker_job_id = str(payload.get("worker_job_id") or task.get("current_worker_job_id") or "").strip() or None
        actor = str(payload.get("actor") or "system")
        if worker_job_id:
            worker_job_service.record_worker_result(
                worker_job_id=worker_job_id,
                task_id=task_id,
                worker_url=actor,
                status=final_status,
                output=str(payload.get("output") or ""),
                metadata={"gate_results": gate, "trace_id": payload.get("trace_id")},
            )
        memory_entry = result_memory_service.record_worker_result_memory(
            task_id=task_id,
            goal_id=task.get("goal_id"),
            trace_id=payload.get("trace_id") or task.get("goal_trace_id"),
            worker_job_id=worker_job_id,
            title=task.get("title"),
            output=str(payload.get("output") or ""),
            artifact_refs=[{"kind": "task_output", "task_id": task_id, "worker_job_id": worker_job_id}],
            retrieval_tags=[
                value
                for value in [
                    str(task.get("task_kind") or "").strip(),
                    str(task.get("goal_id") or "").strip(),
                    str(final_status).strip(),
                ]
                if value
            ],
            metadata={"gate_results": gate, "actor": actor},
        )
        verification_status = {
            "record_id": record.id if record else None,
            "status": record.status if record else ("passed" if all_passed else "failed"),
            "retry_count": record.retry_count if record else 0,
            "repair_attempts": record.repair_attempts if record else 0,
            "escalation_reason": record.escalation_reason if record else None,
            "memory_entry_id": memory_entry.id if memory_entry else None,
        }
        update_local_task_status(
            task_id,
            final_status,
            last_output=str(payload.get("output") or ""),
            last_exit_code=0 if all_passed else 1,
            verification_status=verification_status,
            event_type="task_completed_with_gates",
            event_actor=actor,
            event_details={"gate_results": gate, "trace_id": payload.get("trace_id"), "worker_job_id": worker_job_id},
        )
        return {
            "data": {
                "task_id": task_id,
                "status": final_status,
                "gates_passed": all_passed,
                "verification_status": verification_status,
            }
        }

    def orchestration_read_model(self) -> dict[str, Any]:
        repos = get_repository_registry()
        model = build_orchestration_read_model([task.model_dump() for task in repos.task_repo.get_all()])
        model["recent_policy_decisions"] = [item.model_dump() for item in repos.policy_decision_repo.get_all(limit=50)]
        return model


task_orchestration_service = TaskOrchestrationService()


def get_task_orchestration_service() -> TaskOrchestrationService:
    return task_orchestration_service
