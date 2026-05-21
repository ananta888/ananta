from __future__ import annotations

from collections import Counter
from typing import Any

from agent.services.repository_registry import get_repository_registry


class ModelResponseBehaviorAggregationService:
    def aggregate(
        self,
        *,
        provider: str | None = None,
        model_name: str | None = None,
        behavior_profile_name: str | None = None,
        prompt_version: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        runs = get_repository_registry().planning_run_repo.get_recent(limit=limit)
        shape = Counter()
        parse = Counter()
        repair = Counter()
        total = 0
        for r in runs:
            if provider and str(r.model_provider or "") != str(provider):
                continue
            if model_name and str(r.model_name or "") != str(model_name):
                continue
            if behavior_profile_name and str(r.planning_profile or "") != str(behavior_profile_name):
                continue
            if prompt_version and str(r.prompt_version_id or "") != str(prompt_version):
                continue
            total += 1
            shp = str((r.mode_data or {}).get("__output_shape__") or "unknown")
            shape[shp] += 1
            parse[str(r.parse_mode or "unknown")] += 1
            repair["repair_needed" if r.repair_needed else "no_repair"] += 1

        def dist(counter: Counter) -> dict[str, float]:
            if total <= 0:
                return {}
            return {k: round(v / total, 4) for k, v in counter.items()}

        return {
            "observed_run_count": total,
            "primary_output_shape_distribution": dist(shape),
            "parse_mode_distribution": dist(parse),
            "repair_success_distribution": dist(repair),
        }


_SERVICE = ModelResponseBehaviorAggregationService()


def get_model_response_behavior_aggregation_service() -> ModelResponseBehaviorAggregationService:
    return _SERVICE
