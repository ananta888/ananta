from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import current_app, has_app_context

from agent.models import TaskStatus
from agent.routes.tasks.orchestration_policy.read_model import build_orchestration_read_model
from agent.services.copilot_routing_advisor import (
    build_copilot_routing_prompt,
    extract_copilot_routing_hint,
    get_copilot_routing_advisor,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.task_delegation_services import (
    DelegationRequest,
    TaskDelegationPlan,
    TaskDelegationPlanner,
    TaskDelegationResultWriter,
    WorkerExecutionContextFactory,
)
from agent.services.task_context_policy_service import get_task_context_policy_service
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.services.task_runtime_service import forward_to_worker, get_local_task_status, update_local_task_status

__all__ = [
    "TaskOrchestrationDependencies",
    "TaskOrchestrationService",
    "get_task_orchestration_service",
    "build_copilot_routing_prompt",
    "extract_copilot_routing_hint",
]


@dataclass(frozen=True)
class TaskOrchestrationDependencies:
    get_task_status: Callable[[str], dict[str, Any] | None]
    update_task_status: Callable[..., Any]
    forward_task_to_worker: Callable[..., Any]
    repository_registry: Callable[[], Any]
    routing_advisor: Callable[[], Any]
    context_policy_service: Callable[[], Any]
    execution_tracking_service: Callable[[], Any]


@dataclass(frozen=True)
class CompletionOutcome:
    gate_results: dict[str, Any]
    gates_passed: bool
    final_status: str

    @property
    def exit_code(self) -> int:
        return 0 if self.gates_passed else 1


def _default_dependencies() -> TaskOrchestrationDependencies:
    return TaskOrchestrationDependencies(
        get_task_status=get_local_task_status,
        update_task_status=update_local_task_status,
        forward_task_to_worker=forward_to_worker,
        repository_registry=get_repository_registry,
        routing_advisor=get_copilot_routing_advisor,
        context_policy_service=get_task_context_policy_service,
        execution_tracking_service=get_task_execution_tracking_service,
    )


class TaskOrchestrationService:
    """Hub-owned Orchestrierungs-Fassade für Delegation, Completion und Read-Model.

    Fachliche Details sind in dedizierten Services gekapselt:

    * :mod:`agent.services.copilot_routing_advisor` – Copilot-Routing-Hints (Prompt/LLM/Parsing).
    * :mod:`agent.services.task_context_policy_service` – Context-Bundle-Policy,
      Retrieval-Hints und Task-Nachbarschaft.
    * :mod:`agent.services.workspace_scope_builder` – Workspace-Scope-Ableitung und Worker-Workspace.
    """

    def __init__(self, dependencies: TaskOrchestrationDependencies | None = None) -> None:
        self.dependencies = dependencies or _default_dependencies()
        self.delegation_planner = TaskDelegationPlanner(self.dependencies)
        self.execution_context_factory = WorkerExecutionContextFactory(self.dependencies)
        self.delegation_result_writer = TaskDelegationResultWriter(self.dependencies)

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
        parent_task = self.dependencies.get_task_status(task_id)
        if not parent_task:
            return {"error": "parent_task_not_found", "code": 404}

        delegation_request = DelegationRequest(
            parent_task=parent_task,
            task_id=task_id,
            data=data,
        )
        plan = self.delegation_planner.plan(
            request=delegation_request,
            agent_registry_service=agent_registry_service,
        )
        if not isinstance(plan, TaskDelegationPlan):
            return plan

        bundle = self.execution_context_factory.build(
            request=delegation_request,
            plan=plan,
            worker_job_service=worker_job_service,
            worker_contract_service=worker_contract_service,
        )
        return self.delegation_result_writer.forward_and_write(
            request=delegation_request,
            plan=plan,
            bundle=bundle,
        )

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

        task = self.dependencies.get_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}

        outcome = self._derive_completion_outcome(payload)
        output = str(payload.get("output") or "")
        actor = str(payload.get("actor") or "system")
        worker_job_id = self._resolve_worker_job_id(task, payload)
        trace_id = payload.get("trace_id") or task.get("goal_trace_id")

        record = self._persist_verification_record(
            verification_service=verification_service,
            task_id=task_id,
            trace_id=payload.get("trace_id"),
            output=output,
            outcome=outcome,
        )
        self._record_worker_result_if_present(
            worker_job_service=worker_job_service,
            worker_job_id=worker_job_id,
            task_id=task_id,
            actor=actor,
            final_status=outcome.final_status,
            output=output,
            gate=outcome.gate_results,
            trace_id=payload.get("trace_id"),
        )
        memory_entry = self._record_worker_result_memory(
            result_memory_service=result_memory_service,
            task=task,
            task_id=task_id,
            trace_id=trace_id,
            worker_job_id=worker_job_id,
            output=output,
            final_status=outcome.final_status,
            gate=outcome.gate_results,
            actor=actor,
            payload_artifacts=payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else None,
        )
        verification_status = self._build_verification_status(
            record=record,
            all_passed=outcome.gates_passed,
            memory_entry=memory_entry,
        )
        self._apply_completion_status_update(
            task_id=task_id,
            final_status=outcome.final_status,
            output=output,
            all_passed=outcome.gates_passed,
            verification_status=verification_status,
            actor=actor,
            gate=outcome.gate_results,
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
                "status": outcome.final_status,
                "gates_passed": outcome.gates_passed,
                "verification_status": verification_status,
                "evolution_trigger": evolution_trigger,
            }
        }

    @staticmethod
    def _derive_completion_outcome(payload: dict[str, Any]) -> CompletionOutcome:
        gate = payload.get("gate_results") or {}
        all_passed = bool(gate.get("passed", False))
        final_status = TaskStatus.COMPLETED.value if all_passed else TaskStatus.VERIFICATION_FAILED.value
        return CompletionOutcome(gate_results=dict(gate), gates_passed=all_passed, final_status=final_status)

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
        outcome: CompletionOutcome,
    ):
        return verification_service.create_or_update_record(
            task_id,
            trace_id=trace_id,
            output=output,
            exit_code=outcome.exit_code,
            gate_results=outcome.gate_results,
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

    def _apply_completion_status_update(
        self,
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
        self.dependencies.update_task_status(
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
        updated = self.dependencies.get_task_status(task_id) or {}
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
        repos = self.dependencies.repository_registry()
        model = build_orchestration_read_model([task.model_dump() for task in repos.task_repo.get_all()])
        model["recent_policy_decisions"] = [item.model_dump() for item in repos.policy_decision_repo.get_all(limit=50)]
        model["worker_execution_reconciliation"] = (
            self.dependencies.execution_tracking_service().build_execution_reconciliation_snapshot()
        )
        return model


task_orchestration_service = TaskOrchestrationService()


def get_task_orchestration_service() -> TaskOrchestrationService:
    return task_orchestration_service
