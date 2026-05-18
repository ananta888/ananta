from __future__ import annotations

import time
from typing import Any

from agent.db_models import GoalDB
from agent.repository import goal_repo, task_repo
from agent.services.goal_execution_contract_service import get_goal_execution_contract_service
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.task_queue_service import get_task_queue_service
from agent.services.task_runtime_service import update_local_task_status


def _merge_rag_sources(goal_sources: dict, task_kind: str) -> dict:
    try:
        from flask import current_app, has_app_context
        agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
    except Exception:
        agent_cfg = {}
    kc_cfg = dict((agent_cfg.get("knowledge_context") or {}).get("auto_include") or {})
    auto_kinds = [str(k).strip().lower() for k in list(kc_cfg.get("task_kinds") or []) if k]
    include_defaults = not auto_kinds or not task_kind or task_kind.lower() in auto_kinds

    def _ids(src: dict, key: str) -> list[str]:
        return [str(v).strip() for v in list(src.get(key) or []) if str(v).strip()]

    collection_ids = list(dict.fromkeys(
        _ids(goal_sources, "knowledge_collection_ids")
        + (_ids(kc_cfg, "knowledge_collection_ids") if include_defaults else [])
    ))
    artifact_ids = list(dict.fromkeys(
        _ids(goal_sources, "artifact_ids")
        + (_ids(kc_cfg, "artifact_ids") if include_defaults else [])
    ))
    repo_scope_refs = (
        list(goal_sources.get("repo_scope_refs") or [])
        + (list(kc_cfg.get("repo_scope_refs") or []) if include_defaults else [])
    )
    if not collection_ids and not artifact_ids and not repo_scope_refs:
        return {}
    return {
        "knowledge_collection_ids": collection_ids,
        "artifact_ids": artifact_ids,
        "repo_scope_refs": repo_scope_refs,
    }


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
        shell_command_mode = str(rationale.get("shell_command_mode") or "").strip() or None
        task_kind = str(rationale.get("task_kind") or "").strip().lower()
        output_dir = ""
        goal_context_text = ""
        goal_rag_sources: dict = {}
        goal_mode_data: dict = {}
        goal_execution_contract: dict = {}
        if goal_id:
            try:
                goal = goal_repo.get_by_id(str(goal_id))
                if goal:
                    output_dir = str((goal.execution_preferences or {}).get("output_dir") or "").strip()
                    goal_context_text = str(goal.goal or "").strip()
                    goal_rag_sources = dict((goal.execution_preferences or {}).get("rag_sources") or {})
                    goal_mode_data = dict(goal.mode_data or {})
                    goal_execution_contract = dict((goal.execution_preferences or {}).get("goal_execution_contract") or {})
            except Exception:
                pass
        research_context_input = _merge_rag_sources(goal_rag_sources, task_kind)
        verification_spec = dict(node.verification_spec or {})

        deterministic_repair_foundation = goal_mode_data.get("deterministic_repair_foundation")
        if isinstance(deterministic_repair_foundation, dict) and deterministic_repair_foundation.get("repair_procedure"):
            extra_context = {"deterministic_repair_foundation": deterministic_repair_foundation}
        else:
            extra_context = {}

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
            **({"context": {"context_text": goal_context_text}} if goal_context_text else {}),
            **({"workspace": {"output_dir": output_dir}} if output_dir else {}),
            **({"shell_command_mode": shell_command_mode} if shell_command_mode else {}),
            **({"research_context_input": research_context_input} if research_context_input else {}),
            **extra_context,
        }
        worker_execution_contract = get_goal_execution_contract_service().task_scoped_contract(
            goal_contract=goal_execution_contract,
            plan_id=plan_id,
            plan_node_id=node.id,
            expected_artifacts=list(verification_spec.get("expected_artifacts") or []),
        )
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
                "verification_spec": verification_spec,
                "expected_artifacts": list(verification_spec.get("expected_artifacts") or []),
                "worker_execution_context": worker_execution_context,
                "worker_execution_contract": worker_execution_contract,
                "status_reason_details": {
                    "materialized_from_plan": True,
                    "planning_provenance": {
                        "plan_id": plan_id,
                        "plan_node_id": node.id,
                        **blueprint_provenance,
                    },
                    "artifact_traceability": {
                        "plan_node_id": node.id,
                        "artifact_trace_id": str((rationale or {}).get("artifact_trace_id") or ""),
                        "expected_artifacts_count": len(list(verification_spec.get("expected_artifacts") or [])),
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
        normalized_target = str(target_status or goal.status)
        goal.status = normalized_target
        goal.updated_at = time.time()
        if readiness is not None:
            goal.readiness = dict(readiness)
        if reason:
            current = dict(goal.execution_preferences or {})
            current["last_status_reason"] = str(reason)
            scoped = get_goal_config_runtime_service().get_effective_config(goal_id=str(getattr(goal, "id", "") or "").strip() or None)
            current["goal_config_source"] = str(scoped.source or "global_fallback")
            goal.execution_preferences = current
        if normalized_target in {"failed", "completed"} and str(getattr(goal, "id", "") or "").strip():
            goal_id = str(goal.id)
            for task in task_repo.get_all():
                if str(getattr(task, "goal_id", "") or "").strip() != goal_id:
                    continue
                task_status = str(getattr(task, "status", "") or "").strip().lower()
                if task_status in {"completed", "failed"}:
                    continue
                update_local_task_status(
                    str(task.id),
                    "failed",
                    error=f"goal_terminal:{normalized_target}",
                    event_type="goal_terminal_task_sweep",
                    event_actor="goal_lifecycle_service",
                    event_details={"goal_id": goal_id, "target_status": normalized_target},
                )
        return goal_repo.save(goal)


    def recover_stalled_planning_goal(self, goal: GoalDB) -> GoalDB:
        """Re-triggers planning for a goal stuck in 'planning' with no tasks.

        Idempotent: capped at 2 attempts with a 60s cooldown.
        """
        status = str(getattr(goal, "status", "") or "").strip().lower()
        if status not in {"planning", "planning_queued", "planning_running"}:
            return goal
        goal_id = str(getattr(goal, "id", "") or "").strip()
        if not goal_id:
            return goal
        now_ts = time.time()
        updated_at = float(getattr(goal, "updated_at", 0.0) or 0.0)
        if updated_at and (now_ts - updated_at) < 30:
            return goal
        tasks = [t for t in task_repo.get_all() if str(getattr(t, "goal_id", "") or "").strip() == goal_id]
        if tasks:
            return goal
        execution_preferences = dict(getattr(goal, "execution_preferences", None) or {})
        recovery = dict(execution_preferences.get("planning_recovery") or {})
        attempts = int(recovery.get("attempts") or 0)
        last_attempt_at = float(recovery.get("last_attempt_at") or 0.0)
        if attempts >= 2:
            return goal
        if last_attempt_at and (now_ts - last_attempt_at) < 60:
            return goal
        recovery.update({"attempts": attempts + 1, "last_attempt_at": now_ts, "last_reason": "stalled_planning_no_tasks"})
        execution_preferences["planning_recovery"] = recovery
        goal.execution_preferences = execution_preferences
        goal = goal_repo.save(goal)
        try:
            effective = dict(getattr(goal, "workflow_effective", None) or {})
            from agent.routes.tasks.auto_planner import auto_planner
            result = auto_planner.plan_goal(
                goal=str(getattr(goal, "goal", "") or ""),
                context=str(getattr(goal, "context", "") or "") or None,
                team_id=effective.get("routing", {}).get("team_id"),
                parent_task_id=None,
                create_tasks=bool(effective.get("planning", {}).get("create_tasks", True)),
                use_template=bool(effective.get("planning", {}).get("use_template", True)),
                use_repo_context=bool(effective.get("planning", {}).get("use_repo_context", True)),
                goal_id=goal.id,
                goal_trace_id=str(getattr(goal, "trace_id", "") or ""),
                mode=str(getattr(goal, "mode", "") or "generic"),
                mode_data=dict(getattr(goal, "mode_data", None) or {}),
            )
            if result.get("error"):
                recovery.update({"last_error": str(result.get("error"))[:240]})
                execution_preferences["planning_recovery"] = recovery
                goal.execution_preferences = execution_preferences
                goal = goal_repo.save(goal)
                if int(recovery.get("attempts") or 0) >= 2:
                    return self.transition_goal(goal, target_status="failed", reason=str(result.get("error") or "planning_failed"))
                return self.transition_goal(goal, target_status="planning", reason="planning_recovery_retry_scheduled")
            created_task_ids = list(result.get("created_task_ids") or [])
            if not created_task_ids:
                recovery.update({"last_error": "planning_recovery_no_tasks_created"})
                execution_preferences["planning_recovery"] = recovery
                goal.execution_preferences = execution_preferences
                goal = goal_repo.save(goal)
                if int(recovery.get("attempts") or 0) >= 2:
                    return self.transition_goal(goal, target_status="failed", reason="planning_recovery_no_tasks_created")
                return self.transition_goal(goal, target_status="planning", reason="planning_recovery_retry_scheduled")
            return self.transition_goal(goal, target_status="planned", reason="planning_recovery_completed")
        except Exception as exc:
            recovery.update({"last_error": str(exc)[:240]})
            execution_preferences["planning_recovery"] = recovery
            goal.execution_preferences = execution_preferences
            return goal_repo.save(goal)


task_lifecycle_service = TaskLifecycleService()
goal_lifecycle_service = GoalLifecycleService()


def get_task_lifecycle_service() -> TaskLifecycleService:
    return task_lifecycle_service


def get_goal_lifecycle_service() -> GoalLifecycleService:
    return goal_lifecycle_service
