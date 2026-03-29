import copy
from typing import Any

from flask import current_app

from agent.config import settings
from agent.db_models import GoalDB
from agent.services.planning_service import get_goal_feature_flags, get_planning_service
from agent.services.planning_utils import GOAL_TEMPLATES
from agent.services.repository_registry import get_repository_registry


class GoalService:
    def deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(base)
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self.deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def flatten_dict(self, data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for key, value in (data or {}).items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                flat.update(self.flatten_dict(value, path))
            else:
                flat[path] = value
        return flat

    def build_provenance(self, defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, str]:
        provenance = {key: "default" for key in self.flatten_dict(defaults)}
        for key in self.flatten_dict(overrides):
            provenance[key] = "override"
        return provenance

    def default_workflow_config(self) -> dict[str, Any]:
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        planning_defaults = {
            "engine": "auto_planner",
            "create_tasks": True,
            "use_template": True,
            "use_repo_context": True,
            "max_subtasks_per_goal": 8,
        }
        routing_defaults = {
            "mode": "active_team_or_hub_default",
            "team_id": None,
            "worker_selection": "current_assignment_flow",
        }
        verification_defaults = {
            "mode": "existing_quality_gates",
            "enabled": bool((agent_cfg.get("quality_gates") or {}).get("enabled", True)),
        }
        artifact_defaults = {
            "result_view": "task_summary",
            "include_task_tree": True,
        }
        policy_defaults = {
            "mode": "hub_enforced",
            "security_level": "safe_defaults",
        }
        return {
            "planning": planning_defaults,
            "routing": routing_defaults,
            "verification": verification_defaults,
            "artifacts": artifact_defaults,
            "policy": policy_defaults,
        }

    def build_goal_workflow_overrides(self, payload: Any) -> dict[str, Any]:
        overrides = copy.deepcopy(payload.workflow or {})
        if payload.team_id:
            overrides.setdefault("routing", {})["team_id"] = payload.team_id
        if payload.create_tasks is not None:
            overrides.setdefault("planning", {})["create_tasks"] = bool(payload.create_tasks)
        if payload.use_template is not None:
            overrides.setdefault("planning", {})["use_template"] = bool(payload.use_template)
        if payload.use_repo_context is not None:
            overrides.setdefault("planning", {})["use_repo_context"] = bool(payload.use_repo_context)
        return overrides

    def goal_readiness(self) -> dict[str, Any]:
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        llm_cfg = agent_cfg.get("llm_config", {}) or {}
        repos = get_repository_registry()
        workers = repos.agent_repo.get_all()
        active_team = next((team for team in repos.team_repo.get_all() if team.is_active), None)
        local_worker_available = bool(settings.role == "worker" or getattr(settings, "hub_can_be_worker", False))
        worker_available = bool(workers) or local_worker_available
        planning_provider_available = bool(str(llm_cfg.get("provider") or "").strip())
        planning_template_available = bool(GOAL_TEMPLATES)
        planning_available = bool(planning_provider_available or planning_template_available)
        degraded_hints: list[str] = []

        if not workers and local_worker_available:
            degraded_hints.append("no_remote_workers_registered_using_local_worker_fallback")
        if not active_team:
            degraded_hints.append("no_active_team_default_routing_will_use_existing_assignment_flow")
        if not planning_provider_available:
            degraded_hints.append("llm_provider_not_configured_template_planning_only")

        return {
            "happy_path_ready": bool(worker_available and planning_available),
            "planning_available": planning_available,
            "planning_provider_available": planning_provider_available,
            "planning_template_available": planning_template_available,
            "worker_available": worker_available,
            "active_team_id": active_team.id if active_team else None,
            "available_worker_count": len(workers),
            "degraded_hints": degraded_hints,
            "defaults": self.default_workflow_config(),
            "feature_flags": get_goal_feature_flags(),
        }

    def enforce_goal_preconditions(
        self,
        *,
        payload: Any,
        effective_workflow: dict[str, Any],
        readiness: dict[str, Any],
        is_admin: bool,
    ) -> str | None:
        planning_cfg = dict(effective_workflow.get("planning") or {})
        use_template = bool(planning_cfg.get("use_template", True))
        create_tasks = bool(planning_cfg.get("create_tasks", True))
        if create_tasks and not use_template and not bool(readiness.get("planning_provider_available")):
            return "planning_backend_unavailable"

        requested_policy_override = bool((payload.workflow or {}).get("policy"))
        if requested_policy_override and not is_admin:
            return "policy_override_requires_admin"
        return None

    def serialize_goal(self, goal: GoalDB) -> dict[str, Any]:
        repos = get_repository_registry()
        data = goal.model_dump()
        data["task_count"] = len(repos.task_repo.get_by_goal_id(goal.id))
        return data

    def team_scope_allows(self, goal: GoalDB, user_payload: dict[str, Any] | None, is_admin: bool) -> bool:
        if not goal.team_id or is_admin:
            return True
        user_payload = user_payload or {}
        return bool(user_payload.get("team_id")) and str(user_payload.get("team_id")) == str(goal.team_id)

    def can_access_goal(self, goal: GoalDB | None, user_payload: dict[str, Any] | None, is_admin: bool) -> bool:
        if not goal:
            return False
        return self.team_scope_allows(goal, user_payload, is_admin)

    def sanitize_governance_summary(self, summary: dict[str, Any], is_admin: bool) -> dict[str, Any]:
        if is_admin:
            return summary
        return {
            "goal_id": summary.get("goal_id"),
            "trace_id": summary.get("trace_id"),
            "policy": {
                "total": (summary.get("policy") or {}).get("total", 0),
                "approved": (summary.get("policy") or {}).get("approved", 0),
                "blocked": (summary.get("policy") or {}).get("blocked", 0),
            },
            "verification": {
                "total": (summary.get("verification") or {}).get("total", 0),
                "passed": (summary.get("verification") or {}).get("passed", 0),
                "failed": (summary.get("verification") or {}).get("failed", 0),
                "escalated": (summary.get("verification") or {}).get("escalated", 0),
            },
            "summary": {
                **dict(summary.get("summary") or {}),
                "governance_visible": False,
                "detail_level": "restricted",
            },
        }

    def build_artifact_summary(self, goal: GoalDB) -> dict[str, Any]:
        repos = get_repository_registry()
        tasks = repos.task_repo.get_by_goal_id(goal.id)
        verification_records = repos.verification_record_repo.get_by_goal_id(goal.id)
        memory_entries = repos.memory_entry_repo.get_by_goal(goal.id)
        task_outputs = [
            {
                "task_id": task.id,
                "title": task.title,
                "status": task.status,
                "plan_node_id": task.plan_node_id,
                "preview": str(task.last_output or "")[:280],
                "trace_id": task.goal_trace_id,
            }
            for task in tasks
            if task.last_output
        ]
        latest_output = next((item for item in task_outputs if item.get("preview")), None)
        return {
            "goal_id": goal.id,
            "trace_id": goal.trace_id,
            "result_summary": {
                "status": goal.status,
                "task_count": len(tasks),
                "completed_tasks": len([task for task in tasks if task.status == "completed"]),
                "failed_tasks": len([task for task in tasks if task.status == "failed"]),
                "verification_passed": len([record for record in verification_records if record.status == "passed"]),
                "memory_entries": len(memory_entries),
            },
            "headline_artifact": latest_output,
            "artifacts": task_outputs[:10],
            "memory_entries": [
                {
                    "id": entry.id,
                    "task_id": entry.task_id,
                    "title": entry.title,
                    "summary": entry.summary,
                    "trace_id": entry.trace_id,
                    "retrieval_tags": list(entry.retrieval_tags or []),
                }
                for entry in memory_entries[:10]
            ],
        }

    def goal_detail(self, goal: GoalDB, *, is_admin: bool) -> dict[str, Any]:
        repos = get_repository_registry()
        plan, nodes = get_planning_service().get_latest_plan_for_goal(goal.id)
        tasks = repos.task_repo.get_by_goal_id(goal.id)
        from agent.services.verification_service import get_verification_service

        governance = get_verification_service().governance_summary(goal.id, include_sensitive=is_admin)
        memory_entries = repos.memory_entry_repo.get_by_goal(goal.id)
        return {
            "goal": self.serialize_goal(goal),
            "trace": {
                "trace_id": goal.trace_id,
                "goal_id": goal.id,
                "plan_id": plan.id if plan else None,
                "task_ids": [task.id for task in tasks],
            },
            "artifacts": self.build_artifact_summary(goal),
            "plan": {
                "plan": plan.model_dump() if plan else None,
                "nodes": [node.model_dump() for node in nodes],
            },
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "priority": task.priority,
                    "plan_node_id": task.plan_node_id,
                    "verification_status": dict(task.verification_status or {}),
                    "trace_id": task.goal_trace_id,
                }
                for task in tasks
            ],
            "governance": self.sanitize_governance_summary(governance, is_admin) if governance else None,
            "memory": [
                {
                    "id": entry.id,
                    "task_id": entry.task_id,
                    "title": entry.title,
                    "summary": entry.summary,
                    "content": entry.content if is_admin else None,
                    "artifact_refs": list(entry.artifact_refs or []),
                    "retrieval_tags": list(entry.retrieval_tags or []),
                    "trace_id": entry.trace_id,
                }
                for entry in memory_entries
            ],
        }


goal_service = GoalService()


def get_goal_service() -> GoalService:
    return goal_service
