from __future__ import annotations

import time
from typing import Any

from agent.db_models import GoalDB
from agent.repository import goal_repo
from agent.services.task_queue_service import get_task_queue_service
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
        rationale = dict(node.rationale or {})
        blueprint_provenance = {
            "blueprint_id": str(rationale.get("blueprint_id") or "").strip(),
            "blueprint_name": str(rationale.get("blueprint_name") or "").strip(),
            "blueprint_artifact_id": str(rationale.get("blueprint_artifact_id") or "").strip(),
            "blueprint_role_name": str(rationale.get("blueprint_role_name") or "").strip(),
            "template_name": str(rationale.get("template_name") or "").strip(),
            "template_id": str(rationale.get("template_id") or "").strip(),
        }
        blueprint_provenance = {
            key: value for key, value in blueprint_provenance.items() if value
        }
        worker_execution_context = {
            "kind": "worker_execution_context",
            "version": "v1",
            "planning_provenance": {
                "plan_id": plan_id,
                "plan_node_id": node.id,
                "goal_id": goal_id,
                **blueprint_provenance,
            },
            "routing_hints": {
                "task_kind": rationale.get("task_kind"),
                "required_capabilities": list(rationale.get("required_capabilities") or []),
                "retrieval_intent": rationale.get("retrieval_intent"),
                "required_context_scope": rationale.get("required_context_scope"),
                "preferred_bundle_mode": rationale.get("preferred_bundle_mode"),
            },
        }
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="todo",
            title=node.title,
            description=node.description,
            priority=node.priority,
            created_by="planning_service",
            source="goal_plan",
            team_id=team_id,
            event_type="task_materialized_from_plan",
            event_channel="planning_service",
            event_details={"plan_id": plan_id, "plan_node_id": node.id, "goal_id": goal_id},
            extra_fields={
                "goal_id": goal_id,
                "goal_trace_id": goal_trace_id,
                "plan_id": plan_id,
                "plan_node_id": node.id,
                "task_kind": rationale.get("task_kind"),
                "retrieval_intent": rationale.get("retrieval_intent"),
                "required_context_scope": rationale.get("required_context_scope"),
                "preferred_bundle_mode": rationale.get("preferred_bundle_mode"),
                "required_capabilities": list(rationale.get("required_capabilities") or []),
                "verification_spec": dict(node.verification_spec or {}),
                "worker_execution_context": worker_execution_context,
                "status_reason_details": {
                    "materialized_from_plan": True,
                    "planning_provenance": {
                        "plan_id": plan_id,
                        "plan_node_id": node.id,
                        **blueprint_provenance,
                    },
                },
                "parent_task_id": parent_task_id,
                "source_task_id": parent_task_id,
                "derivation_reason": derivation_reason,
                "derivation_depth": derivation_depth,
                "depends_on": depends_on if depends_on else None,
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
