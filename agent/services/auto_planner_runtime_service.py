from __future__ import annotations

from typing import Any


class AutoPlannerRuntimeService:
    """Endpoint-facing use-cases for auto-planner status, configuration, planning, and follow-up analysis."""

    def status(self, planner) -> dict[str, Any]:
        return planner.status()

    def configure(self, *, planner, data: dict[str, Any]) -> dict[str, Any]:
        return planner.configure(
            enabled=data.get("enabled"),
            auto_followup_enabled=data.get("auto_followup_enabled"),
            max_subtasks_per_goal=data.get("max_subtasks_per_goal"),
            default_priority=data.get("default_priority"),
            auto_start_autopilot=data.get("auto_start_autopilot"),
            llm_timeout=data.get("llm_timeout"),
            llm_retry_attempts=data.get("llm_retry_attempts"),
            llm_retry_backoff=data.get("llm_retry_backoff"),
        )

    def plan_goal(self, *, planner, data: dict[str, Any]) -> dict[str, Any]:
        goal = str(data.get("goal") or "").strip()
        if not goal:
            return {"error": "goal_required", "code": 400}
        result = planner.plan_goal(
            goal=goal,
            context=data.get("context"),
            team_id=data.get("team_id"),
            parent_task_id=data.get("parent_task_id"),
            create_tasks=bool(data.get("create_tasks", True)),
            use_template=bool(data.get("use_template", True)),
            use_repo_context=bool(data.get("use_repo_context", True)),
        )
        if result.get("error"):
            return {"error": result["error"], "code": 400}
        return {"data": result, "code": 201}

    def analyze_task_for_followups(self, *, planner, task_id: str, data: dict[str, Any]) -> dict[str, Any]:
        result = planner.analyze_and_create_followups(
            task_id=task_id,
            output=data.get("output"),
            exit_code=data.get("exit_code"),
        )
        if result.get("error"):
            return {"error": result["error"], "code": 400}
        return {"data": result}


auto_planner_runtime_service = AutoPlannerRuntimeService()


def get_auto_planner_runtime_service() -> AutoPlannerRuntimeService:
    return auto_planner_runtime_service
