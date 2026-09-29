import contextlib
import hashlib
import logging
import threading
import time

logger = logging.getLogger(__name__)
from typing import Any, Optional

from flask import current_app

import agent.services.planning_materialization_validation as _planning_materialization_validation
import agent.services.planning_plan_materialization as _planning_plan_materialization
import agent.services.planning_plan_persistence as _planning_plan_persistence
import agent.services.planning_plan_repair_helpers as _planning_plan_repair_helpers
from agent.db_models import PlanDB, PlanNodeDB
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.goal_planning_intent_service import get_goal_planning_intent_service

# Patch seam: the extracted materialization module resolves it through this facade.
from agent.services.lifecycle_service import get_task_lifecycle_service  # noqa: F401
from agent.services.llm_first_planning_orchestrator_service import get_llm_first_planning_orchestrator_service

# get_plan_generation_limits is re-exported for goal_service / auto_planner_runtime_service.
from agent.services.planning_feature_flags import (  # noqa: F401
    get_goal_feature_flags,
    get_plan_generation_limits,
)
from agent.services.planning_model_profile_service import get_planning_model_profile_service
from agent.services.planning_prompt_evolver_service import get_planning_prompt_evolver_service
from agent.services.planning_proposal_service import (
    normalize_planning_policy_config,
    select_planning_agent_candidate,
)
from agent.services.planning_quality_service import get_planning_quality_service
from agent.services.planning_service_pipeline import (
    _run_quality_repairs,
    _validate_and_finalize_plan,
    resolve_subtasks_with_timeout,
)
from agent.services.planning_strategies import (
    HubCopilotPlanningStrategy,
    LLMPlanningStrategy,
    PlanningStrategyResult,
    TemplatePlanningStrategy,
)
from agent.services.planning_subtask_sanitizer import sanitize_llm_subtask_policy_hints
from agent.services.planning_telemetry_service import get_planning_telemetry_service
from agent.services.planning_utils import sanitize_input, validate_goal
from agent.services.repository_registry import get_repository_registry


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
        return _planning_plan_repair_helpers.build_minimal_non_llm_fallback_subtask(goal=goal, mode=mode)

    def _compute_plan_depth(self, probe_nodes: list[PlanNodeDB]) -> int:
        return _planning_plan_persistence.compute_plan_depth(self, probe_nodes)

    def _apply_plan_generation_limits(
        self, subtasks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, int], str | None]:
        return _planning_plan_persistence.apply_plan_generation_limits(self, subtasks)

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
        return _planning_plan_repair_helpers.build_selective_repair_prompt(
            goal=goal, mode=mode, missing_categories=missing_categories,
            generic_task_indices=generic_task_indices, preferred_output_format=preferred_output_format,
            required_task_kinds=required_task_kinds, error_codes=error_codes,
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
        return _planning_plan_persistence.build_nodes(self, plan_id, subtasks, planning_mode)

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
        return _planning_plan_persistence.persist_plan(
            self, goal_id, trace_id, subtasks, planning_mode, raw_response, context, planning_origin,
            repair_strategy_used, repair_attempt_count, parse_mode, planning_run_id, initial_rationale,
        )

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
        return _planning_plan_materialization.materialize_plan(
            self, planner, plan, nodes, team_id, parent_task_id, goal_id, goal_trace_id, mode,
            deterministic_task_ids, source_task_id, initial_task_status,
        )

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
        """Materialize one persisted draft after a Hub approval was granted."""
        return _planning_plan_materialization.materialize_existing_plan(
            self, planner=planner, plan_id=plan_id, approval_request_id=approval_request_id, team_id=team_id,
            parent_task_id=parent_task_id, source_task_id=source_task_id,
            expected_plan_digest=expected_plan_digest, initial_task_status=initial_task_status,
        )

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
        return _planning_materialization_validation.task_matches_materialization_binding(
            task, plan=plan, node=node, task_id=task_id, team_id=team_id, parent_task_id=parent_task_id,
            source_task_id=source_task_id, depends_on=depends_on, initial_task_status=initial_task_status,
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
        return _planning_plan_materialization.classify_deterministic_materialization(
            self, plan=plan, staged=staged, team_id=team_id, parent_task_id=parent_task_id,
            source_task_id=source_task_id, initial_task_status=initial_task_status,
        )

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
        return _planning_plan_materialization.restore_retryable_materialization_state(
            self, plan_id=plan_id, error=error, team_id=team_id, parent_task_id=parent_task_id,
            source_task_id=source_task_id, initial_task_status=initial_task_status,
        )

    def _validate_existing_plan_for_materialization(
        self,
        *,
        plan: PlanDB,
        nodes: list[PlanNodeDB],
        team_id: str | None,
    ) -> dict[str, Any]:
        return _planning_materialization_validation.validate_existing_plan_for_materialization(
            self, plan=plan, nodes=nodes, team_id=team_id,
        )

    @staticmethod
    def _dependency_contract_error(
        nodes: list[PlanNodeDB],
    ) -> str | None:
        return _planning_materialization_validation.dependency_contract_error(nodes)

    def _prepare_materialization(
        self,
        nodes: list[PlanNodeDB],
        *,
        deterministic_seed: str | None = None,
    ) -> list[dict[str, Any]] | None:
        return _planning_plan_materialization.prepare_materialization(
            self, nodes, deterministic_seed=deterministic_seed,
        )

    def _rollback_materialization(self, plan: PlanDB | None, nodes: list[PlanNodeDB], created_ids: list[str], error: str) -> None:
        return _planning_plan_materialization.rollback_materialization(self, plan, nodes, created_ids, error)

    @staticmethod
    def _repair_invalid_plan_payload(payload: dict[str, Any], validation_errors: list[str]) -> dict[str, Any]:
        return _planning_plan_repair_helpers.repair_invalid_plan_payload(payload, validation_errors)

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
        # DPRV: advisory injection screen (off by default; shadow records only; never blocks)
        from agent.services.prompt_injection_screening import screen_ingress

        injection_signal = screen_ingress(goal, source="goal_planning")
        if injection_signal is not None and injection_signal.needs_review:
            logging.warning("goal %s: prompt-injection screen %s (%s)", goal_id, injection_signal.verdict,
                            injection_signal.label or injection_signal.reason)

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
