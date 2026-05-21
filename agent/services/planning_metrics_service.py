from __future__ import annotations

from collections import defaultdict
from typing import Any

from agent.services.repository_registry import get_repository_registry


class PlanningMetricsService:
    @staticmethod
    def _quality_score(*, parse_success_rate: float, repair_rate: float, validation_success_rate: float, materialization_success_rate: float) -> float:
        return round(
            (
                float(parse_success_rate)
                + float(validation_success_rate)
                + float(materialization_success_rate)
                + (1.0 - float(repair_rate))
            )
            / 4.0,
            4,
        )

    def summarize(
        self,
        *,
        model_provider: str | None = None,
        model_name: str | None = None,
        prompt_version: str | None = None,
        output_shape: str | None = None,
        behavior_profile_name: str | None = None,
        group_by_profile: bool = False,
        limit: int = 500,
    ) -> dict[str, Any]:
        rows = get_repository_registry().planning_run_repo.get_recent(limit=limit)
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

        filtered.sort(key=lambda row: float(getattr(row, "created_at", 0.0) or 0.0))
        groups: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "run_count": 0,
            "parse_success_count": 0,
            "repair_count": 0,
            "validation_success_count": 0,
            "materialization_success_count": 0,
            "avg_generated_tasks": 0.0,
            "output_shape_distribution": defaultdict(int),
            "format_error_distribution": defaultdict(int),
            "recent_quality_samples": [],
        })
        for row in filtered:
            if group_by_profile:
                key = f"{row.model_provider or 'unknown'}::{row.model_name or 'unknown'}::{row.planning_profile or 'unknown'}"
            else:
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
            quality = self._quality_score(
                parse_success_rate=1.0 if str(row.parse_mode or "") not in {"", "parse_failed"} else 0.0,
                repair_rate=1.0 if bool(row.repair_needed) else 0.0,
                validation_success_rate=1.0 if bool(row.validation_success) else 0.0,
                materialization_success_rate=1.0 if int(row.generated_task_count or 0) > 0 else 0.0,
            )
            item["recent_quality_samples"].append(quality)

        data = []
        for key, item in groups.items():
            c = max(1, int(item["run_count"]))
            parse_success_rate = round(item["parse_success_count"] / c, 4)
            repair_rate = round(item["repair_count"] / c, 4)
            validation_success_rate = round(item["validation_success_count"] / c, 4)
            materialization_success_rate = round(item["materialization_success_count"] / c, 4)
            quality_score = self._quality_score(
                parse_success_rate=parse_success_rate,
                repair_rate=repair_rate,
                validation_success_rate=validation_success_rate,
                materialization_success_rate=materialization_success_rate,
            )
            recent_samples = list(item["recent_quality_samples"])
            if len(recent_samples) >= 4:
                midpoint = max(1, len(recent_samples) // 2)
                older = recent_samples[:midpoint]
                newer = recent_samples[midpoint:]
                older_avg = round(sum(older) / max(1, len(older)), 4)
                newer_avg = round(sum(newer) / max(1, len(newer)), 4)
                delta = round(newer_avg - older_avg, 4)
                if delta > 0.05:
                    trend_direction = "improving"
                elif delta < -0.05:
                    trend_direction = "degrading"
                else:
                    trend_direction = "stable"
            else:
                trend_direction = "insufficient_data"
            data.append(
                {
                    "group": key,
                    "model_key": "::".join(key.split("::")[:2]) if "::" in key else key,
                    "run_count": item["run_count"],
                    "parse_success_rate": parse_success_rate,
                    "repair_rate": repair_rate,
                    "validation_success_rate": validation_success_rate,
                    "materialization_success_rate": materialization_success_rate,
                    "quality_score": quality_score,
                    "trend_direction": trend_direction,
                    "sample_size_is_small": item["run_count"] < 5,
                    "avg_generated_tasks": round(item["avg_generated_tasks"] / c, 2),
                    "output_shape_distribution": {k: round(v / c, 4) for k, v in item["output_shape_distribution"].items()},
                    "format_error_distribution": {k: round(v / c, 4) for k, v in item["format_error_distribution"].items()},
                    "response_behavior_profile": key.split("::", 2)[2] if group_by_profile and key.count("::") >= 2 else (key.split("::", 1)[1] if "::" in key else None),
                }
            )
        data.sort(key=lambda x: x["run_count"], reverse=True)
        return {"run_count": len(filtered), "groups": data}


_SERVICE = PlanningMetricsService()


def get_planning_metrics_service() -> PlanningMetricsService:
    return _SERVICE
