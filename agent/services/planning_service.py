import json
import logging
import time
import uuid
from typing import Any, Optional

from flask import current_app

from agent.db_models import ConfigDB, PlanDB, PlanNodeDB
from agent.routes.tasks.dependency_policy import normalize_depends_on, validate_dependency_graph
from agent.services.lifecycle_service import get_task_lifecycle_service
from agent.services.planning_strategies import (
    HubCopilotPlanningStrategy,
    LLMPlanningStrategy,
    PlanningStrategyResult,
    TemplatePlanningStrategy,
)
from agent.services.planning_utils import sanitize_input, validate_goal
from agent.services.planning_evaluation_service import get_planning_evaluation_service
from agent.services.planning_telemetry_service import get_planning_telemetry_service
from agent.services.goal_planning_intent_service import get_goal_planning_intent_service
from agent.services.llm_first_planning_orchestrator_service import get_llm_first_planning_orchestrator_service
from agent.services.planning_template_mining_service import get_planning_template_mining_service
from agent.services.planning_review_queue_service import get_planning_review_queue_service
from agent.services.repository_registry import get_repository_registry
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.verification_policy_service import default_verification_spec
from agent.services.worker_routing_policy_utils import (
    derive_required_capabilities,
    extract_blueprint_role_defaults,
    merge_capabilities_with_blueprint_defaults,
)
from agent.services.planning_proposal_service import (
    build_plan_proposal,
    normalize_planning_policy_config,
    select_planning_agent_candidate,
    validate_plan_proposal_payload,
)

PLAN_FEATURE_FLAGS_KEY = "goal_workflow_feature_flags"


def _infer_subtask_task_kind(subtask: dict[str, Any]) -> str:
    task_like = {
        "title": str(subtask.get("title") or ""),
        "description": str(subtask.get("description") or ""),
    }
    capabilities = derive_required_capabilities(task_like)
    for kind in ("testing", "review", "planning", "research", "coding"):
        if kind in capabilities:
            return kind
    return "coding"


def _retrieval_hints_for_task_kind(task_kind: str | None) -> dict[str, str]:
    normalized = str(task_kind or "").strip().lower()
    if normalized in {"bugfix", "testing", "test"}:
        return {
            "retrieval_intent": "localize_failure_and_fix",
            "required_context_scope": "local_code_and_failure_neighbors",
            "preferred_bundle_mode": "standard",
        }
    if normalized in {"refactor", "implement", "coding"}:
        return {
            "retrieval_intent": "symbol_and_dependency_neighborhood",
            "required_context_scope": "module_and_related_symbols",
            "preferred_bundle_mode": "standard",
        }
    if normalized in {"architecture", "analysis", "doc", "research"}:
        return {
            "retrieval_intent": "architecture_and_decision_context",
            "required_context_scope": "cross_module_docs_and_contracts",
            "preferred_bundle_mode": "full",
        }
    if normalized in {"config", "xml", "ops"}:
        return {
            "retrieval_intent": "configuration_contracts_and_runtime_edges",
            "required_context_scope": "config_and_integration_points",
            "preferred_bundle_mode": "standard",
        }
    return {
        "retrieval_intent": "execution_focused_context",
        "required_context_scope": "task_and_direct_neighbors",
        "preferred_bundle_mode": "standard",
    }


def _sanitize_blueprint_provenance(subtask: dict[str, Any]) -> dict[str, str]:
    role_hints = list(subtask.get("blueprint_role_template_hints") or [])
    primary_hint = role_hints[0] if role_hints and isinstance(role_hints[0], dict) else {}
    blueprint_role_name = (
        str(subtask.get("blueprint_role_name") or "").strip()
        or str(primary_hint.get("role_name") or "").strip()
    )
    template_name = (
        str(subtask.get("template_name") or "").strip()
        or str(primary_hint.get("template_name") or "").strip()
        or str(primary_hint.get("template_id") or "").strip()
    )
    provenance = {
        "blueprint_id": str(subtask.get("blueprint_id") or "").strip(),
        "blueprint_name": str(subtask.get("blueprint_name") or "").strip(),
        "blueprint_artifact_id": str(subtask.get("blueprint_artifact_id") or "").strip(),
        "blueprint_role_name": blueprint_role_name,
        "template_name": template_name,
        "template_id": str(subtask.get("template_id") or "").strip(),
    }
    return {key: value for key, value in provenance.items() if value}


def _sanitize_role_defaults(subtask: dict[str, Any]) -> dict[str, Any]:
    explicit_defaults = extract_blueprint_role_defaults(subtask)
    if explicit_defaults:
        return explicit_defaults

    role_hints = list(subtask.get("blueprint_role_template_hints") or [])
    if not role_hints or not isinstance(role_hints[0], dict):
        return {}
    hint = dict(role_hints[0])
    return extract_blueprint_role_defaults(
        {
            "blueprint_role_defaults": {
                "capability_defaults": hint.get("capability_defaults"),
                "risk_profile": hint.get("risk_profile"),
                "verification_defaults": hint.get("verification_defaults"),
            }
        }
    )


_ALLOWED_CAPS_BY_KIND: dict[str, set[str]] = {
    "coding": {"coding", "analysis", "doc"},
    "testing": {"testing", "analysis", "doc"},
    "review": {"review", "analysis", "doc"},
    "research": {"research", "analysis", "doc"},
    "planning": {"planning", "analysis", "doc"},
    "ops": {"ops", "analysis", "doc"},
    "analysis": {"analysis", "doc"},
    "doc": {"doc", "analysis"},
}


def _sanitize_llm_subtask_policy_hints(subtask: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Apply deterministic safety gates to LLM-suggested policy hints.

    LLM output may suggest capabilities/context/tooling, but cannot expand policy.
    """
    out = dict(subtask or {})
    warnings: list[str] = []
    task_kind = str(out.get("task_kind") or "").strip().lower() or _infer_subtask_task_kind(out)
    allowed_caps = _ALLOWED_CAPS_BY_KIND.get(task_kind, {"analysis", "doc"})
    requested_caps = [str(item).strip().lower() for item in list(out.get("required_capabilities") or []) if str(item).strip()]
    filtered_caps = [cap for cap in requested_caps if cap in allowed_caps]
    if requested_caps and len(filtered_caps) != len(requested_caps):
        warnings.append("capability_escalation_blocked")
    out["required_capabilities"] = filtered_caps

    requested_scope = str(out.get("context_scope") or "").strip().lower()
    if requested_scope in {"full", "global", "admin"}:
        warnings.append("context_scope_escalation_blocked")
        out.pop("context_scope", None)

    if "tool_permissions" in out or "allowed_tools" in out:
        warnings.append("tool_escalation_blocked")
        out.pop("tool_permissions", None)
        out.pop("allowed_tools", None)
    return out, warnings


def _merge_verification_defaults(
    base_verification_spec: dict[str, Any],
    role_defaults: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base_verification_spec or {})
    if not role_defaults:
        return merged

    merged["blueprint_role_defaults"] = dict(role_defaults)
    verification_defaults = role_defaults.get("verification_defaults")
    if not isinstance(verification_defaults, dict):
        return merged

    if bool(verification_defaults.get("required")):
        merged["required"] = True
    if bool(verification_defaults.get("policy")):
        merged["policy"] = True

    gates: list[str] = []
    for item in list(verification_defaults.get("gates") or []):
        gate = str(item).strip()
        if gate and gate not in gates:
            gates.append(gate)
    if gates:
        existing_gates = [
            str(item).strip()
            for item in list(merged.get("required_gates") or [])
            if str(item).strip()
        ]
        for gate in gates:
            if gate not in existing_gates:
                existing_gates.append(gate)
        merged["required_gates"] = existing_gates
    return merged


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
    stored = get_repository_registry().config_repo.get_by_key(PLAN_FEATURE_FLAGS_KEY)
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
    get_repository_registry().config_repo.save(ConfigDB(key=PLAN_FEATURE_FLAGS_KEY, value_json=json.dumps(merged)))
    return merged


def get_plan_generation_limits() -> dict[str, int]:
    config = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("goal_plan_limits", {}) or {}

    def _safe_int(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except Exception:
            return int(fallback)

    raw_max_nodes = config.get("max_plan_nodes")
    if raw_max_nodes is None:
        raw_max_nodes = config.get("max_nodes")
    max_nodes = max(1, min(_safe_int(raw_max_nodes, 8), 50))

    raw_max_depth = config.get("max_plan_depth")
    if raw_max_depth is None:
        raw_max_depth = config.get("max_depth")
    max_depth = max(1, min(_safe_int(raw_max_depth, max_nodes), max_nodes))

    return {
        "max_plan_nodes": max_nodes,
        "max_plan_depth": max_depth,
        # Legacy aliases for backward compatibility.
        "max_nodes": max_nodes,
        "max_depth": max_depth,
    }


class PlanningService:
    def _resolve_planning_policy(self) -> dict[str, Any]:
        try:
            cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        except Exception:
            cfg = {}
        raw = cfg.get("planning_policy") if isinstance(cfg.get("planning_policy"), dict) else {}
        return normalize_planning_policy_config(raw)

    @staticmethod
    def _build_minimal_non_llm_fallback_subtask(*, goal: str, mode: str = "generic") -> dict[str, Any]:
        goal_preview = str(goal or "").strip()[:160] or "Goal"
        title = "Create initial executable project skeleton"
        if mode == "new_software_project":
            title = "Create project skeleton and first runnable check"
        return {
            "title": title,
            "description": (
                f"Initialize a minimal executable baseline for goal: {goal_preview}. "
                "Create at least one concrete project artifact and one basic verification/check command."
            ),
            "priority": "High",
            "task_kind": "coding",
            "depends_on": [],
            "fallback_origin": "non_llm_minimal_task",
        }

    def _compute_plan_depth(self, probe_nodes: list[PlanNodeDB]) -> int:
        depth_by_key: dict[str, int] = {}
        max_depth = 0
        for node in probe_nodes:
            if not node.depends_on:
                depth = 1
            else:
                depth = 1 + max(depth_by_key.get(dep, 1) for dep in node.depends_on)
            depth_by_key[node.node_key] = depth
            if depth > max_depth:
                max_depth = depth
        return max_depth

    def _apply_plan_generation_limits(
        self, subtasks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, int], str | None]:
        limits = get_plan_generation_limits()
        bounded = [dict(subtask or {}) for subtask in (subtasks or [])]
        node_count = len(bounded)
        for subtask in bounded:
            raw_deps = list(subtask.get("depends_on") or [])
            raw_mode = str(subtask.get("dependency_mode") or "").strip().lower()
            if raw_mode not in {"parallel", "explicit", "sequential"}:
                raw_mode = "explicit" if raw_deps else "sequential"
            if "__parallel__" in raw_deps:
                subtask["dependency_mode"] = "parallel"
                subtask["depends_on"] = []
                continue
            depends_on = []
            for dep in raw_deps:
                dep_text = str(dep).strip()
                if not dep_text:
                    continue
                if dep_text.isdigit() and int(dep_text) <= node_count:
                    depends_on.append(dep_text)
                elif dep_text in {f"{index}" for index in range(1, node_count + 1)}:
                    depends_on.append(dep_text)
            if depends_on:
                subtask["depends_on"] = depends_on
                subtask["dependency_mode"] = "explicit"
            elif "depends_on" in subtask:
                subtask.pop("depends_on", None)
                subtask["dependency_mode"] = "parallel" if raw_mode == "parallel" else "sequential"

        limits = {
            **limits,
            "observed_plan_nodes": node_count,
        }
        if node_count > limits["max_plan_nodes"]:
            return bounded[: limits["max_plan_nodes"]], {**limits, "truncated": True}, "max_plan_nodes"

        observed_depth = self._compute_plan_depth(self._build_nodes("plan-limit-probe", bounded, "limit_probe"))
        limits = {**limits, "observed_plan_depth": observed_depth}
        if observed_depth > limits["max_plan_depth"]:
            return bounded, limits, "max_plan_depth"
        return bounded, limits, None

    def _resolve_subtasks(
        self,
        planner,
        goal: str,
        context: Optional[str],
        use_template: bool,
        use_repo_context: bool,
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> dict[str, Any]:
        result = self._run_planning_strategies(
            planner=planner,
            goal=goal,
            context=context,
            use_template=use_template,
            use_repo_context=use_repo_context,
            mode=mode,
            mode_data=mode_data,
        )
        return {
            "subtasks": result.subtasks,
            "raw_response": result.raw_response,
            "context": result.context,
            "template_used": result.template_used,
            "planning_mode": result.planning_mode,
            "planning_origin": result.planning_origin,
            "repair_strategy_used": result.repair_strategy_used,
            "repair_attempt_count": result.repair_attempt_count,
            "parse_mode": result.parse_mode,
            "parse_confidence": result.parse_confidence,
            "output_shape": result.output_shape,
            "format_error_codes": result.format_error_codes or [],
            "parser_trace": result.parser_trace or [],
            "prompt_version_id": result.prompt_version_id,
            "planning_profile": result.planning_profile,
        }

    def _run_planning_strategies(
        self,
        *,
        planner,
        goal: str,
        context: Optional[str],
        use_template: bool,
        use_repo_context: bool,
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> PlanningStrategyResult:
        planning_policy = self._resolve_planning_policy()
        decision = get_llm_first_planning_orchestrator_service().decide_strategy_order(
            mode=mode,
            use_template=use_template,
            use_repo_context=use_repo_context,
            planning_policy=planning_policy,
        )
        strategy_map = {
            "template": TemplatePlanningStrategy(enabled=use_template),
            "hub_copilot": HubCopilotPlanningStrategy(use_repo_context=use_repo_context),
            "llm": LLMPlanningStrategy(use_repo_context=use_repo_context),
        }
        strategies = [strategy_map[name] for name in decision.strategy_order if name in strategy_map]
        if not strategies:
            strategies = [LLMPlanningStrategy(use_repo_context=use_repo_context)]
        for strategy in strategies:
            result = strategy.execute(planner, goal, context, mode=mode, mode_data=mode_data)
            if result is not None:
                setattr(planner, "_planning_strategy_rationale", decision.rationale)
                return result
        raise RuntimeError("planning_strategy_resolution_failed")

    def _build_nodes(self, plan_id: str, subtasks: list[dict], planning_mode: str) -> list[PlanNodeDB]:
        nodes: list[PlanNodeDB] = []
        node_keys: list[str] = []

        for index, subtask in enumerate(subtasks, start=1):
            node_key = f"{plan_id}-node-{index}"
            node_keys.append(node_key)
            task_kind = _infer_subtask_task_kind(subtask)
            retrieval_hints = _retrieval_hints_for_task_kind(task_kind)
            blueprint_provenance = _sanitize_blueprint_provenance(subtask)
            role_defaults = _sanitize_role_defaults(subtask)
            required_capabilities = derive_required_capabilities(
                {
                    "title": str(subtask.get("title") or ""),
                    "description": str(subtask.get("description") or ""),
                    "task_kind": task_kind,
                },
                task_kind,
            )
            required_capabilities = merge_capabilities_with_blueprint_defaults(
                required_capabilities,
                {"blueprint_role_defaults": role_defaults},
            )
            raw_depends_on = list(subtask.get("depends_on") or [])
            dependency_mode = str(subtask.get("dependency_mode") or "").strip().lower()
            if dependency_mode not in {"parallel", "explicit", "sequential"}:
                dependency_mode = "explicit" if raw_depends_on else "sequential"
            if "__parallel__" in raw_depends_on:
                dependency_mode = "parallel"
                raw_depends_on = []
            is_parallel = dependency_mode == "parallel"
            depends_on: list[str] = []
            if raw_depends_on and dependency_mode == "explicit":
                for dep in raw_depends_on:
                    dep_text = str(dep).strip()
                    if dep_text in node_keys:
                        depends_on.append(dep_text)
                    elif dep_text.isdigit():
                        dep_index = int(dep_text) - 1
                        if 0 <= dep_index < len(node_keys):
                            depends_on.append(node_keys[dep_index])
            elif dependency_mode == "sequential" and index > 1:
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
                        "task_kind": task_kind,
                        "retrieval_intent": retrieval_hints["retrieval_intent"],
                        "required_context_scope": retrieval_hints["required_context_scope"],
                        "preferred_bundle_mode": retrieval_hints["preferred_bundle_mode"],
                        "required_capabilities": required_capabilities,
                        "source_depends_on": raw_depends_on,
                        "dependency_mode": dependency_mode,
                        "artifact_trace_id": str(subtask.get("artifact_trace_id") or f"A{index}"),
                        "expected_artifacts": [dict(a) for a in list(subtask.get("expected_artifacts") or []) if isinstance(a, dict)],
                        "artifact": subtask.get("artifact"),
                        "risk_focus": subtask.get("risk_focus"),
                        "test_focus": subtask.get("test_focus"),
                        "review_focus": subtask.get("review_focus"),
                        **({"parallel": True} if is_parallel else {}),
                        **blueprint_provenance,
                        **({"blueprint_role_defaults": role_defaults} if role_defaults else {}),
                    },
                    verification_spec=_merge_verification_defaults(
                        default_verification_spec(
                            {
                                "task_kind": task_kind,
                                "title": subtask.get("title"),
                                "description": subtask.get("description"),
                            }
                        ),
                        role_defaults,
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
        planning_origin: str | None = None,
        repair_strategy_used: str | None = None,
        repair_attempt_count: int = 0,
        parse_mode: str | None = None,
        planning_run_id: str | None = None,
    ) -> tuple[PlanDB | None, list[PlanNodeDB]]:
        repos = get_repository_registry()
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
                "planning_origin": planning_origin or planning_mode,
                "repair_strategy_used": repair_strategy_used,
                "repair_attempt_count": int(repair_attempt_count or 0),
                "parse_mode": parse_mode,
                "planning_run_id": planning_run_id,
                "node_count": len(subtasks),
                "context_used": bool(context),
                "raw_response_preview": (raw_response or "")[:400],
            },
        )
        plan = repos.plan_repo.save(plan)
        repos.plan_node_repo.delete_by_plan_id(plan.id)
        nodes = self._build_nodes(plan.id, subtasks, planning_mode)
        try:
            for node in nodes:
                repos.plan_node_repo.save(node)
        except Exception as exc:
            repos.plan_node_repo.delete_by_plan_id(plan.id)
            plan.status = "failed"
            plan.rationale = {
                **(plan.rationale or {}),
                "persist_error": str(exc)[:500],
            }
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)
            logging.getLogger(__name__).warning("Plan persistence failed for %s: %s", plan.id, exc)
            return plan, []
        return plan, repos.plan_node_repo.get_by_plan_id(plan.id)

    _PIPELINE_SHELL_MODES = {"admin_repair", "runtime_repair", "docker_compose_repair"}

    def _materialize_plan(
        self,
        planner,
        plan: PlanDB | None,
        nodes: list[PlanNodeDB],
        team_id: Optional[str],
        parent_task_id: Optional[str],
        goal_id: Optional[str],
        goal_trace_id: Optional[str],
        mode: str = "generic",
    ) -> tuple[list[str], str | None]:
        repos = get_repository_registry()
        staged = self._prepare_materialization(nodes=nodes)
        if staged is None:
            if plan:
                plan.status = "failed"
                plan.rationale = {**(plan.rationale or {}), "materialization_error": "invalid_dependencies"}
                plan.updated_at = time.time()
                repos.plan_repo.save(plan)
            return [], "invalid_dependencies"

        # Inject shell_command_mode into node rationale for repair/pipeline modes
        if mode in self._PIPELINE_SHELL_MODES:
            for node in nodes:
                node.rationale = {**(node.rationale or {}), "shell_command_mode": "pipeline"}

        created_ids: list[str] = []
        try:
            for entry in staged:
                node = entry["node"]
                task_id = entry["task_id"]
                task_depends_on = entry["depends_on"]
                get_task_lifecycle_service().materialize_from_plan_node(
                    task_id=task_id,
                    node=node,
                    team_id=team_id,
                    goal_id=goal_id,
                    goal_trace_id=goal_trace_id,
                    plan_id=plan.id if plan else None,
                    parent_task_id=parent_task_id,
                    derivation_reason=f"goal_{plan.planning_mode if plan else 'planning'}",
                    derivation_depth=1 if parent_task_id else 0,
                    depends_on=task_depends_on,
                )
                created_ids.append(task_id)
                node.materialized_task_id = task_id
                node.status = "materialized"
                node.updated_at = time.time()
                repos.plan_node_repo.save(node)
                planner._stats["tasks_created"] += 1
        except Exception as exc:
            self._rollback_materialization(plan=plan, nodes=nodes, created_ids=created_ids, error=str(exc))
            return [], "materialization_failed"

        if plan:
            plan.status = "materialized" if created_ids else "draft"
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)
        return created_ids, None

    def _prepare_materialization(self, nodes: list[PlanNodeDB]) -> list[dict[str, Any]] | None:
        node_to_task_id = {node.node_key: f"goal-{uuid.uuid4().hex[:8]}" for node in nodes}
        staged: list[dict[str, Any]] = []
        created_order: list[str] = []
        staged_graph: dict[str, list[str]] = {}
        for node in nodes:
            task_id = node_to_task_id[node.node_key]
            task_depends_on = []
            is_parallel_node = bool((node.rationale or {}).get("parallel"))
            dependency_mode = str((node.rationale or {}).get("dependency_mode") or "").strip().lower()
            if node.depends_on:
                mapped = [node_to_task_id.get(dep) for dep in node.depends_on]
                task_depends_on = [dep for dep in mapped if dep]
            elif dependency_mode == "sequential" and not is_parallel_node and created_order:
                task_depends_on = created_order[-1:]
            task_depends_on = normalize_depends_on(task_depends_on, task_id)
            staged_graph[task_id] = task_depends_on
            staged.append({"node": node, "task_id": task_id, "depends_on": task_depends_on})
            created_order.append(task_id)
        ok, _reason = validate_dependency_graph(staged_graph)
        if not ok:
            return None
        return staged

    def _rollback_materialization(self, plan: PlanDB | None, nodes: list[PlanNodeDB], created_ids: list[str], error: str) -> None:
        repos = get_repository_registry()
        for task_id in created_ids:
            try:
                repos.task_repo.delete(task_id)
            except Exception:
                current_app.logger.warning("Failed to rollback task %s after materialization failure", task_id)
        for node in nodes:
            if node.materialized_task_id in created_ids:
                node.materialized_task_id = None
                node.status = "pending"
                node.updated_at = time.time()
                repos.plan_node_repo.save(node)
        if plan:
            plan.status = "failed"
            plan.rationale = {
                **(plan.rationale or {}),
                "materialization_error": error[:500],
            }
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)

    @staticmethod
    def _repair_invalid_plan_payload(payload: dict[str, Any], validation_errors: list[str]) -> dict[str, Any]:
        repaired = dict(payload or {})
        nodes = [dict(item) for item in list(repaired.get("nodes") or []) if isinstance(item, dict)]
        need_artifacts = any(str(err).startswith("missing_expected_artifacts:") for err in list(validation_errors or []))
        if need_artifacts:
            for node in nodes:
                task_kind = str(node.get("task_kind") or "").strip().lower()
                expected = [dict(a) for a in list(node.get("expected_artifacts") or []) if isinstance(a, dict)]
                if task_kind in {"coding", "testing", "ops"} and not expected:
                    expected = [{"kind": "workspace_change", "required": True, "description": f"{node.get('node_key')}-output"}]
                    node["expected_artifacts"] = expected
                verification_spec = dict(node.get("verification_spec") or {})
                if expected and not verification_spec.get("expected_artifacts"):
                    verification_spec["expected_artifacts"] = expected
                node["verification_spec"] = verification_spec
        repaired["nodes"] = nodes
        return repaired

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
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> dict[str, Any]:
        flags = get_goal_feature_flags()
        if not flags.get("goal_workflow_enabled", True):
            return {"subtasks": [], "created_task_ids": [], "error": "goal_workflow_disabled"}

        is_valid, error_msg = validate_goal(goal)
        if not is_valid:
            return {"subtasks": [], "created_task_ids": [], "error": error_msg}

        goal = sanitize_input(goal)
        context = sanitize_input(context) if context else None
        intent = get_goal_planning_intent_service().classify(goal_text=goal, mode=mode)
        scoped_resolution = get_goal_config_runtime_service().get_effective_config(goal_id=goal_id, task_id=None)
        setattr(planner, "_goal_effective_config", dict(scoped_resolution.config or {}))
        setattr(planner, "_goal_config_source", str(scoped_resolution.source or "global_fallback"))
        scoped_cfg = dict(scoped_resolution.config or {})
        scoped_llm_cfg = dict(scoped_cfg.get("llm_config") or {})
        telemetry_run = get_planning_telemetry_service().start_run(
            goal_id=goal_id,
            trace_id=goal_trace_id,
            goal_text=goal,
            mode=mode,
            mode_data={**dict(mode_data or {}), "__intent__": intent},
            provider=str(scoped_llm_cfg.get("provider") or ""),
            model_name=str(scoped_llm_cfg.get("model") or ""),
            model_base_url=str(scoped_llm_cfg.get("base_url") or ""),
            planning_profile=None,
            prompt_version_id=None,
            prompt_language=None,
            context_char_count=len(str(context or "")),
            status="started",
        )

        try:
            resolved = self._resolve_subtasks(
                planner=planner,
                goal=goal,
                context=context,
                use_template=use_template,
                use_repo_context=use_repo_context,
                mode=mode,
                mode_data=mode_data,
            )
        except Exception as exc:
            planner._stats["errors"] += 1
            get_planning_telemetry_service().update_run(
                telemetry_run,
                status="failed",
                error_classification="resolve_subtasks_exception",
                validation_errors=[str(exc)],
            )
            return {"subtasks": [], "created_task_ids": [], "error": str(exc), "planning_run_id": telemetry_run.id}

        subtasks = resolved["subtasks"]
        planning_policy = self._resolve_planning_policy()
        mode_data_dict = dict(mode_data or {})
        no_task_dependencies = bool(mode_data_dict.get("no_task_dependencies"))
        if (
            mode == "new_software_project"
            and "no_task_dependencies" not in mode_data_dict
            and bool(planning_policy.get("new_software_project_parallel_default", True))
        ):
            no_task_dependencies = True

        if no_task_dependencies:
            # Sentinel must be applied before _apply_plan_generation_limits so the depth probe
            # sees parallel nodes (depth=1) and doesn't reject the plan.
            # _apply_plan_generation_limits preserves __parallel__ unchanged.
            # _build_nodes and _prepare_materialization both check rationale["parallel"] to skip
            # the auto-sequential fallback.
            for subtask in subtasks:
                subtask["dependency_mode"] = "parallel"
                subtask["depends_on"] = []
        policy_gate_warnings: list[str] = []
        hardened_subtasks: list[dict[str, Any]] = []
        for subtask in list(subtasks or []):
            cleaned, warns = _sanitize_llm_subtask_policy_hints(subtask)
            hardened_subtasks.append(cleaned)
            policy_gate_warnings.extend(list(warns or []))
        subtasks = hardened_subtasks
        subtasks, limits, limit_exceeded = self._apply_plan_generation_limits(subtasks)
        raw_response = resolved["raw_response"]
        planning_mode = resolved["planning_mode"]
        planning_origin = str(resolved.get("planning_origin") or planning_mode)
        repair_strategy_used = resolved.get("repair_strategy_used")
        repair_attempt_count = int(resolved.get("repair_attempt_count") or 0)
        parse_mode = resolved.get("parse_mode")
        parse_confidence = resolved.get("parse_confidence")
        output_shape = str(resolved.get("output_shape") or "")
        format_error_codes = [str(x) for x in list(resolved.get("format_error_codes") or [])]
        for warning in policy_gate_warnings:
            if warning not in format_error_codes:
                format_error_codes.append(warning)
        parser_trace = list(resolved.get("parser_trace") or [])
        prompt_version_id = str(resolved.get("prompt_version_id") or "")
        planning_profile = str(resolved.get("planning_profile") or "")
        context = resolved["context"]
        template_used = resolved["template_used"]
        get_planning_telemetry_service().update_run(
            telemetry_run,
            mode_data_patch={"__output_shape__": output_shape, "__parser_trace__": parser_trace},
            raw_output=str(raw_response or ""),
            parse_mode=str(parse_mode or ""),
            parse_confidence=str(parse_confidence or "low"),
            repair_needed=bool(repair_attempt_count),
            repair_success=bool(subtasks),
            repair_strategy_used=str(repair_strategy_used or ""),
            repair_attempt_count=repair_attempt_count,
            parse_warnings=format_error_codes,
            status="resolved",
        )
        planner_selection: dict[str, Any] = {
            "delegated_planning_enabled": bool(planning_policy.get("delegated_planning_enabled")),
            "selected_agent": None,
            "selection_reason": "hub_local_planning",
        }
        if planning_policy.get("delegated_planning_enabled"):
            agents = [agent.model_dump() for agent in get_repository_registry().agent_repo.get_all()]
            candidate = select_planning_agent_candidate(agents=agents, planning_policy=planning_policy)
            if candidate:
                planner_selection["selected_agent"] = candidate
                planner_selection["selection_reason"] = "planning_agent_selected"
            else:
                planner_selection["selection_reason"] = "no_planning_agent_available_fallback_to_hub"

        if limit_exceeded and str(limit_exceeded) != "max_plan_nodes":
            planner._stats["errors"] += 1
            return {
                "subtasks": [],
                "created_task_ids": [],
                "raw_response": raw_response if not create_tasks else None,
                "template_used": template_used,
                "planning_origin": planning_origin,
                "repair_strategy_used": repair_strategy_used,
                "repair_attempt_count": repair_attempt_count,
                "parse_mode": parse_mode,
                "feature_flags": flags,
                "plan_limits": limits,
                "error": f"limit_exceeded:{limit_exceeded}",
                "error_classification": "limit_exceeded",
                "limit_exceeded_reason": limit_exceeded,
                "planning_policy": planning_policy,
                "planner_selection": planner_selection,
                "planning_run_id": telemetry_run.id,
            }
        if limit_exceeded and str(limit_exceeded) == "max_plan_nodes":
            logging.warning(
                "plan_soft_truncated:max_plan_nodes observed=%s allowed=%s",
                limits.get("observed_plan_nodes"),
                limits.get("max_plan_nodes"),
            )

        if not subtasks:
            scoped_cfg = dict(getattr(planner, "_goal_effective_config", {}) or {})
            raw_planning_policy = scoped_cfg.get("planning_policy") if isinstance(scoped_cfg.get("planning_policy"), dict) else {}
            allow_non_llm_fallback = bool(raw_planning_policy.get("allow_non_llm_minimal_task_fallback", False))
            if allow_non_llm_fallback:
                subtasks = [
                    self._build_minimal_non_llm_fallback_subtask(
                        goal=goal,
                        mode=mode,
                    )
                ]
                limits = dict(limits or {})
                limits["fallback_task_generated"] = True
                limits["fallback_task_reason"] = "unstructured_llm_response"
            else:
                return {
                    "subtasks": [],
                    "created_task_ids": [],
                    "raw_response": raw_response,
                    "planning_origin": planning_origin,
                    "repair_strategy_used": repair_strategy_used,
                    "repair_attempt_count": repair_attempt_count,
                    "parse_mode": parse_mode,
                    "error_classification": "unstructured_llm_response",
                    "planning_policy": planning_policy,
                    "planner_selection": planner_selection,
                    "planning_run_id": telemetry_run.id,
                }

        proposal_payload = build_plan_proposal(
            goal_id=str(goal_id or "").strip() or "ad-hoc-goal",
            trace_id=str(goal_trace_id or "").strip() or f"trace-{uuid.uuid4().hex[:8]}",
            summary=goal,
            subtasks=subtasks,
            required_capabilities=[],
        )
        known_capabilities = {
            "planning",
            "coding",
            "testing",
            "review",
            "research",
            "ops",
            "analysis",
            "doc",
            "plan.propose",
            "risk.estimate",
            "dependencies.suggest",
            "clarifying_questions.suggest",
        }
        proposal_validation = validate_plan_proposal_payload(proposal_payload, known_capabilities=known_capabilities)
        if not proposal_validation.ok:
            repaired_payload = self._repair_invalid_plan_payload(proposal_validation.normalized_payload, proposal_validation.errors)
            repaired_validation = validate_plan_proposal_payload(repaired_payload, known_capabilities=known_capabilities)
            if repaired_validation.ok:
                proposal_payload = repaired_validation.normalized_payload
                proposal_validation = repaired_validation
        if not proposal_validation.ok:
            planner._stats["errors"] += 1
            return {
                "subtasks": [],
                "created_task_ids": [],
                "raw_response": raw_response if not create_tasks else None,
                "template_used": template_used,
                "planning_origin": planning_origin,
                "repair_strategy_used": repair_strategy_used,
                "repair_attempt_count": repair_attempt_count,
                "parse_mode": parse_mode,
                "feature_flags": flags,
                "plan_limits": limits,
                "error": "invalid_plan_proposal",
                "error_classification": "invalid_plan_proposal",
                "proposal_validation_errors": proposal_validation.errors,
                "planning_policy": planning_policy,
                "planner_selection": planner_selection,
                "planning_run_id": telemetry_run.id,
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
                planning_origin=planning_origin,
                repair_strategy_used=repair_strategy_used,
                repair_attempt_count=repair_attempt_count,
                parse_mode=parse_mode,
                planning_run_id=str(telemetry_run.id),
            )

        created_ids: list[str] = []
        materialization_error: str | None = None
        if create_tasks:
            materialize_nodes = nodes or self._build_nodes(goal_id or "ad-hoc-plan", subtasks, planning_mode)
            created_ids, materialization_error = self._materialize_plan(
                planner=planner,
                plan=plan,
                nodes=materialize_nodes,
                team_id=team_id,
                parent_task_id=parent_task_id,
                goal_id=goal_id,
                goal_trace_id=goal_trace_id,
                mode=mode,
            )
            if materialization_error:
                planner._stats["errors"] += 1
                return {
                    "subtasks": subtasks,
                    "created_task_ids": [],
                    "raw_response": raw_response if not create_tasks else None,
                    "template_used": template_used,
                    "planning_origin": planning_origin,
                    "repair_strategy_used": repair_strategy_used,
                    "repair_attempt_count": repair_attempt_count,
                    "parse_mode": parse_mode,
                    "plan_id": plan.id if plan else None,
                    "plan_node_ids": [node.id for node in nodes],
                    "feature_flags": flags,
                    "plan_limits": limits,
                    "error": materialization_error,
                    "error_classification": materialization_error,
                    "plan_proposal": proposal_validation.normalized_payload,
                    "proposal_validation_errors": proposal_validation.errors,
                    "planning_policy": planning_policy,
                    "planner_selection": planner_selection,
                    "planning_run_id": telemetry_run.id,
                }
            planner._stats["goals_processed"] += 1
            planner._persist_state()

        dependency_modes: dict[str, int] = {}
        for subtask in list(subtasks or []):
            dep_mode = str(subtask.get("dependency_mode") or "sequential").strip().lower() or "sequential"
            dependency_modes[dep_mode] = dependency_modes.get(dep_mode, 0) + 1
        expected_artifacts_count = sum(
            len([item for item in list((subtask or {}).get("expected_artifacts") or []) if isinstance(item, dict)])
            for subtask in list(subtasks or [])
        )
        verification_spec_count = sum(
            1
            for subtask in list(subtasks or [])
            if isinstance((subtask or {}).get("verification_spec"), dict) and bool((subtask or {}).get("verification_spec"))
        )
        telemetry_run = get_planning_telemetry_service().update_run(
            telemetry_run,
            validation_success=True,
            validation_errors=list(proposal_validation.errors or []),
            generated_task_count=len(created_ids),
            expected_artifacts_count=expected_artifacts_count,
            verification_spec_count=verification_spec_count,
            dependency_mode_distribution=dependency_modes,
            materialized_task_ids=created_ids,
            status="materialized" if created_ids else "planned",
        )
        try:
            get_planning_review_queue_service().evaluate_run_for_review(telemetry_run)
        except Exception:
            pass
        get_planning_evaluation_service().evaluate(
            planning_run_id=str(telemetry_run.id),
            goal_id=goal_id,
            trace_id=goal_trace_id,
        )
        # opportunistic mining step (bounded window inside service)
        try:
            get_planning_template_mining_service().mine_candidates(min_total_score=0.85, limit=30)
        except Exception:
            pass

        return {
            "subtasks": subtasks,
            "created_task_ids": created_ids,
            "raw_response": raw_response if not create_tasks else None,
            "template_used": template_used,
            "planning_origin": planning_origin,
            "repair_strategy_used": repair_strategy_used,
            "repair_attempt_count": repair_attempt_count,
            "parse_mode": parse_mode,
            "plan_id": plan.id if plan else None,
            "plan_node_ids": [node.id for node in nodes],
            "feature_flags": flags,
            "plan_limits": limits,
            "plan_proposal": proposal_validation.normalized_payload,
            "proposal_validation_errors": proposal_validation.errors,
            "planning_policy": planning_policy,
            "planner_selection": planner_selection,
            "goal_config_source": str(getattr(planner, "_goal_config_source", "global_fallback")),
            "planning_run_id": telemetry_run.id,
            "prompt_version_id": prompt_version_id or None,
            "planning_profile": planning_profile or None,
            "planning_intent": intent,
            "planning_strategy_rationale": str(getattr(planner, "_planning_strategy_rationale", "")) or None,
        }

    def get_latest_plan_for_goal(self, goal_id: str) -> tuple[PlanDB | None, list[PlanNodeDB]]:
        repos = get_repository_registry()
        plans = repos.plan_repo.get_by_goal_id(goal_id)
        if not plans:
            return None, []
        plan = plans[0]
        return plan, repos.plan_node_repo.get_by_plan_id(plan.id)

    def patch_plan_node(self, goal_id: str, node_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        repos = get_repository_registry()
        plan, _ = self.get_latest_plan_for_goal(goal_id)
        if not plan:
            return None, "plan_not_found"
        node = repos.plan_node_repo.get_by_id(node_id)
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
        repos.plan_node_repo.save(node)
        plan.updated_at = time.time()
        repos.plan_repo.save(plan)
        return node.model_dump(), None


planning_service = PlanningService()


def get_planning_service() -> PlanningService:
    return planning_service
