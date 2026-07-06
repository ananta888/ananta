from __future__ import annotations

import logging

from agent.db_models import PlanningEvaluationDB
from agent.services.repository_registry import get_repository_registry
from agent.services.planning_semantic_behavior_analyzer import analyze_semantic_behavior


class PlanningEvaluationService:
    def evaluate(self, *, planning_run_id: str, goal_id: str | None, trace_id: str | None) -> PlanningEvaluationDB:
        run = get_repository_registry().planning_run_repo.get_by_id(planning_run_id)
        if run is None:
            return get_repository_registry().planning_evaluation_repo.save(
                PlanningEvaluationDB(
                    planning_run_id=planning_run_id,
                    goal_id=goal_id,
                    trace_id=trace_id,
                    completion_status="failed",
                    failure_reason="planning_run_not_found",
                )
            )

        parse_score = 1.0 if str(run.parse_mode or "") not in {"", "parse_failed"} else 0.0
        validation_score = 1.0 if run.validation_success else 0.0
        materialization_score = 1.0 if (run.generated_task_count or 0) > 0 else 0.0
        artifact_score = 1.0 if (run.expected_artifacts_count or 0) > 0 else 0.0
        verification_score = 1.0 if (run.verification_spec_count or 0) > 0 else 0.0
        execution_score = 1.0 if run.status in {"materialized", "completed"} else 0.0
        total = round(
            (parse_score + validation_score + materialization_score + execution_score + artifact_score + verification_score)
            / 6.0,
            4,
        )

        completion_status = "completed" if total >= 0.7 else "partial"
        failure_reason = None if completion_status == "completed" else (run.error_classification or "low_total_score")

        evaluation = get_repository_registry().planning_evaluation_repo.get_by_run_id(planning_run_id) or PlanningEvaluationDB(
            planning_run_id=planning_run_id,
            goal_id=goal_id,
            trace_id=trace_id,
        )
        evaluation.parse_score = parse_score
        evaluation.validation_score = validation_score
        evaluation.materialization_score = materialization_score
        evaluation.execution_score = execution_score
        evaluation.artifact_score = artifact_score
        evaluation.verification_score = verification_score
        evaluation.total_score = total
        evaluation.completion_status = completion_status
        evaluation.failure_reason = failure_reason
        evaluation.details = {
            "generated_task_count": int(run.generated_task_count or 0),
            "parse_mode": run.parse_mode,
            "repair_attempt_count": int(run.repair_attempt_count or 0),
        }
        try:
            semantic_codes: list[str] = []
            quality_texts: list[str] = []
            if goal_id:
                plans = get_repository_registry().plan_repo.get_by_goal_id(goal_id)
                if plans:
                    nodes = get_repository_registry().plan_node_repo.get_by_plan_id(plans[0].id)
                    subtasks = []
                    for n in nodes:
                        quality_texts.extend(
                            value
                            for value in (str(n.title or ""), str(n.description or ""))
                            if value.strip()
                        )
                        subtasks.append(
                            {
                                "title": n.title,
                                "description": n.description,
                                "task_kind": (n.rationale or {}).get("task_kind"),
                                "depends_on": list(n.depends_on or []),
                                "dependency_mode": (n.rationale or {}).get("dependency_mode"),
                                "expected_artifacts": list((n.rationale or {}).get("expected_artifacts") or []),
                                "verification_spec": dict(n.verification_spec or {}),
                            }
                        )
                    semantic_codes = analyze_semantic_behavior(subtasks=subtasks)
            evaluation.details = {**dict(evaluation.details or {}), "semantic_behavior_codes": semantic_codes}
            from flask import current_app, has_app_context

            cfg = current_app.config.get("AGENT_CONFIG", {}) if has_app_context() else {}
            tq_cfg = cfg.get("text_quality") if isinstance(cfg.get("text_quality"), dict) else {}
            if tq_cfg.get("enabled") and tq_cfg.get("evaluate_planning_outputs") and quality_texts:
                from agent.services.text_quality.models import ContentKind
                from agent.services.text_quality.runtime_service import (
                    get_text_quality_runtime_service,
                )

                result, row = get_text_quality_runtime_service().evaluate(
                    text="\n".join(quality_texts),
                    language=str(run.prompt_language or "de"),
                    content_kind=ContentKind.PLANNING_TASK_DESCRIPTION,
                    planning_run_id=str(run.id),
                    planning_evaluation_id=str(evaluation.id),
                    prompt_version_id=run.prompt_version_id,
                )
                text_quality_summary = {
                    "evaluation_id": row.id,
                    "status": result.status.value,
                    "slop_score": result.slop_score,
                    "depth_score": result.depth_score,
                    "style_fit_score": result.style_fit_score,
                    "reason_codes": result.reason_codes,
                    "criteria_version": result.criteria_version,
                    "evaluator_version": result.evaluator_version,
                    "content_kind": result.content_kind.value,
                    "language": result.language,
                }
                evaluation.details = {
                    **dict(evaluation.details or {}),
                    "text_quality": text_quality_summary,
                }
                run.mode_data = {
                    **dict(run.mode_data or {}),
                    "__text_quality__": text_quality_summary,
                }
                get_repository_registry().planning_run_repo.save(run)
        except Exception:
            logging.exception(
                "text_quality_planning_evaluation_degraded",
                extra={"planning_run_id": planning_run_id},
            )
        return get_repository_registry().planning_evaluation_repo.save(evaluation)


_SERVICE = PlanningEvaluationService()


def get_planning_evaluation_service() -> PlanningEvaluationService:
    return _SERVICE
