import json
import time
import uuid
from typing import Any, Optional

from flask import current_app

from agent.db_models import ConfigDB, PlanDB, PlanNodeDB
from agent.repository import config_repo, plan_node_repo, plan_repo
from agent.routes.tasks.dependency_policy import normalize_depends_on, validate_dependencies_and_cycles
from agent.routes.tasks.utils import _update_local_task_status
from agent.services.verification_service import default_verification_spec

PLAN_FEATURE_FLAGS_KEY = "goal_workflow_feature_flags"


def get_goal_feature_flags() -> dict[str, bool]:
    # Defaults are taken from agent settings when available, otherwise fallback to True
    try:
        from agent.config import settings as _settings
        defaults = {
            "goal_workflow_enabled": bool(getattr(_settings, "goal_workflow_enabled", True)),
            "persisted_plans_enabled": bool(getattr(_settings, "persisted_plans_enabled", True)),
        }
    except Exception:
        defaults = {
            "goal_workflow_enabled": True,
            "persisted_plans_enabled": True,
        }
    stored = config_repo.get_by_key(PLAN_FEATURE_FLAGS_KEY)
    # Debug: log settings and stored flags to help tests diagnose
    try:
        import logging

        logging.getLogger("agent.services.planning_service").debug(
            f"get_goal_feature_flags: defaults={defaults}, stored={stored.value_json if stored else None}"
        )
    except Exception:
        pass

    if not stored:
        return defaults
    try:
        payload = json.loads(stored.value_json or "{}")
        if isinstance(payload, dict):
            merged = {**defaults, **{k: bool(v) for k, v in payload.items()}}
            try:
                import logging

                logging.getLogger("agent.services.planning_service").debug(f"merged feature flags: {merged}")
            except Exception:
                pass
            return merged
    except Exception:
        pass
    return defaults


def set_goal_feature_flags(flags: dict[str, Any]) -> dict[str, bool]:
    merged = {**get_goal_feature_flags(), **{k: bool(v) for k, v in (flags or {}).items()}}
    config_repo.save(ConfigDB(key=PLAN_FEATURE_FLAGS_KEY, value_json=json.dumps(merged)))
    return merged


def get_plan_generation_limits() -> dict[str, int]:
    config = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("goal_plan_limits", {}) or {}
    max_nodes = max(1, min(int(config.get("max_nodes") or 8), 50))
    max_depth = max(1, min(int(config.get("max_depth") or max_nodes), max_nodes))
    return {"max_nodes": max_nodes, "max_depth": max_depth}


class PlanningService:
    def _apply_plan_generation_limits(self, subtasks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
        limits = get_plan_generation_limits()
        bounded_count = min(len(subtasks), limits["max_nodes"], limits["max_depth"])
        bounded = list(subtasks[:bounded_count])
        for subtask in bounded:
            depends_on = []
            for dep in list(subtask.get("depends_on") or []):
                dep_text = str(dep).strip()
                if not dep_text:
                    continue
                if dep_text.isdigit() and int(dep_text) <= bounded_count:
                    depends_on.append(dep_text)
                elif dep_text in {f"{index}" for index in range(1, bounded_count + 1)}:
                    depends_on.append(dep_text)
            if depends_on:
                subtask["depends_on"] = depends_on
        return bounded, limits

    def _resolve_subtasks(
        self,
        planner,
        goal: str,
        context: Optional[str],
        use_template: bool,
        use_repo_context: bool,
    ) -> dict[str, Any]:
        from agent.routes.tasks.auto_planner import (
            _build_planning_prompt,
            _match_goal_template,
            _parse_subtasks_from_llm_response,
            _try_load_repo_context,
        )

        resolved_context = context
        template_used = False
        raw_response = None

        if use_template:
            template_subtasks = _match_goal_template(goal)
            if template_subtasks:
                return {
                    "subtasks": template_subtasks[: planner.max_subtasks_per_goal],
                    "raw_response": raw_response,
                    "context": resolved_context,
                    "template_used": True,
                    "planning_mode": "template",
                }

        if use_repo_context and not resolved_context:
            repo_context = _try_load_repo_context(goal)
            if repo_context:
                resolved_context = repo_context

        prompt = _build_planning_prompt(goal, resolved_context, planner.max_subtasks_per_goal)
        llm_config = current_app.config.get("AGENT_CONFIG", {}).get("llm_config", {})
        raw_response = planner._call_llm_with_retry(prompt, llm_config)
        subtasks = _parse_subtasks_from_llm_response(raw_response, default_priority=planner.default_priority)
        return {
            "subtasks": subtasks,
            "raw_response": raw_response,
            "context": resolved_context,
            "template_used": template_used,
            "planning_mode": "llm",
        }

    def _build_nodes(self, plan_id: str, subtasks: list[dict], planning_mode: str) -> list[PlanNodeDB]:
        nodes: list[PlanNodeDB] = []
        node_keys: list[str] = []

        for index, subtask in enumerate(subtasks, start=1):
            node_key = f"{plan_id}-node-{index}"
            node_keys.append(node_key)
            raw_depends_on = list(subtask.get("depends_on") or [])
            depends_on: list[str] = []
            if raw_depends_on:
                for dep in raw_depends_on:
                    dep_text = str(dep).strip()
                    if dep_text in node_keys:
                        depends_on.append(dep_text)
                    elif dep_text.isdigit():
                        dep_index = int(dep_text) - 1
                        if 0 <= dep_index < len(node_keys):
                            depends_on.append(node_keys[dep_index])
            elif index > 1:
                depends_on.append(node_keys[index - 2])

            nodes.append(
                PlanNodeDB(
                    plan_id=plan_id,
                    node_key=node_key,
                    title=str(subtask.get("title") or f"Step {index}")[:200],
                    description=str(subtask.get("description") or subtask.get("title") or "")[:2000],
                    priority=str(subtask.get("priority") or "Medium"),
                    position=index,
                    depends_on=depends_on,
                    rationale={
                        "planning_mode": planning_mode,
                        "task_kind": planning_mode,
                        "source_depends_on": raw_depends_on,
                    },
                    verification_spec=default_verification_spec(
                        {"task_kind": planning_mode, "title": subtask.get("title"), "description": subtask.get("description")}
                    ),
                )
            )
        return nodes

    def _persist_plan(
        self,
        goal_id: str,
        trace_id: str,
        subtasks: list[dict],
        planning_mode: str,
        raw_response: Optional[str],
        context: Optional[str],
    ) -> tuple[PlanDB | None, list[PlanNodeDB]]:
        flags = get_goal_feature_flags()
        if not flags.get("persisted_plans_enabled", True):
            return None, []

        plan = PlanDB(
            goal_id=goal_id,
            trace_id=trace_id,
            status="draft",
            planning_mode=planning_mode,
            rationale={
                "planning_mode": planning_mode,
                "node_count": len(subtasks),
                "context_used": bool(context),
                "raw_response_preview": (raw_response or "")[:400],
            },
        )
        plan = plan_repo.save(plan)
        plan_node_repo.delete_by_plan_id(plan.id)
        nodes = self._build_nodes(plan.id, subtasks, planning_mode)
        for node in nodes:
            plan_node_repo.save(node)
        return plan, plan_node_repo.get_by_plan_id(plan.id)

    def _materialize_plan(
        self,
        planner,
        plan: PlanDB | None,
        nodes: list[PlanNodeDB],
        team_id: Optional[str],
        parent_task_id: Optional[str],
        goal_id: Optional[str],
        goal_trace_id: Optional[str],
    ) -> list[str]:
        created_ids: list[str] = []
        node_to_task_id: dict[str, str] = {}

        for node in nodes:
            task_id = f"goal-{uuid.uuid4().hex[:8]}"
            task_depends_on = []
            if node.depends_on:
                mapped = [node_to_task_id.get(dep) for dep in node.depends_on]
                task_depends_on = [dep for dep in mapped if dep]
            elif created_ids:
                task_depends_on = created_ids[-1:]

            task_depends_on = normalize_depends_on(task_depends_on, task_id)
            ok, reason = validate_dependencies_and_cycles(task_id, task_depends_on)
            if not ok:
                current_app.logger.warning("Skipping materialized task with invalid deps: %s", reason)
                continue

            _update_local_task_status(
                task_id,
                "todo",
                title=node.title,
                description=node.description,
                priority=node.priority,
                team_id=team_id,
                goal_id=goal_id,
                goal_trace_id=goal_trace_id,
                plan_id=plan.id if plan else None,
                plan_node_id=node.id,
                task_kind=(node.rationale or {}).get("task_kind"),
                verification_spec=dict(node.verification_spec or {}),
                parent_task_id=parent_task_id,
                source_task_id=parent_task_id,
                derivation_reason=f"goal_{plan.planning_mode if plan else 'planning'}",
                derivation_depth=1 if parent_task_id else 0,
                depends_on=task_depends_on if task_depends_on else None,
            )
            created_ids.append(task_id)
            node_to_task_id[node.node_key] = task_id
            node.materialized_task_id = task_id
            node.status = "materialized"
            node.updated_at = time.time()
            plan_node_repo.save(node)
            planner._stats["tasks_created"] += 1

        if plan:
            plan.status = "materialized" if created_ids else "draft"
            plan.updated_at = time.time()
            plan_repo.save(plan)
        return created_ids

    def plan_goal(
        self,
        planner,
        goal: str,
        context: Optional[str] = None,
        team_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        create_tasks: bool = True,
        use_template: bool = True,
        use_repo_context: bool = True,
        goal_id: Optional[str] = None,
        goal_trace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        flags = get_goal_feature_flags()
        if not flags.get("goal_workflow_enabled", True):
            return {"subtasks": [], "created_task_ids": [], "error": "goal_workflow_disabled"}

        from agent.routes.tasks.auto_planner import _validate_goal, _sanitize_input

        is_valid, error_msg = _validate_goal(goal)
        if not is_valid:
            return {"subtasks": [], "created_task_ids": [], "error": error_msg}

        goal = _sanitize_input(goal)
        context = _sanitize_input(context) if context else None

        try:
            resolved = self._resolve_subtasks(
                planner=planner,
                goal=goal,
                context=context,
                use_template=use_template,
                use_repo_context=use_repo_context,
            )
        except Exception as exc:
            planner._stats["errors"] += 1
            return {"subtasks": [], "created_task_ids": [], "error": str(exc)}

        subtasks = resolved["subtasks"]
        subtasks, limits = self._apply_plan_generation_limits(subtasks)
        raw_response = resolved["raw_response"]
        planning_mode = resolved["planning_mode"]
        context = resolved["context"]
        template_used = resolved["template_used"]

        if not subtasks:
            return {
                "subtasks": [],
                "created_task_ids": [],
                "raw_response": raw_response,
                "error_classification": "unstructured_llm_response",
            }

        plan = None
        nodes: list[PlanNodeDB] = []
        if goal_id and goal_trace_id:
            plan, nodes = self._persist_plan(
                goal_id=goal_id,
                trace_id=goal_trace_id,
                subtasks=subtasks,
                planning_mode=planning_mode,
                raw_response=raw_response,
                context=context,
            )

        created_ids: list[str] = []
        if create_tasks:
            materialize_nodes = nodes or self._build_nodes(goal_id or "ad-hoc-plan", subtasks, planning_mode)
            created_ids = self._materialize_plan(
                planner=planner,
                plan=plan,
                nodes=materialize_nodes,
                team_id=team_id,
                parent_task_id=parent_task_id,
                goal_id=goal_id,
                goal_trace_id=goal_trace_id,
            )
            planner._stats["goals_processed"] += 1
            planner._persist_state()

        return {
            "subtasks": subtasks,
            "created_task_ids": created_ids,
            "raw_response": raw_response if not create_tasks else None,
            "template_used": template_used,
            "plan_id": plan.id if plan else None,
            "plan_node_ids": [node.id for node in nodes],
            "feature_flags": flags,
            "plan_limits": limits,
        }

    def get_latest_plan_for_goal(self, goal_id: str) -> tuple[PlanDB | None, list[PlanNodeDB]]:
        plans = plan_repo.get_by_goal_id(goal_id)
        if not plans:
            return None, []
        plan = plans[0]
        return plan, plan_node_repo.get_by_plan_id(plan.id)

    def patch_plan_node(self, goal_id: str, node_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        plan, _ = self.get_latest_plan_for_goal(goal_id)
        if not plan:
            return None, "plan_not_found"
        node = plan_node_repo.get_by_id(node_id)
        if not node or node.plan_id != plan.id:
            return None, "node_not_found"
        if node.materialized_task_id:
            return None, "node_already_materialized"

        allowed_fields = {"title", "description", "priority", "depends_on", "editable"}
        for key, value in (payload or {}).items():
            if key not in allowed_fields:
                continue
            if key == "depends_on":
                setattr(node, key, [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else [])
            else:
                setattr(node, key, value)
        node.updated_at = time.time()
        node.status = "edited"
        node.rationale = {**(node.rationale or {}), "edited": True}
        plan_node_repo.save(node)
        plan.updated_at = time.time()
        plan_repo.save(plan)
        return node.model_dump(), None


planning_service = PlanningService()


def get_planning_service() -> PlanningService:
    return planning_service
