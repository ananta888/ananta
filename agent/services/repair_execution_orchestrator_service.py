"""Hub-side repair execution orchestrator.

DRR-T014: Hub-driven step-by-step repair execution. The Hub sends one
authorized step at a time and evaluates the result before authorizing next.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    RepairProcedure,
    RepairStep,
)
from worker.repair.repair_procedure_runner import RepairProcedureRunner


_TERMINAL_STEP_STATUSES = frozenset({"failed", "denied", "escalated", "verification_failed"})


class RepairExecutionOrchestrator:
    """Hub-side step-by-step repair orchestrator. DRR-T014.

    Issues one step at a time to RepairProcedureRunner, evaluates the
    result, and decides whether to advance or stop.
    """

    def __init__(
        self,
        procedure: RepairProcedure,
        *,
        task_id: str = "",
        actor_ref: str = "hub",
        correlation_id: str = "",
    ) -> None:
        self.procedure = procedure
        self.task_id = task_id or f"repair-task-{uuid.uuid4().hex[:8]}"
        self.actor_ref = actor_ref
        self.correlation_id = correlation_id or f"repair-corr-{uuid.uuid4().hex[:8]}"
        self._current_step_index: int = 0
        self._step_results: list[dict[str, Any]] = []
        self._started_at: float = time.time()
        self._finished: bool = False
        self._outcome: str = "in_progress"

    def current_step(self) -> RepairStep | None:
        steps = self.procedure.steps
        if self._current_step_index >= len(steps):
            return None
        return steps[self._current_step_index]

    def _build_step_envelope_dict(
        self,
        step: RepairStep,
        *,
        approval_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the dict expected by RepairProcedureRunner.run_step(). DRR-T014."""
        envelope: dict[str, Any] = {
            "step": step.model_dump(),
            "task_id": self.task_id,
            "procedure_id": self.procedure.procedure_id,
            "audit_correlation_id": self.correlation_id,
            "parent_plan_id": getattr(self.procedure, "plan_id", None) or self.task_id,
            "step_id": step.step_id,
        }
        if approval_ref:
            envelope["approval_ref"] = approval_ref
        return envelope

    def execute_step(
        self,
        step: RepairStep,
        *,
        approval_ref: dict[str, Any] | None = None,
        capability_grant: CapabilityGrant | None = None,
    ) -> dict[str, Any]:
        """Execute a single step and record the result. DRR-T014."""
        if self._finished:
            return {"status": "terminal", "step_id": step.step_id, "reason": "orchestrator_already_finished"}

        runner = RepairProcedureRunner()
        step_env_dict = self._build_step_envelope_dict(step, approval_ref=approval_ref)
        step_result = runner.run_step(step_env_dict)

        result_dict: dict[str, Any] = {
            "step_id": step.step_id,
            "status": step_result.status.value,
            "reason_code": step_result.reason_code,
            "started_at": step_result.started_at,
            "ended_at": step_result.ended_at,
        }
        self._step_results.append(result_dict)

        status = step_result.status.value
        if status in _TERMINAL_STEP_STATUSES:
            self._finished = True
            self._outcome = status
        elif status == "approval_required":
            self._finished = True
            self._outcome = "needs_approval"
        else:
            self._current_step_index += 1
            if self._current_step_index >= len(self.procedure.steps):
                self._finished = True
                self._outcome = "completed"

        return result_dict

    def advance(
        self,
        *,
        approval_ref: dict[str, Any] | None = None,
        capability_grant: CapabilityGrant | None = None,
    ) -> dict[str, Any]:
        """Execute the next step in sequence. DRR-T014."""
        step = self.current_step()
        if step is None:
            self._finished = True
            self._outcome = "completed"
            return {"status": "completed", "step_id": None}
        return self.execute_step(
            step, approval_ref=approval_ref, capability_grant=capability_grant
        )

    def run_all(
        self,
        *,
        approval_ref: dict[str, Any] | None = None,
        capability_grant: CapabilityGrant | None = None,
    ) -> dict[str, Any]:
        """Run all steps until completion or stop condition. DRR-T014."""
        while not self._finished:
            result = self.advance(
                approval_ref=approval_ref, capability_grant=capability_grant
            )
            stop_status = result.get("status", "")
            if stop_status in _TERMINAL_STEP_STATUSES | {"needs_approval", "terminal"}:
                break
        return self.summary()

    def summary(self) -> dict[str, Any]:
        """Return orchestration summary. DRR-T014."""
        return {
            "task_id": self.task_id,
            "procedure_id": self.procedure.procedure_id,
            "outcome": self._outcome,
            "finished": self._finished,
            "step_results": list(self._step_results),
            "steps_completed": self._current_step_index,
            "total_steps": len(self.procedure.steps),
            "elapsed_seconds": round(time.time() - self._started_at, 3),
        }


def run_hub_driven_repair(
    procedure: RepairProcedure,
    *,
    task_id: str = "",
    actor_ref: str = "hub",
    approval_ref: dict[str, Any] | None = None,
    capability_grant: CapabilityGrant | None = None,
    correlation_id: str = "",
) -> dict[str, Any]:
    """Hub-driven step-by-step repair execution entry point. DRR-T014."""
    orchestrator = RepairExecutionOrchestrator(
        procedure,
        task_id=task_id,
        actor_ref=actor_ref,
        correlation_id=correlation_id,
    )
    return orchestrator.run_all(
        approval_ref=approval_ref,
        capability_grant=capability_grant,
    )
