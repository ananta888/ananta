from __future__ import annotations

from typing import Any

from agent.services.deterministic_repair_path_service import (
    execute_repair_procedure,
    match_failure_signatures,
    run_diagnosis_playbook,
    select_repair_procedure_from_catalog,
)
from agent.services.task_handler_registry import TaskHandler


class DeterministicRepairHandler(TaskHandler):
    """TaskHandler for admin_repair / deterministic_repair task_kind.

    Executes the pre-computed deterministic repair procedure instead of
    using LLM-based propose. Falls through to generic handler when the
    task carries no deterministic repair data.
    """

    def propose(self, **kwargs: Any) -> dict | None:
        task = kwargs.get("task") or {}
        return self._propose_repair_step(task)

    def execute(self, **kwargs: Any) -> dict | None:
        task = kwargs.get("task") or {}
        request_data = kwargs.get("request_data")
        return self._execute_repair_step(task, request_data)

    @staticmethod
    def _get_foundation(task: dict) -> dict | None:
        ctx = dict(task.get("worker_execution_context") or {})
        foundation = ctx.get("deterministic_repair_foundation")
        if isinstance(foundation, dict) and foundation.get("repair_procedure"):
            return foundation
        mode_data = dict(task.get("mode_data") or {})
        foundation = mode_data.get("deterministic_repair_foundation")
        if isinstance(foundation, dict) and foundation.get("repair_procedure"):
            return foundation
        return None

    @staticmethod
    def _propose_repair_step(task: dict) -> dict | None:
        foundation = DeterministicRepairHandler._get_foundation(task)
        if not foundation:
            return None

        procedure = foundation.get("repair_procedure", {})
        preview = foundation.get("repair_preview", {})
        diagnosis = foundation.get("diagnosis_artifact", {})

        steps = list(procedure.get("steps") or preview.get("steps") or [])
        if not steps:
            return None

        def _step_id(s: dict, idx: int) -> str:
            return str(s.get("step_id") or s.get("id") or f"step-{idx}")

        return {
            "command": None,
            "tool_calls": None,
            "structured_action": {
                "action": "deterministic_repair",
                "execution_mode": "step_confirmed",
                "diagnosis": {
                    "problem_class": diagnosis.get("problem_class"),
                    "confidence": diagnosis.get("confidence"),
                    "likely_causes": diagnosis.get("likely_causes"),
                },
                "procedure": {
                    "procedure_id": procedure.get("id") or procedure.get("procedure_id", "repair-procedure-v1"),
                    "safety_class": procedure.get("safety_class", "bounded"),
                    "steps": [
                        {
                            "step_id": _step_id(s, i),
                            "title": s.get("title", ""),
                            "action_class": s.get("action_class", "inspect_state"),
                            "mutation_candidate": bool(s.get("mutation_candidate")),
                            "rollback_supported": bool(s.get("rollback_supported", True)),
                            "verification_required": bool(s.get("verification_required", True)),
                            "expected_verification": s.get("expected_verification", ""),
                        }
                        for i, s in enumerate(steps)
                    ],
                },
                "preview": {
                    "step_count": len(steps),
                    "mutation_step_ids": [
                        s.get("step_id") for s in steps if s.get("mutation_candidate")
                    ],
                    "dry_run_default": preview.get("dry_run_default", True),
                },
            },
            "review": {
                "required": any(s.get("mutation_candidate") for s in steps),
                "status": "pending",
            },
        }

    @staticmethod
    def _execute_repair_step(task: dict, request_data: Any) -> dict | None:
        foundation = DeterministicRepairHandler._get_foundation(task)
        if not foundation:
            return None

        procedure = foundation.get("repair_procedure", {})
        steps = list(procedure.get("steps") or [])
        if not steps:
            return None

        def _step_id(s: dict) -> str:
            return str(s.get("step_id") or s.get("id") or "")

        current_step_index = 0
        if request_data:
            requested_step_id = getattr(request_data, "step_id", None) or (
                request_data.get("step_id") if isinstance(request_data, dict) else None
            )
            if requested_step_id:
                for i, s in enumerate(steps):
                    if _step_id(s) == requested_step_id:
                        current_step_index = i
                        break

        step = steps[current_step_index] if current_step_index < len(steps) else steps[-1]
        action_class = step.get("action_class", "inspect_state")

        return {
            "executed_step_id": _step_id(step),
            "action_class": action_class,
            "execution_mode": "deterministic",
            "command_hint": step.get("command_hint", ""),
            "verification": {
                "required": bool(step.get("verification_required", True)),
                "expected": step.get("expected_verification", ""),
            },
            "rollback": {
                "supported": bool(step.get("rollback_supported", True)),
                "hint": step.get("rollback_hint", "no_mutation"),
            },
        }
