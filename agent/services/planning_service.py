import contextlib
import hashlib
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

logger = logging.getLogger(__name__)
from typing import Any, Optional

from flask import current_app, g

from agent.db_models import PlanDB, PlanNodeDB
from agent.routes.tasks.dependency_policy import normalize_depends_on, validate_dependency_graph
from agent.services.lifecycle_service import get_task_lifecycle_service
from agent.services.planning_feature_flags import (
    get_goal_feature_flags,
    get_plan_generation_limits,
    set_goal_feature_flags,
)
from agent.services.planning_proposal_service import (
    build_plan_proposal,
    normalize_planning_policy_config,
    select_planning_agent_candidate,
    validate_plan_proposal_payload,
)
from agent.services.planning_strategies import (
    HubCopilotPlanningStrategy,
    LLMPlanningStrategy,
    PlanningStrategyResult,
    TemplatePlanningStrategy,
)
from agent.services.planning_subtask_sanitizer import (
    infer_subtask_task_kind,
    merge_verification_defaults,
    retrieval_hints_for_task_kind,
    sanitize_blueprint_provenance,
    sanitize_llm_subtask_policy_hints,
    sanitize_role_defaults,
)
from agent.services.planning_utils import parse_subtasks_from_llm_response, sanitize_input, validate_goal
from agent.services.planning_evaluation_service import get_planning_evaluation_service
from agent.services.planning_telemetry_service import get_planning_telemetry_service
from agent.services.goal_planning_intent_service import get_goal_planning_intent_service
from agent.services.planning_quality_service import get_planning_quality_service
from agent.services.planning_prompt_evolver_service import get_planning_prompt_evolver_service
from agent.services.planning_model_profile_service import get_planning_model_profile_service
from agent.services.llm_first_planning_orchestrator_service import get_llm_first_planning_orchestrator_service
from agent.services.planning_template_mining_service import get_planning_template_mining_service
from agent.services.planning_review_queue_service import get_planning_review_queue_service
from agent.services.repository_registry import get_repository_registry
from agent.services.recovery_plan_contract import calculate_recovery_plan_digest
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.verification_policy_service import default_verification_spec
from agent.services.worker_routing_policy_utils import (
    derive_required_capabilities,
    merge_capabilities_with_blueprint_defaults,
)
from agent.services.planning_service_pipeline import (
    _run_quality_repairs,
    _validate_and_finalize_plan,
    resolve_subtasks_with_timeout,
)


class PlanningService:
    _materialization_locks_guard = threading.Lock()
    _materialization_locks: dict[str, threading.RLock] = {}

    @classmethod
    def _materialization_lock(cls, plan_id: str) -> threading.RLock:
        """Return a process-local lock for one persisted plan.

        The persisted plan status remains the durable idempotency marker.  The
        narrow per-plan lock additionally prevents two concurrent approval
        callbacks in one Hub process from staging different random task IDs.
        """
        with cls._materialization_locks_guard:
            return cls._materialization_locks.setdefault(str(plan_id), threading.RLock())

    @classmethod
    @contextlib.contextmanager
    def _distributed_materialization_lock(cls, plan_id: str):
        """Serialize exact-plan edits/materialization across Hub processes.

        PostgreSQL advisory locks are used only as a coordination primitive;
        repositories keep their existing transaction boundaries. SQLite and
        injected test repositories remain protected by the process-local lock.
        """

        try:
            from sqlalchemy import text

            from agent.database import engine
        except Exception:
            logging.getLogger(__name__).exception(
                "Distributed plan lock setup failed for %s",
                plan_id,
            )
            yield False
            return

        if str(engine.dialect.name or "").lower() != "postgresql":
            yield True
            return

        connection = None
        try:
            lock_id = int(
                hashlib.sha256(
                    f"planning-materialization:{plan_id}".encode("utf-8")
                ).hexdigest()[:15],
                16,
            )
            connection = engine.connect()
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id},
                ).scalar()
            )
        except Exception:
            if connection is not None:
                connection.close()
            logging.getLogger(__name__).exception(
                "Distributed plan lock acquisition failed for %s",
                plan_id,
            )
            yield False
            return

        try:
            yield acquired
        finally:
            try:
                if acquired:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": lock_id},
                    )
            finally:
                if connection is not None:
                    connection.close()

    @contextlib.contextmanager
    def plan_mutation_lock(self, plan_id: str):
        """Serialize every exact-plan mutation in this and other Hub processes."""

        normalized_plan_id = str(plan_id or "").strip()
        with (
            self._materialization_lock(normalized_plan_id),
            self._distributed_materialization_lock(
                normalized_plan_id
            ) as distributed_lock_acquired,
        ):
            yield bool(distributed_lock_acquired)

    @staticmethod
    def _maybe_evolve_prompt(*, telemetry_run, planning_policy: dict[str, Any]) -> None:
        try:
            get_planning_prompt_evolver_service().evolve_from_run(
                run=telemetry_run,
                planning_policy=planning_policy,
            )
        except Exception as exc:
            logger.warning("_maybe_evolve_prompt failed: %s", exc)

    @staticmethod
    def _update_profile_learning_state(*, telemetry_run) -> None:
        try:
            from agent.services.planning_telemetry_service import get_planning_telemetry_service
            record = get_planning_telemetry_service().build_learning_record(telemetry_run)
            observed_shape = str(record.get("observed_output_shape") or "").strip() or None
            if not observed_shape:
                return
            provider = str(record.get("model_provider") or "").strip() or None
            model_name = str(record.get("model_name") or "").strip() or None
            if not provider or not model_name:
                return
            model_family = str(record.get("model_family") or "").strip() or None
            profile_svc = get_planning_model_profile_service()
            profile = profile_svc.resolve_profile(provider=provider, model_name=model_name)
            profile_id = profile.get("id")
            if not profile_id:
                return
            db_profile = None
            for _p in get_repository_registry().planning_model_profile_repo.get_enabled():
                if str(getattr(_p, 'id', '') or '') == str(profile_id or ''):
                    db_profile = _p
                    break
            if db_profile is None:
                return
            current_state = dict(db_profile.learning_state or {})
            current_shape = str(current_state.get("observed_output_shape") or "").strip()
            if current_shape == observed_shape:
                return
            profile_svc.update_learning_state(
                db_profile,
                state=str(current_state.get("state") or "stable"),
                source="planning_service_post_run",
                observed_output_shape=observed_shape,
                observed_model_family=model_family,
                sample_size=(int(current_state.get("sample_size") or 0) + 1) if current_state.get("sample_size") else 1,
            )
        except Exception as exc:
            logger.warning("_update_profile_learning_state failed: %s", exc, exc_info=True)

    def _resolve_planning_policy(self, scoped_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(scoped_cfg, dict):
            scoped_raw = scoped_cfg.get("planning_policy")
            if isinstance(scoped_raw, dict) and scoped_raw:
                return normalize_planning_policy_config(scoped_raw)
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
        planning_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._run_planning_strategies(
            planner=planner,
            goal=goal,
            context=context,
            use_template=use_template,
            use_repo_context=use_repo_context,
            mode=mode,
            mode_data=mode_data,
            planning_policy=planning_policy,
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

    @staticmethod
    def _build_selective_repair_prompt(
        *,
        goal: str,
        mode: str,
        missing_categories: list[str],
        generic_task_indices: list[int],
        preferred_output_format: str,
        required_task_kinds: list[str] | None = None,
        error_codes: list[str] | None = None,
    ) -> str:
        effective_missing = list(missing_categories or [])
        if mode == "new_software_project" and not effective_missing:
            effective_missing = ["analysis", "infrastructure", "implementation", "tests", "review"]
        missing = ", ".join(effective_missing) if effective_missing else "none"
        generic = ", ".join(str(i) for i in generic_task_indices[:12]) if generic_task_indices else "none"
        missing_lines = "\n".join(
            f"- category={str(cat).strip().lower()}: mindestens 1 konkrete Aufgabe"
            for cat in effective_missing
            if str(cat).strip()
        )
        if not missing_lines:
            missing_lines = "- none"
        required_lines = "\n".join(
            f"- task_kind={str(kind).strip().lower()}"
            for kind in list(required_task_kinds or [])
            if str(kind).strip()
        ) or "- none"
        repair_error_codes = ", ".join(str(code).strip() for code in list(error_codes or []) if str(code).strip()) or "none"
        return (
            "Repair only missing or weak parts of the plan. Do not rewrite everything.\n"
            f"GOAL: {goal}\n"
            f"MODE: {mode}\n"
            f"MISSING_CATEGORIES: {missing}\n"
            f"GENERIC_TASK_INDEXES: {generic}\n"
            f"CONTRACT_ERROR_CODES: {repair_error_codes}\n"
            "Return only additional or replacement tasks needed to close gaps.\n"
            "WICHTIG:\n"
            "1) Liefere NUR JSON-Array.\n"
            "2) Jede Aufgabe MUSS diese Felder haben: title, description, task_kind, priority.\n"
            "3) task_kind-Mapping (GENAU diese Werte verwenden):\n"
            "   analysis → task_kind='analysis'\n"
            "   infrastructure → task_kind='ops'  (Docker, CI/CD, Env-Setup, Dockerfile, Pipeline)\n"
            "   implementation → task_kind='coding'\n"
            "   tests → task_kind='testing'\n"
            "   review → task_kind='review'\n"
            "4) Jede description MUSS einen konkreten Output nennen (Dateipfad, Endpoint, Command oder Artifact).\n"
            "5) Keine generischen Sammel-Tasks.\n"
            "6) Decke zwingend diese fehlenden Kategorien ab:\n"
            f"{missing_lines}\n"
            "7) Decke diese fehlenden task_kind-Werte direkt ab:\n"
            f"{required_lines}\n"
            f"Preferred output format: {preferred_output_format}.\n"
            "Keep fields short and concrete."
        )

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
        planning_policy: dict[str, Any] | None = None,
    ) -> PlanningStrategyResult:
        planning_policy = dict(planning_policy or self._resolve_planning_policy())
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
            task_kind = infer_subtask_task_kind(subtask)
            retrieval_hints = retrieval_hints_for_task_kind(task_kind)
            blueprint_provenance = sanitize_blueprint_provenance(subtask)
            role_defaults = sanitize_role_defaults(subtask)
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
                    verification_spec=merge_verification_defaults(
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
        initial_rationale: dict[str, Any] | None = None,
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
                **dict(initial_rationale or {}),
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
        deterministic_task_ids: bool = False,
        source_task_id: Optional[str] = None,
        initial_task_status: str = "todo",
    ) -> tuple[list[str], str | None]:
        repos = get_repository_registry()
        if goal_id:
            authoritative_goal = repos.goal_repo.get_by_id(
                str(goal_id)
            )
            from agent.services.organization_planning_adapter import (
                is_organization_goal,
            )

            if is_organization_goal(authoritative_goal):
                return [], "organization_planning_legacy_bypass_blocked"
            if (
                authoritative_goal is None
                or str(
                    getattr(
                        authoritative_goal,
                        "status",
                        "",
                    )
                    or ""
                )
                .strip()
                .lower()
                in {
                    "completed",
                    "failed",
                    "cancelled",
                    "aborted",
                    "timeout",
                    "archived",
                }
            ):
                return [], "goal_terminal"
        staged = self._prepare_materialization(
            nodes=nodes,
            deterministic_seed=(
                str(getattr(plan, "id", "") or "")
                if deterministic_task_ids and plan is not None
                else None
            ),
        )
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

        existing_ids: list[str] = []
        entries_to_create = list(staged)
        if deterministic_task_ids and plan is not None:
            (
                existing_ids,
                entries_to_create,
                binding_error,
            ) = self._classify_deterministic_materialization(
                plan=plan,
                staged=staged,
                team_id=team_id,
                parent_task_id=parent_task_id,
                source_task_id=source_task_id,
                initial_task_status=initial_task_status,
            )
            if binding_error is not None:
                plan.status = "failed"
                plan.rationale = {
                    **dict(plan.rationale or {}),
                    "materialization_error": binding_error,
                }
                plan.updated_at = time.time()
                repos.plan_repo.save(plan)
                return [], binding_error

        newly_created_ids: list[str] = []
        try:
            for entry in entries_to_create:
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
                    derivation_depth=1 if (parent_task_id or source_task_id) else 0,
                    depends_on=task_depends_on,
                    source_task_id=source_task_id,
                    initial_status=initial_task_status,
                )
                newly_created_ids.append(task_id)
                node.materialized_task_id = task_id
                node.status = "materialized"
                node.updated_at = time.time()
                repos.plan_node_repo.save(node)
                planner._stats["tasks_created"] += 1
        except Exception as exc:
            logging.getLogger(__name__).warning("Plan materialization failed for plan %s: %s", plan.id if plan else "ad-hoc", exc)
            self._rollback_materialization(
                plan=plan,
                nodes=nodes,
                created_ids=newly_created_ids,
                error=str(exc),
            )
            return [], "materialization_failed"

        materialized_ids = [
            str(entry["task_id"])
            for entry in staged
            if str(entry["task_id"]) in set(existing_ids + newly_created_ids)
        ]
        if plan:
            plan.status = "materialized" if materialized_ids else "draft"
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)
        return materialized_ids, None

    def materialize_existing_plan(
        self,
        *,
        planner,
        plan_id: str,
        approval_request_id: str,
        team_id: str | None = None,
        parent_task_id: str | None = None,
        source_task_id: str | None = None,
        expected_plan_digest: str | None = None,
        initial_task_status: str = "todo",
    ) -> dict[str, Any]:
        """Materialize one persisted draft after a Hub approval was granted.

        Approval validation deliberately remains outside this service.  This
        method owns only the planning-domain transition from a validated,
        persisted plan to Hub tasks, making the mutation reusable without
        exposing ``_materialize_plan`` to routes or approval infrastructure.
        """
        normalized_plan_id = str(plan_id or "").strip()
        normalized_approval_id = str(approval_request_id or "").strip()
        normalized_initial_status = str(
            initial_task_status or "todo"
        ).strip().lower()
        if not normalized_plan_id:
            return {"status": "failed", "reason_code": "plan_id_required", "created_task_ids": []}
        if not normalized_approval_id:
            return {"status": "failed", "reason_code": "approval_request_id_required", "created_task_ids": []}
        if normalized_initial_status not in {
            "todo",
            "blocked_by_dependency",
            "paused",
        }:
            return {
                "status": "failed",
                "reason_code": "initial_task_status_invalid",
                "created_task_ids": [],
            }

        with self.plan_mutation_lock(
            normalized_plan_id
        ) as distributed_lock_acquired:
            if not distributed_lock_acquired:
                return {
                    "status": "failed",
                    "reason_code": "plan_mutation_in_progress",
                    "plan_id": normalized_plan_id,
                    "created_task_ids": [],
                }
            repos = get_repository_registry()
            plan = repos.plan_repo.get_by_id(normalized_plan_id)
            if plan is None:
                return {"status": "failed", "reason_code": "plan_not_found", "created_task_ids": []}
            if str(
                dict(plan.rationale or {}).get("approval_request_id") or ""
            ) != normalized_approval_id:
                return {
                    "status": "failed",
                    "reason_code": "plan_approval_binding_mismatch",
                    "plan_id": plan.id,
                    "created_task_ids": [],
                }
            already_materialized = (
                str(plan.status or "") == "materialized"
            )
            if (
                not already_materialized
                and str(plan.status or "")
                not in {"draft", "pending_approval", "approved"}
            ):
                return {
                    "status": "failed",
                    "reason_code": f"plan_status_not_materializable:{plan.status}",
                    "plan_id": plan.id,
                    "created_task_ids": [],
                }

            nodes = repos.plan_node_repo.get_by_plan_id(plan.id)
            if not nodes:
                return {
                    "status": "failed",
                    "reason_code": "plan_nodes_missing",
                    "plan_id": plan.id,
                    "created_task_ids": [],
                }
            normalized_expected_digest = str(
                expected_plan_digest or ""
            ).strip()
            if normalized_expected_digest:
                current_digest = calculate_recovery_plan_digest(plan, nodes)
                bound_digest = str(
                    dict(plan.rationale or {}).get("plan_digest") or ""
                ).strip()
                if (
                    current_digest != normalized_expected_digest
                    or bound_digest != normalized_expected_digest
                ):
                    return {
                        "status": "failed",
                        "reason_code": "recovery_plan_digest_stale",
                        "plan_id": plan.id,
                        "current_plan_digest": current_digest,
                        "created_task_ids": [],
                    }

            if already_materialized:
                staged = self._prepare_materialization(
                    nodes=nodes,
                    deterministic_seed=str(plan.id),
                )
                if staged is None:
                    return {
                        "status": "failed",
                        "reason_code": "invalid_dependencies",
                        "plan_id": plan.id,
                        "created_task_ids": [],
                    }
                (
                    existing_ids,
                    missing_entries,
                    binding_error,
                ) = self._classify_deterministic_materialization(
                    plan=plan,
                    staged=staged,
                    team_id=team_id,
                    parent_task_id=parent_task_id,
                    source_task_id=source_task_id,
                    initial_task_status=normalized_initial_status,
                )
                if binding_error is not None or missing_entries:
                    return {
                        "status": "failed",
                        "reason_code": (
                            binding_error
                            or "materialized_tasks_missing"
                        ),
                        "plan_id": plan.id,
                        "created_task_ids": [],
                    }
                return {
                    "status": "materialized",
                    "reason_code": "already_materialized",
                    "plan_id": plan.id,
                    "created_task_ids": existing_ids,
                }

            validation = self._validate_existing_plan_for_materialization(
                plan=plan,
                nodes=nodes,
                team_id=team_id,
            )
            if not validation["ok"]:
                plan.status = "draft"
                plan.rationale = {
                    **dict(plan.rationale or {}),
                    "approval_state": "validation_failed",
                    "materialization_validation": validation,
                }
                plan.updated_at = time.time()
                repos.plan_repo.save(plan)
                return {
                    "status": "failed",
                    "reason_code": str(validation["reason_code"]),
                    "plan_id": plan.id,
                    "created_task_ids": [],
                    "validation": validation,
                }

            plan.status = "approved"
            plan.rationale = {
                **dict(plan.rationale or {}),
                "approval_request_id": normalized_approval_id,
                "approval_state": "granted",
                "materialization_validation": validation,
            }
            plan.updated_at = time.time()
            plan = repos.plan_repo.save(plan)
            created_ids, error = self._materialize_plan(
                planner=planner,
                plan=plan,
                nodes=nodes,
                team_id=team_id,
                parent_task_id=parent_task_id,
                goal_id=plan.goal_id,
                goal_trace_id=plan.trace_id,
                mode=str(plan.planning_mode or "generic"),
                deterministic_task_ids=True,
                source_task_id=source_task_id,
                initial_task_status=normalized_initial_status,
            )
            if error:
                retryable = self._restore_retryable_materialization_state(
                    plan_id=plan.id,
                    error=error,
                    team_id=team_id,
                    parent_task_id=parent_task_id,
                    source_task_id=source_task_id,
                    initial_task_status=normalized_initial_status,
                )
                return {
                    "status": "failed",
                    "reason_code": error,
                    "plan_id": plan.id,
                    "created_task_ids": [],
                    "retryable": retryable,
                }
            return {
                "status": "materialized",
                "reason_code": "approved_plan_materialized",
                "plan_id": plan.id,
                "created_task_ids": created_ids,
            }

    @staticmethod
    def _task_matches_materialization_binding(
        task: Any,
        *,
        plan: PlanDB,
        node: PlanNodeDB,
        task_id: str,
        team_id: str | None,
        parent_task_id: str | None,
        source_task_id: str | None,
        depends_on: list[str],
        initial_task_status: str,
    ) -> bool:
        """Check the immutable ownership fields of a deterministic plan task."""

        expected_source = (
            source_task_id
            if source_task_id is not None
            else parent_task_id
        )
        expected_depth = 1 if (parent_task_id or source_task_id) else 0
        rationale = dict(node.rationale or {})
        expected = {
            "id": str(task_id),
            "goal_id": str(plan.goal_id or ""),
            "goal_trace_id": str(plan.trace_id or ""),
            "plan_id": str(plan.id or ""),
            "plan_node_id": str(node.id or ""),
            "team_id": str(team_id or ""),
            "parent_task_id": str(parent_task_id or ""),
            "source_task_id": str(expected_source or ""),
            "derivation_reason": f"goal_{plan.planning_mode or 'planning'}",
            "derivation_depth": int(expected_depth),
            "status": str(initial_task_status or "todo").strip().lower(),
            "title": str(node.title or ""),
            "description": str(node.description or ""),
            "priority": str(node.priority or ""),
            "task_kind": str(rationale.get("task_kind") or ""),
            "retrieval_intent": str(
                rationale.get("retrieval_intent") or ""
            ),
            "required_context_scope": str(
                rationale.get("required_context_scope") or ""
            ),
            "preferred_bundle_mode": str(
                rationale.get("preferred_bundle_mode") or ""
            ),
        }
        actual = {
            "id": str(getattr(task, "id", "") or ""),
            "goal_id": str(getattr(task, "goal_id", "") or ""),
            "goal_trace_id": str(
                getattr(task, "goal_trace_id", "") or ""
            ),
            "plan_id": str(getattr(task, "plan_id", "") or ""),
            "plan_node_id": str(
                getattr(task, "plan_node_id", "") or ""
            ),
            "team_id": str(getattr(task, "team_id", "") or ""),
            "parent_task_id": str(
                getattr(task, "parent_task_id", "") or ""
            ),
            "source_task_id": str(
                getattr(task, "source_task_id", "") or ""
            ),
            "derivation_reason": str(
                getattr(task, "derivation_reason", "") or ""
            ),
            "derivation_depth": int(
                getattr(task, "derivation_depth", 0) or 0
            ),
            "status": str(getattr(task, "status", "") or "")
            .strip()
            .lower(),
            "title": str(getattr(task, "title", "") or ""),
            "description": str(
                getattr(task, "description", "") or ""
            ),
            "priority": str(getattr(task, "priority", "") or ""),
            "task_kind": str(
                getattr(task, "task_kind", "") or ""
            ),
            "retrieval_intent": str(
                getattr(task, "retrieval_intent", "") or ""
            ),
            "required_context_scope": str(
                getattr(task, "required_context_scope", "") or ""
            ),
            "preferred_bundle_mode": str(
                getattr(task, "preferred_bundle_mode", "") or ""
            ),
        }
        return (
            actual == expected
            and list(getattr(task, "depends_on", None) or [])
            == list(depends_on or [])
            and list(
                getattr(task, "required_capabilities", None) or []
            )
            == list(rationale.get("required_capabilities") or [])
            and dict(getattr(task, "verification_spec", None) or {})
            == dict(node.verification_spec or {})
        )

    def _classify_deterministic_materialization(
        self,
        *,
        plan: PlanDB,
        staged: list[dict[str, Any]],
        team_id: str | None,
        parent_task_id: str | None,
        source_task_id: str | None,
        initial_task_status: str,
    ) -> tuple[list[str], list[dict[str, Any]], str | None]:
        """Resume only a provably identical deterministic partial DAG."""

        repos = get_repository_registry()
        existing_ids: list[str] = []
        entries_to_create: list[dict[str, Any]] = []
        for entry in staged:
            node = entry["node"]
            task_id = str(entry["task_id"])
            bound_task_id = str(
                getattr(node, "materialized_task_id", "") or ""
            ).strip()
            if bound_task_id and bound_task_id != task_id:
                return [], [], "materialization_binding_conflict"

            task = repos.task_repo.get_by_id(task_id)
            if task is None:
                if bound_task_id:
                    return [], [], "materialization_binding_conflict"
                entries_to_create.append(entry)
                continue
            if not self._task_matches_materialization_binding(
                task,
                plan=plan,
                node=node,
                task_id=task_id,
                team_id=team_id,
                parent_task_id=parent_task_id,
                source_task_id=source_task_id,
                depends_on=list(entry["depends_on"] or []),
                initial_task_status=initial_task_status,
            ):
                return [], [], "materialization_binding_conflict"
            if not bound_task_id:
                # Crash window: task commit succeeded, node commit did not.
                node.materialized_task_id = task_id
                node.status = "materialized"
                node.updated_at = time.time()
                repos.plan_node_repo.save(node)
            existing_ids.append(task_id)
        return existing_ids, entries_to_create, None

    def _restore_retryable_materialization_state(
        self,
        *,
        plan_id: str,
        error: str,
        team_id: str | None,
        parent_task_id: str | None,
        source_task_id: str | None,
        initial_task_status: str,
    ) -> bool:
        """Re-open a plan when every deterministic survivor is exact."""

        if str(error or "") != "materialization_failed":
            return False
        repos = get_repository_registry()
        plan = repos.plan_repo.get_by_id(plan_id)
        nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
        if plan is None or not nodes:
            return False
        staged = self._prepare_materialization(
            nodes=nodes,
            deterministic_seed=str(plan.id),
        )
        if staged is None:
            return False
        (
            existing_ids,
            _missing_entries,
            binding_error,
        ) = self._classify_deterministic_materialization(
            plan=plan,
            staged=staged,
            team_id=team_id,
            parent_task_id=parent_task_id,
            source_task_id=source_task_id,
            initial_task_status=initial_task_status,
        )
        if binding_error is not None:
            return False
        plan.status = "pending_approval"
        plan.rationale = {
            **dict(plan.rationale or {}),
            "approval_state": "materialization_retryable",
            "materialization_error": str(error)[:500],
            "materialization_survivor_task_ids": list(
                existing_ids
            ),
        }
        plan.updated_at = time.time()
        repos.plan_repo.save(plan)
        return True

    def _validate_existing_plan_for_materialization(
        self,
        *,
        plan: PlanDB,
        nodes: list[PlanNodeDB],
        team_id: str | None,
    ) -> dict[str, Any]:
        """Re-run deterministic proposal, DAG, and quality gates after review."""
        dependency_error = self._dependency_contract_error(nodes)
        if dependency_error is not None:
            return {
                "ok": False,
                "reason_code": dependency_error,
            }
        key_to_position = {
            str(node.node_key): str(index)
            for index, node in enumerate(nodes, start=1)
        }
        subtasks: list[dict[str, Any]] = []
        for node in nodes:
            rationale = dict(node.rationale or {})
            subtasks.append(
                {
                    "title": str(node.title or ""),
                    "description": str(node.description or ""),
                    "priority": str(node.priority or "Medium"),
                    "task_kind": str(rationale.get("task_kind") or "coding"),
                    "depends_on": [
                        key_to_position[dep]
                        for dep in list(node.depends_on or [])
                    ],
                    "required_capabilities": [
                        str(item)
                        for item in list(rationale.get("required_capabilities") or [])
                        if str(item).strip()
                    ],
                    "expected_artifacts": [
                        dict(item)
                        for item in list(rationale.get("expected_artifacts") or [])
                        if isinstance(item, dict)
                    ],
                    "verification_spec": dict(node.verification_spec or {}),
                }
            )

        scoped = get_goal_config_runtime_service().get_effective_config(
            goal_id=plan.goal_id,
            task_id=None,
        )
        planning_policy = self._resolve_planning_policy(dict(scoped.config or {}))
        quality = get_planning_quality_service().evaluate(
            subtasks=subtasks,
            mode=str(plan.planning_mode or "generic"),
            planning_policy=planning_policy,
            team_id=team_id,
        )
        remaining_reasons = [
            item
            for item in str(quality.reason or "").split("|")
            if item and item != "ok"
        ]
        soft_only = bool(subtasks) and remaining_reasons and all(
            item.startswith("missing_categories:")
            for item in remaining_reasons
        )
        if not quality.ok and not soft_only:
            return {
                "ok": False,
                "reason_code": "planning_quality_gate_failed",
                "quality_reason": str(quality.reason or ""),
                "missing_categories": list(quality.missing_categories or []),
                "generic_task_indices": list(quality.generic_task_indices or []),
            }

        proposal = build_plan_proposal(
            goal_id=plan.goal_id,
            trace_id=plan.trace_id,
            summary=f"Approved recovery plan {plan.id}",
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
        proposal_validation = validate_plan_proposal_payload(
            proposal,
            known_capabilities=known_capabilities,
        )
        if not proposal_validation.ok:
            return {
                "ok": False,
                "reason_code": "invalid_plan_proposal",
                "proposal_validation_errors": list(proposal_validation.errors or []),
            }
        if self._prepare_materialization(nodes=nodes) is None:
            return {
                "ok": False,
                "reason_code": "invalid_dependencies",
            }
        return {
            "ok": True,
            "reason_code": "validated",
            "quality_soft_accepted": bool(soft_only),
        }

    @staticmethod
    def _dependency_contract_error(
        nodes: list[PlanNodeDB],
    ) -> str | None:
        node_keys = [str(node.node_key or "").strip() for node in nodes]
        if any(not key for key in node_keys):
            return "plan_node_key_missing"
        if len(set(node_keys)) != len(node_keys):
            return "plan_node_key_duplicate"
        known = set(node_keys)
        for node in nodes:
            dependencies = [
                str(value or "").strip()
                for value in list(node.depends_on or [])
            ]
            if len(set(dependencies)) != len(dependencies):
                return "plan_dependency_duplicate"
            if str(node.node_key) in dependencies:
                return "plan_dependency_self_reference"
            if any(not dependency or dependency not in known for dependency in dependencies):
                return "plan_dependency_unknown"
        return None

    def _prepare_materialization(
        self,
        nodes: list[PlanNodeDB],
        *,
        deterministic_seed: str | None = None,
    ) -> list[dict[str, Any]] | None:
        if self._dependency_contract_error(nodes) is not None:
            return None
        if deterministic_seed:
            node_to_task_id = {
                node.node_key: (
                    "goal-"
                    + hashlib.sha256(
                        (
                            str(deterministic_seed)
                            + "\x00"
                            + str(node.id)
                            + "\x00"
                            + str(node.node_key)
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                )
                for node in nodes
            }
        else:
            node_to_task_id = {
                node.node_key: f"goal-{uuid.uuid4().hex[:8]}"
                for node in nodes
            }
        staged: list[dict[str, Any]] = []
        created_order: list[str] = []
        staged_graph: dict[str, list[str]] = {}
        for node in nodes:
            task_id = node_to_task_id[node.node_key]
            task_depends_on = []
            is_parallel_node = bool((node.rationale or {}).get("parallel"))
            dependency_mode = str((node.rationale or {}).get("dependency_mode") or "").strip().lower()
            if node.depends_on:
                task_depends_on = [
                    node_to_task_id[dep] for dep in node.depends_on
                ]
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
        initial_plan_rationale: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if goal_id:
            from agent.services.organization_planning_adapter import (
                is_organization_goal,
                load_goal_for_planning,
                organization_id_from_goal,
                organization_planning_required_response,
            )

            authoritative_goal = load_goal_for_planning(goal_id)
            if is_organization_goal(authoritative_goal):
                return organization_planning_required_response(
                    goal_id=goal_id,
                    organization_id=organization_id_from_goal(authoritative_goal),
                )
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
        planning_policy = self._resolve_planning_policy(scoped_cfg)
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

        resolved = resolve_subtasks_with_timeout(
            service=self,
            planner=planner,
            goal=goal,
            context=context,
            use_template=use_template,
            use_repo_context=use_repo_context,
            mode=mode,
            mode_data=mode_data,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
            telemetry_run=telemetry_run,
            planning_policy=planning_policy,
        )
        if "error" in resolved:
            return resolved
        telemetry_run = resolved.pop("_telemetry_run")

        subtasks = resolved["subtasks"]
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
            cleaned, warns = sanitize_llm_subtask_policy_hints(subtask)
            hardened_subtasks.append(cleaned)
            policy_gate_warnings.extend(list(warns or []))
        subtasks = hardened_subtasks
        preferred_output_format = str(
            planning_policy.get("preferred_output_format")
            or ((planning_policy.get("runtime_profiles") or {}).get(str(planning_policy.get("default_runtime_profile") or ""), {}) or {}).get("preferred_output_format")
            or "json"
        ).strip().lower()

        # Pipeline phase: Validate -> Selective Repair (bounded rounds).
        quality = get_planning_quality_service().evaluate(
            subtasks=subtasks,
            mode=mode,
            planning_policy=planning_policy,
            team_id=team_id,
        )
        has_explicit_validation_profiles = (
            "validation_profiles" in planning_policy
            and isinstance(planning_policy.get("validation_profiles"), dict)
        )
        enforce_quality_gate = (
            mode == "new_software_project"
            or (has_explicit_validation_profiles and bool(str(goal_id or "").strip()))
        )
        if current_app.testing:
            enforce_quality_gate = False
        perform_quality_repairs = bool(create_tasks and enforce_quality_gate)
        repair_context = dict(mode_data_dict.get("planning_repair_context") or {})
        required_task_kinds = [
            str(item).strip().lower()
            for item in list(repair_context.get("missing_task_kinds") or [])
            if str(item).strip()
        ]
        repair_error_codes = [
            str(item).strip()
            for item in list(repair_context.get("error_codes") or [])
            if str(item).strip()
        ]
        _quality_result = _run_quality_repairs(
            service=self,
            planner=planner,
            subtasks=subtasks,
            goal=goal,
            mode=mode,
            planning_policy=planning_policy,
            team_id=team_id,
            scoped_llm_cfg=scoped_llm_cfg,
            goal_id=goal_id,
            quality=quality,
            repair_context=repair_context,
            preferred_output_format=preferred_output_format,
            perform_quality_repairs=perform_quality_repairs,
            enforce_quality_gate=enforce_quality_gate,
            create_tasks=create_tasks,
        )
        subtasks = _quality_result["subtasks"]
        quality = _quality_result["quality"]
        selective_repair_codes = _quality_result["selective_repair_codes"]

        # Soft-accept: if the remaining failure is ONLY missing_categories (no too_few_tasks,
        # no too_many_generic) and we have subtasks, pass them through. The routes layer
        # already treats missing_categories as a soft failure and will set the goal to
        # 'planned' rather than 'failed'. Hard-fail only for too_few_tasks / too_many_generic.
        _remaining_reasons = [r for r in (quality.reason or "").split("|") if r and r != "ok"]
        _is_soft_remaining = (
            not quality.ok
            and subtasks
            and all(r.startswith("missing_categories:") for r in _remaining_reasons)
        )
        if _is_soft_remaining:
            selective_repair_codes.append("soft_accepted_missing_categories")
            quality = type(quality)(
                ok=True,
                reason="ok",
                missing_categories=quality.missing_categories,
                generic_task_indices=quality.generic_task_indices,
                details=quality.details,
            )

        if enforce_quality_gate and create_tasks and subtasks and not quality.ok:
            telemetry_run = get_planning_telemetry_service().update_run(
                telemetry_run,
                validation_success=False,
                validation_errors=[quality.reason],
                error_classification="planning_quality_gate_failed",
                status="failed",
            )
            self._maybe_evolve_prompt(telemetry_run=telemetry_run, planning_policy=planning_policy)
            self._update_profile_learning_state(telemetry_run=telemetry_run)
            return {
                "subtasks": [],
                "created_task_ids": [],
                "error": "planning_insufficient_task_detail",
                "error_classification": "planning_quality_gate_failed",
                "planning_quality_reason": quality.reason,
                "planning_quality_details": quality.details,
                "planning_policy": planning_policy,
                "planner_selection": {"selection_reason": "quality_gate_failed"},
                "planning_run_id": telemetry_run.id,
            }

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
        for code in selective_repair_codes:
            if code not in format_error_codes:
                format_error_codes.append(code)
        for warning in policy_gate_warnings:
            if warning not in format_error_codes:
                format_error_codes.append(warning)
        parser_trace = list(resolved.get("parser_trace") or [])
        prompt_version_id = str(resolved.get("prompt_version_id") or "")
        planning_profile = str(resolved.get("planning_profile") or "")
        telemetry_run.prompt_version_id = prompt_version_id or telemetry_run.prompt_version_id
        telemetry_run.planning_profile = planning_profile or telemetry_run.planning_profile
        context = resolved["context"]
        template_used = resolved["template_used"]
        # Count selective repair rounds toward repair_attempt_count so the learning loop
        # sees the repair signal even when the LLM strategy itself reported 0 retries.
        _selective_round_count = sum(
            1 for c in selective_repair_codes if c.startswith("selective_repair_rounds:")
        )
        _last_resort_count = sum(
            1 for c in selective_repair_codes if c.startswith("last_resort_ask:")
        )
        effective_repair_count = repair_attempt_count + _selective_round_count + _last_resort_count
        get_planning_telemetry_service().update_run(
            telemetry_run,
            mode_data_patch={"__output_shape__": output_shape, "__parser_trace__": parser_trace},
            raw_output=str(raw_response or ""),
            parse_mode=str(parse_mode or ""),
            parse_confidence=str(parse_confidence or "low"),
            repair_needed=bool(effective_repair_count),
            repair_success=bool(subtasks),
            repair_strategy_used=str(repair_strategy_used or ""),
            repair_attempt_count=effective_repair_count,
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
                telemetry_run = get_planning_telemetry_service().update_run(
                    telemetry_run,
                    validation_success=False,
                    validation_errors=["unstructured_llm_response"],
                    error_classification="unstructured_llm_response",
                    status="failed",
                )
                self._maybe_evolve_prompt(telemetry_run=telemetry_run, planning_policy=planning_policy)
                self._update_profile_learning_state(telemetry_run=telemetry_run)
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

        return _validate_and_finalize_plan(
            service=self,
            planner=planner,
            create_tasks=create_tasks,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
            subtasks=subtasks,
            goal=goal,
            planning_mode=planning_mode,
            raw_response=raw_response,
            context=context,
            planning_origin=planning_origin,
            repair_strategy_used=repair_strategy_used,
            repair_attempt_count=repair_attempt_count,
            parse_mode=parse_mode,
            telemetry_run=telemetry_run,
            flags=flags,
            limits=limits,
            planning_policy=planning_policy,
            planner_selection=planner_selection,
            prompt_version_id=prompt_version_id,
            planning_profile=planning_profile,
            intent=intent,
            template_used=template_used,
            selective_repair_codes=selective_repair_codes,
            format_error_codes=format_error_codes,
            parser_trace=parser_trace,
            mode=mode,
            team_id=team_id,
            parent_task_id=parent_task_id,
            initial_plan_rationale=initial_plan_rationale,
        )

    def get_latest_plan_for_goal(self, goal_id: str) -> tuple[PlanDB | None, list[PlanNodeDB]]:
        repos = get_repository_registry()
        plans = repos.plan_repo.get_by_goal_id(goal_id)
        if not plans:
            return None, []
        plan = plans[0]
        return plan, repos.plan_node_repo.get_by_plan_id(plan.id)

    def get_plan_for_goal(self, goal_id: str, plan_id: str) -> tuple[PlanDB | None, list[PlanNodeDB]]:
        """Return one explicitly addressed plan only when it belongs to the goal."""
        repos = get_repository_registry()
        plan = repos.plan_repo.get_by_id(plan_id)
        if not plan or str(plan.goal_id) != str(goal_id):
            return None, []
        return plan, repos.plan_node_repo.get_by_plan_id(plan.id)

    def patch_plan_node(self, goal_id: str, node_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        plan, _ = self.get_latest_plan_for_goal(goal_id)
        if not plan:
            return None, "plan_not_found"
        return self.patch_plan_node_for_plan(goal_id, plan.id, node_id, payload)

    def patch_plan_node_for_plan(
        self,
        goal_id: str,
        plan_id: str,
        node_id: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Patch an unmaterialized node in one goal-bound persisted plan."""
        normalized_plan_id = str(plan_id or "").strip()
        with self.plan_mutation_lock(
            normalized_plan_id
        ) as distributed_lock_acquired:
            if not distributed_lock_acquired:
                return None, "plan_mutation_in_progress"
            repos = get_repository_registry()
            plan, _ = self.get_plan_for_goal(goal_id, normalized_plan_id)
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
