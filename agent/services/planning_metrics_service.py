from __future__ import annotations

from collections import defaultdict
from typing import Any

from agent.services.repository_registry import get_repository_registry


class PlanningMetricsService:
    def summarize(
        self,
        *,
        model_provider: str | None = None,
        model_name: str | None = None,
        prompt_version: str | None = None,
        output_shape: str | None = None,
        behavior_profile_name: str | None = None,
    ) -> dict[str, Any]:
        rows = get_repository_registry().planning_run_repo.get_recent(limit=500)
        filtered = []
        for row in rows:
            if model_provider and str(row.model_provider or "") != str(model_provider):
                continue
            if model_name and str(row.model_name or "") != str(model_name):
                continue
            if prompt_version and str(row.prompt_version_id or "") != str(prompt_version):
                continue
            if output_shape and str((row.mode_data or {}).get("__output_shape__") or "") != str(output_shape):
                continue
            if behavior_profile_name and str(row.planning_profile or "") != str(behavior_profile_name):
                continue
            filtered.append(row)

        groups: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "run_count": 0,
            "parse_success_count": 0,
            "repair_count": 0,
            "validation_success_count": 0,
            "materialization_success_count": 0,
            "avg_generated_tasks": 0.0,
            "output_shape_distribution": defaultdict(int),
            "format_error_distribution": defaultdict(int),
        })
        for row in filtered:
            key = f"{row.model_provider or 'unknown'}::{row.model_name or 'unknown'}"
            item = groups[key]
            item["run_count"] += 1
            item["parse_success_count"] += 1 if str(row.parse_mode or "") not in {"", "parse_failed"} else 0
            item["repair_count"] += 1 if bool(row.repair_needed) else 0
            item["validation_success_count"] += 1 if bool(row.validation_success) else 0
            item["materialization_success_count"] += 1 if int(row.generated_task_count or 0) > 0 else 0
            item["avg_generated_tasks"] += float(row.generated_task_count or 0)
            shape = str((row.mode_data or {}).get("__output_shape__") or "unknown")
            item["output_shape_distribution"][shape] += 1
            for code in list(row.parse_warnings or []):
                item["format_error_distribution"][str(code)] += 1

        data = []
        for key, item in groups.items():
            c = max(1, int(item["run_count"]))
            data.append(
                {
                    "group": key,
                    "run_count": item["run_count"],
                    "parse_success_rate": round(item["parse_success_count"] / c, 4),
                    "repair_rate": round(item["repair_count"] / c, 4),
                    "validation_success_rate": round(item["validation_success_count"] / c, 4),
                    "materialization_success_rate": round(item["materialization_success_count"] / c, 4),
                    "avg_generated_tasks": round(item["avg_generated_tasks"] / c, 2),
                    "output_shape_distribution": {k: round(v / c, 4) for k, v in item["output_shape_distribution"].items()},
                    "format_error_distribution": {k: round(v / c, 4) for k, v in item["format_error_distribution"].items()},
                    "response_behavior_profile": key.split("::", 1)[1] if "::" in key else None,
                }
            )
        data.sort(key=lambda x: x["run_count"], reverse=True)
        return {"run_count": len(filtered), "groups": data}


_SERVICE = PlanningMetricsService()


def get_planning_metrics_service() -> PlanningMetricsService:
    return _SERVICE
