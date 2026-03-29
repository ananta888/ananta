from __future__ import annotations

import time
from typing import Any

from agent.db_models import GoalDB
from agent.repository import goal_repo
from agent.services.task_runtime_service import update_local_task_status


class TaskLifecycleService:
    """Explicit task lifecycle use-cases to avoid scattered implicit status updates."""

    def materialize_from_plan_node(
        self,
        *,
        task_id: str,
        node: Any,
        team_id: str | None,
        goal_id: str | None,
        goal_trace_id: str | None,
        plan_id: str | None,
        parent_task_id: str | None,
        derivation_reason: str,
        derivation_depth: int,
        depends_on: list[str] | None,
    ) -> None:
        update_local_task_status(
            task_id,
            "todo",
            title=node.title,
            description=node.description,
            priority=node.priority,
            team_id=team_id,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
            plan_id=plan_id,
            plan_node_id=node.id,
            task_kind=(node.rationale or {}).get("task_kind"),
            verification_spec=dict(node.verification_spec or {}),
            parent_task_id=parent_task_id,
            source_task_id=parent_task_id,
            derivation_reason=derivation_reason,
            derivation_depth=derivation_depth,
            depends_on=depends_on if depends_on else None,
            event_type="task_materialized_from_plan",
            event_actor="planning_service",
            event_details={
                "plan_id": plan_id,
                "plan_node_id": node.id,
                "goal_id": goal_id,
            },
        )

    def attach_verification_result(
        self,
        *,
        task_id: str,
        current_status: str,
        verification_spec: dict[str, Any],
        verification_status: dict[str, Any],
    ) -> None:
        update_local_task_status(
            task_id,
            current_status,
            verification_spec=verification_spec,
            verification_status=verification_status,
            event_type="task_verification_updated",
            event_actor="verification_service",
            event_details={
                "verification_status": verification_status.get("status"),
                "record_id": verification_status.get("record_id"),
            },
        )


class GoalLifecycleService:
    """Explicit goal lifecycle transitions with consistent metadata updates."""

    def transition_goal(
        self,
        goal: GoalDB,
        *,
        target_status: str,
        reason: str | None = None,
        readiness: dict[str, Any] | None = None,
    ) -> GoalDB:
        goal.status = str(target_status or goal.status)
        goal.updated_at = time.time()
        if readiness is not None:
            goal.readiness = dict(readiness)
        if reason:
            current = dict(goal.execution_preferences or {})
            current["last_status_reason"] = str(reason)
            goal.execution_preferences = current
        return goal_repo.save(goal)


task_lifecycle_service = TaskLifecycleService()
goal_lifecycle_service = GoalLifecycleService()


def get_task_lifecycle_service() -> TaskLifecycleService:
    return task_lifecycle_service


def get_goal_lifecycle_service() -> GoalLifecycleService:
    return goal_lifecycle_service
