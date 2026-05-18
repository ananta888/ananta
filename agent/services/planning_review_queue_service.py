from __future__ import annotations

import time
from typing import Any

from agent.db_models import PlanningReviewItemDB, PlanningRunDB
from agent.services.repository_registry import get_repository_registry


class PlanningReviewQueueService:
    def evaluate_run_for_review(self, run: PlanningRunDB) -> list[PlanningReviewItemDB]:
        created: list[PlanningReviewItemDB] = []
        repos = get_repository_registry()

        if str(run.parse_mode or "") == "parse_failed":
            recent = repos.planning_run_repo.get_recent(limit=50)
            same_model_fails = [
                r for r in recent
                if str(r.model_provider or "") == str(run.model_provider or "")
                and str(r.model_name or "") == str(run.model_name or "")
                and str(r.parse_mode or "") == "parse_failed"
            ]
            if len(same_model_fails) >= 3:
                created.append(
                    repos.planning_review_item_repo.save(
                        PlanningReviewItemDB(
                            planning_run_id=str(run.id),
                            review_type="repeated_parse_failed",
                            reason_codes=["parse_failed_repeated"],
                            payload={"model_provider": run.model_provider, "model_name": run.model_name, "count": len(same_model_fails)},
                        )
                    )
                )

        if float(run.generated_task_count or 0) <= 0 and str(run.status or "") in {"planned", "materialized"}:
            created.append(
                repos.planning_review_item_repo.save(
                    PlanningReviewItemDB(
                        planning_run_id=str(run.id),
                        review_type="no_artifacts_or_tasks",
                        reason_codes=["no_tasks_generated"],
                        payload={"status": run.status},
                    )
                )
            )

        return created

    def list_open(self, limit: int = 200) -> list[PlanningReviewItemDB]:
        return get_repository_registry().planning_review_item_repo.get_open(limit=limit)

    def apply_action(self, *, item_id: str, action: str, actor: str, details: dict[str, Any] | None = None) -> PlanningReviewItemDB | None:
        repos = get_repository_registry()
        open_items = repos.planning_review_item_repo.get_open(limit=2000)
        item = next((i for i in open_items if str(i.id) == str(item_id)), None)
        if item is None:
            return None
        log = list(item.action_log or [])
        log.append(
            {
                "ts": time.time(),
                "actor": str(actor or "system"),
                "action": str(action or "").strip().lower(),
                "details": dict(details or {}),
            }
        )
        item.action_log = log
        if str(action or "").strip().lower() in {
            "mark_prompt_issue",
            "approve_template_candidate",
            "reject_template_candidate",
            "mark_model_not_recommended",
            "create_followup_todo",
            "close",
        }:
            item.status = "closed"
        item.updated_at = time.time()
        return repos.planning_review_item_repo.save(item)


_SERVICE = PlanningReviewQueueService()


def get_planning_review_queue_service() -> PlanningReviewQueueService:
    return _SERVICE
