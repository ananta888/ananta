from __future__ import annotations

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
            if goal_id:
                plans = get_repository_registry().plan_repo.get_by_goal_id(goal_id)
                if plans:
                    nodes = get_repository_registry().plan_node_repo.get_by_plan_id(plans[0].id)
                    subtasks = []
                    for n in nodes:
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
        except Exception:
            pass
        return get_repository_registry().planning_evaluation_repo.save(evaluation)


_SERVICE = PlanningEvaluationService()


def get_planning_evaluation_service() -> PlanningEvaluationService:
    return _SERVICE
