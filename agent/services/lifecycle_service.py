from __future__ import annotations

import time
from typing import Any

from agent.db_models import GoalDB
from agent.repository import goal_repo
from agent.services.goal_execution_contract_service import get_goal_execution_contract_service
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
