from __future__ import annotations

from collections import Counter
from typing import Any

from agent.db_models import PlanningPatternClusterDB, PlanningTemplateCandidateDB
from agent.services.repository_registry import get_repository_registry


class PlanningTemplateMiningService:
    def mine_candidates(self, *, min_total_score: float = 0.7, limit: int = 200) -> dict[str, Any]:
        repos = get_repository_registry()
        runs = repos.planning_run_repo.get_recent(limit=limit)
        created_candidates = 0
        cluster_counter: Counter[str] = Counter()

        for run in runs:
            eval_row = repos.planning_evaluation_repo.get_by_run_id(str(run.id))
            if eval_row is None:
                continue
            if float(eval_row.total_score or 0.0) < float(min_total_score):
                continue
            if int(run.generated_task_count or 0) <= 0:
                continue

            goal_type = str(((run.mode_data or {}).get("__intent__") or {}).get("goal_type") or "generic")
            candidate_payload = {
                "goal_type": goal_type,
                "mode": run.mode,
                "parse_mode": run.parse_mode,
                "repair_strategy_used": run.repair_strategy_used,
                "dependency_mode_distribution": dict(run.dependency_mode_distribution or {}),
                "generated_task_count": int(run.generated_task_count or 0),
                "prompt_version_id": run.prompt_version_id,
                "planning_profile": run.planning_profile,
            }
            repos.planning_template_candidate_repo.save(
                PlanningTemplateCandidateDB(
                    source_run_id=str(run.id),
                    goal_type=goal_type,
                    mode=str(run.mode or "generic"),
                    candidate_payload=candidate_payload,
                    confidence="high" if float(eval_row.total_score or 0.0) >= 0.85 else "medium",
                    status="proposed",
                )
            )
            created_candidates += 1
            cluster_key = f"{goal_type}::{run.mode}::{run.parse_mode or 'unknown'}"
            cluster_counter[cluster_key] += 1

        for key, count in cluster_counter.items():
            goal_type, mode, parse_mode = key.split("::", 2)
            repos.planning_pattern_cluster_repo.save(
                PlanningPatternClusterDB(
                    goal_type=goal_type,
                    model_provider=None,
                    model_name=None,
                    cluster_key=key,
                    cluster_payload={"mode": mode, "parse_mode": parse_mode, "source": "planning_template_mining"},
                    sample_count=int(count),
                )
            )

        return {
            "scanned_runs": len(runs),
            "created_candidates": created_candidates,
            "created_clusters": len(cluster_counter),
        }


_SERVICE = PlanningTemplateMiningService()


def get_planning_template_mining_service() -> PlanningTemplateMiningService:
    return _SERVICE
