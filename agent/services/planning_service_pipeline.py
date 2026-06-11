from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Optional

from flask import current_app, g

from agent.db_models import PlanDB, PlanNodeDB
from agent.routes.tasks.dependency_policy import normalize_depends_on, validate_dependency_graph
from agent.services.lifecycle_service import get_task_lifecycle_service
from agent.services.planning_feature_flags import get_goal_feature_flags, get_plan_generation_limits
from agent.services.planning_proposal_service import (
    build_plan_proposal,
    select_planning_agent_candidate,
    validate_plan_proposal_payload,
)
from agent.services.planning_quality_service import get_planning_quality_service
from agent.services.planning_review_queue_service import get_planning_review_queue_service
from agent.services.planning_telemetry_service import get_planning_telemetry_service
from agent.services.planning_template_mining_service import get_planning_template_mining_service
from agent.services.repository_registry import get_repository_registry
from agent.services.planning_evaluation_service import get_planning_evaluation_service
from agent.services.planning_utils import parse_subtasks_from_llm_response

logger = logging.getLogger(__name__)


def resolve_subtasks_with_timeout(
    service,
    planner,
    goal: str,
    context: Optional[str],
    use_template: bool,
    use_repo_context: bool,
    mode: str,
    mode_data: Optional[dict],
    goal_id: Optional[str],
    goal_trace_id: Optional[str],
    telemetry_run: Any,
    planning_policy: dict[str, Any],
) -> dict[str, Any]:
    inner_timeout = max(30, int(planning_policy.get("timeout_seconds") or 300))
    app_obj = current_app._get_current_object()

    def _resolve_with_app_context() -> dict[str, Any]:
        with app_obj.app_context():
            g.llm_goal_id = str(goal_id or "") or None
            g.llm_task_id = None
            return service._resolve_subtasks(
                planner=planner,
                goal=goal,
                context=context,
                use_template=use_template,
                use_repo_context=use_repo_context,
                mode=mode,
                mode_data=mode_data,
                planning_policy=planning_policy,
            )

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(_resolve_with_app_context)
        resolved = future.result(timeout=inner_timeout)
    except FutureTimeoutError:
        planner._stats["errors"] += 1
        telemetry_run = get_planning_telemetry_service().update_run(
            telemetry_run,
            status="failed",
            error_classification="resolve_subtasks_timeout",
            validation_errors=[f"resolve_subtasks_timeout:{inner_timeout}s"],
        )
        service._maybe_evolve_prompt(telemetry_run=telemetry_run, planning_policy=planning_policy)
        service._update_profile_learning_state(telemetry_run=telemetry_run)
        return {
            "subtasks": [],
            "created_task_ids": [],
            "error": "resolve_subtasks_timeout",
            "planning_run_id": telemetry_run.id,
            "error_classification": "resolve_subtasks_timeout",
        }
    except Exception as exc:
        planner._stats["errors"] += 1
        if isinstance(exc, RecursionError):
            error_message = "planning_recursion_guard_triggered"
            error_classification = "planning_recursion_error"
        else:
            error_message = str(exc)
            error_classification = "resolve_subtasks_exception"
        telemetry_run = get_planning_telemetry_service().update_run(
            telemetry_run,
            status="failed",
            error_classification=error_classification,
            validation_errors=[error_message],
        )
        service._maybe_evolve_prompt(telemetry_run=telemetry_run, planning_policy=planning_policy)
        service._update_profile_learning_state(telemetry_run=telemetry_run)
        return {"subtasks": [], "created_task_ids": [], "error": error_message, "planning_run_id": telemetry_run.id}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    resolved["_telemetry_run"] = telemetry_run
    return resolved


def _run_selective_repair(
    service,
    planner,
    subtasks: list[dict[str, Any]],
    goal: str,
    mode: str,
    planning_policy: dict[str, Any],
    team_id: Optional[str],
    scoped_llm_cfg: dict[str, Any],
    goal_id: Optional[str],
    quality: Any,
    repair_context: dict[str, Any],
    preferred_output_format: str,
    perform_quality_repairs: bool,
    enforce_quality_gate: bool,
    create_tasks: bool,
) -> dict[str, Any]:
    selective_repair_codes: list[str] = []
    required_task_kinds: list[str] = []
    repair_error_codes: list[str] = []

    if repair_context:
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

    _sr = planning_policy.get("selective_repair_rounds")
    selective_rounds = max(0, min(int(_sr if _sr is not None else 2), 4))
    selective_repair_applied = 0

    while perform_quality_repairs and (not quality.ok) and selective_rounds > 0:
        selective_rounds -= 1
        selective_repair_applied += 1
        repair_prompt = service._build_selective_repair_prompt(
            goal=goal,
            mode=mode,
            missing_categories=quality.missing_categories,
            generic_task_indices=quality.generic_task_indices,
            preferred_output_format=preferred_output_format,
            required_task_kinds=required_task_kinds if required_task_kinds else None,
            error_codes=repair_error_codes if repair_error_codes else None,
        )
        had_llm_goal_id = hasattr(g, "llm_goal_id")
        had_llm_task_id = hasattr(g, "llm_task_id")
        prev_llm_goal_id = getattr(g, "llm_goal_id", None)
        prev_llm_task_id = getattr(g, "llm_task_id", None)
        try:
            g.llm_goal_id = str(goal_id or "").strip() or None
            g.llm_task_id = None
            repair_resp = planner._call_llm_with_retry(repair_prompt, scoped_llm_cfg, temperature=0.1)
        finally:
            if had_llm_goal_id:
                g.llm_goal_id = prev_llm_goal_id
            else:
                try:
                    delattr(g, "llm_goal_id")
                except Exception:
                    pass
            if had_llm_task_id:
                g.llm_task_id = prev_llm_task_id
            else:
                try:
                    delattr(g, "llm_task_id")
                except Exception:
                    pass
        repair_subtasks = parse_subtasks_from_llm_response(repair_resp, default_priority=planner.default_priority)
        if not repair_subtasks:
            break
        missing_category_names = {
            str(item).split(":", 1)[0].strip().lower()
            for item in list(quality.missing_categories or [])
            if str(item).strip()
        }
        if missing_category_names:
            qs = get_planning_quality_service()
            focused_subtasks = []
            for candidate in list(repair_subtasks or []):
                cat = str(qs._classify_task_category(candidate)).strip().lower()
                if cat in missing_category_names:
                    focused_subtasks.append(candidate)
            if focused_subtasks:
                repair_subtasks = focused_subtasks
        seen = {
            (str(t.get("title") or "").strip().lower(), str(t.get("description") or "").strip().lower())
            for t in subtasks
        }
        for candidate in repair_subtasks:
            key = (
                str(candidate.get("title") or "").strip().lower(),
                str(candidate.get("description") or "").strip().lower(),
            )
            if key not in seen:
                subtasks.append(dict(candidate))
                seen.add(key)
        quality = get_planning_quality_service().evaluate(
            subtasks=subtasks,
            mode=mode,
            planning_policy=planning_policy,
            team_id=team_id,
        )

    if selective_repair_applied:
        selective_repair_codes.append(f"selective_repair_rounds:{selective_repair_applied}")

    return {
        "subtasks": subtasks,
        "quality": quality,
        "selective_repair_codes": selective_repair_codes,
        "selective_repair_applied": selective_repair_applied,
    }


def _run_quality_repairs(
    service,
    planner,
    subtasks: list[dict[str, Any]],
    goal: str,
    mode: str,
    planning_policy: dict[str, Any],
    team_id: Optional[str],
    scoped_llm_cfg: dict[str, Any],
    goal_id: Optional[str],
    quality: Any,
    repair_context: dict[str, Any],
    preferred_output_format: str,
    perform_quality_repairs: bool,
    enforce_quality_gate: bool,
    create_tasks: bool,
) -> dict[str, Any]:
    result = _run_selective_repair(
        service, planner, subtasks, goal, mode, planning_policy,
        team_id, scoped_llm_cfg, goal_id, quality, repair_context,
        preferred_output_format, perform_quality_repairs, enforce_quality_gate, create_tasks,
    )
    subtasks = result["subtasks"]
    quality = result["quality"]
    selective_repair_codes = result["selective_repair_codes"]
    selective_repair_applied = result["selective_repair_applied"]

    if selective_repair_applied:
        selective_repair_codes.append(f"selective_repair_rounds:{selective_repair_applied}")

    result = _run_last_resort_asks(
        service, planner, subtasks, goal, perform_quality_repairs,
        quality, scoped_llm_cfg, selective_repair_codes, planning_policy,
        mode, team_id,
    )

    return {
        "subtasks": result["subtasks"],
        "quality": result["quality"],
        "selective_repair_codes": result["selective_repair_codes"],
    }


def _run_last_resort_asks(
    service,
    planner,
    subtasks: list[dict[str, Any]],
    goal: str,
    perform_quality_repairs: bool,
    quality: Any,
    scoped_llm_cfg: dict[str, Any],
    selective_repair_codes: list[str],
    planning_policy: dict[str, Any],
    mode: str,
    team_id: Optional[str],
) -> dict[str, Any]:
    _task_gap = max(0, quality.details.get("min_total", 0) - len(subtasks)) if not quality.ok else 0
    _only_missing_cats = (
        perform_quality_repairs
        and not quality.ok
        and quality.missing_categories
        and (
            not any(r.startswith("too_few_tasks:") for r in (quality.reason or "").split("|"))
            or len(quality.missing_categories) >= _task_gap
        )
    )
    if _only_missing_cats:
        _TASK_KIND_FOR_CAT = {
            "infrastructure": "ops",
            "implementation": "coding",
            "tests": "testing",
            "analysis": "analysis",
            "review": "review",
        }
        for _missing_entry in list(quality.missing_categories or []):
            _cat = str(_missing_entry).split(":", 1)[0].strip().lower()
            _tk = _TASK_KIND_FOR_CAT.get(_cat, _cat)
            _targeted_prompt = (
                f"The plan is missing exactly 1 task for category '{_cat}'.\n"
                f"GOAL: {goal}\n"
                f"Return a JSON array with EXACTLY 1 task. Use task_kind='{_tk}'.\n"
                f"Category '{_cat}' examples: "
                + {
                    "infrastructure": "Dockerfile, docker-compose.yml, CI/CD pipeline, environment setup",
                    "implementation": "core service code, API endpoint, business logic module",
                    "tests": "unit tests, integration tests, test fixtures",
                    "analysis": "requirements analysis, architecture decision, spike",
                    "review": "code review checklist, documentation, changelog",
                }.get(_cat, _cat)
                + f"\nExample: "
                + '["'
                + '{"title": "Setup Docker environment", "description": "Create Dockerfile and docker-compose.yml", '
                + f'"task_kind": "{_tk}", "priority": "medium"'
                + '}"]'
                + "\nReturn ONLY the JSON array. No explanation."
            )
            try:
                _resp = planner._call_llm_with_retry(_targeted_prompt, scoped_llm_cfg, temperature=0.1)
                _new = parse_subtasks_from_llm_response(_resp, default_priority=planner.default_priority)
                qs = get_planning_quality_service()
                _seen = {
                    (str(t.get("title") or "").strip().lower(), str(t.get("description") or "").strip().lower())
                    for t in subtasks
                }
                for _c in (_new or []):
                    if qs._classify_task_category(_c).strip().lower() == _cat:
                        _k = (str(_c.get("title") or "").strip().lower(), str(_c.get("description") or "").strip().lower())
                        if _k not in _seen:
                            subtasks.append(dict(_c))
                            _seen.add(_k)
                            selective_repair_codes.append(f"last_resort_ask:{_cat}")
            except Exception:
                pass
        quality = get_planning_quality_service().evaluate(
            subtasks=subtasks,
            mode=mode,
            planning_policy=planning_policy,
            team_id=team_id,
        )

    return {
        "subtasks": subtasks,
        "quality": quality,
        "selective_repair_codes": selective_repair_codes,
    }


def _validate_and_finalize_plan(
    service,
    planner,
    create_tasks: bool,
    goal_id: Optional[str],
    goal_trace_id: Optional[str],
    subtasks: list[dict[str, Any]],
    goal: str,
    planning_mode: str,
    raw_response: Optional[str],
    context: Optional[str],
    planning_origin: str,
    repair_strategy_used: Optional[str],
    repair_attempt_count: int,
    parse_mode: Optional[str],
    telemetry_run: Any,
    flags: dict[str, Any],
    limits: dict[str, Any],
    planning_policy: dict[str, Any],
    planner_selection: dict[str, Any],
    prompt_version_id: Optional[str],
    planning_profile: Optional[str],
    intent: Optional[str],
    template_used: bool,
    selective_repair_codes: list[str],
    format_error_codes: list[str],
    parser_trace: list[str],
    mode: str,
    team_id: Optional[str],
    parent_task_id: Optional[str],
) -> dict[str, Any]:
    proposal_payload = build_plan_proposal(
        goal_id=str(goal_id or "").strip() or "ad-hoc-goal",
        trace_id=str(goal_trace_id or "").strip() or f"trace-{uuid.uuid4().hex[:8]}",
        summary=goal,
        subtasks=subtasks,
        required_capabilities=[],
    )
    known_capabilities = {
        "planning", "coding", "testing", "review", "research",
        "ops", "analysis", "doc", "plan.propose", "risk.estimate",
        "dependencies.suggest", "clarifying_questions.suggest",
    }
    proposal_validation = validate_plan_proposal_payload(proposal_payload, known_capabilities=known_capabilities)
    if not proposal_validation.ok:
        repaired_payload = service._repair_invalid_plan_payload(
            proposal_validation.normalized_payload, proposal_validation.errors
        )
        repaired_validation = validate_plan_proposal_payload(repaired_payload, known_capabilities=known_capabilities)
        if repaired_validation.ok:
            proposal_payload = repaired_validation.normalized_payload
            proposal_validation = repaired_validation
    if not proposal_validation.ok:
        planner._stats["errors"] += 1
        telemetry_run = get_planning_telemetry_service().update_run(
            telemetry_run,
            validation_success=False,
            validation_errors=list(proposal_validation.errors or []),
            error_classification="invalid_plan_proposal",
            status="failed",
        )
        service._maybe_evolve_prompt(telemetry_run=telemetry_run, planning_policy=planning_policy)
        service._update_profile_learning_state(telemetry_run=telemetry_run)
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
        plan, nodes = service._persist_plan(
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
        if goal_id:
            existing_goal_task_ids = [
                str(getattr(task, "id", "") or "")
                for task in get_repository_registry().task_repo.get_all()
                if str(getattr(task, "goal_id", "") or "").strip() == str(goal_id).strip()
            ]
            existing_goal_task_ids = [tid for tid in existing_goal_task_ids if tid]
            if existing_goal_task_ids:
                get_planning_telemetry_service().update_run(
                    telemetry_run,
                    validation_success=True,
                    validation_errors=["materialization_skipped_existing_goal_tasks"],
                    generated_task_count=len(existing_goal_task_ids),
                    materialized_task_ids=existing_goal_task_ids,
                    status="materialized",
                )
                return {
                    "subtasks": subtasks,
                    "created_task_ids": existing_goal_task_ids,
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
        materialize_nodes = nodes or service._build_nodes(goal_id or "ad-hoc-plan", subtasks, planning_mode)
        created_ids, materialization_error = service._materialize_plan(
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
    except Exception as exc:
        logger.warning("evaluate_run_for_review failed: %s", exc)
    service._maybe_evolve_prompt(telemetry_run=telemetry_run, planning_policy=planning_policy)
    service._update_profile_learning_state(telemetry_run=telemetry_run)
    get_planning_evaluation_service().evaluate(
        planning_run_id=str(telemetry_run.id),
        goal_id=goal_id,
        trace_id=goal_trace_id,
    )
    try:
        get_planning_template_mining_service().mine_candidates(min_total_score=0.85, limit=30)
    except Exception as exc:
        logger.warning("mine_candidates failed: %s", exc)

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
