from __future__ import annotations

from collections import Counter
from collections import defaultdict
from typing import Any

from agent.services.repository_registry import get_repository_registry
from agent.services.planning_telemetry_service import get_planning_telemetry_service


class ModelResponseBehaviorAggregationService:
    @staticmethod
    def _infer_model_family(model_name: str | None) -> str | None:
        normalized = str(model_name or "").strip().lower()
        if "gemma" in normalized:
            return "gemma"
        if "qwen" in normalized:
            return "qwen"
        if "llama" in normalized:
            return "llama"
        return None

    @staticmethod
    def _normalized_distribution(counter: Counter, *, total: int) -> dict[str, float]:
        if total <= 0:
            return {}
        return {k: round(v / total, 4) for k, v in counter.items()}

    @staticmethod
    def _shape_to_output_format(shape: str | None) -> str:
        normalized = str(shape or "").strip().lower()
        if normalized in {"strict_json_array", "strict_json_object", "json_in_markdown_fence", "partial_json", "python_literal"}:
            return "json"
        if normalized in {"markdown_bullets", "numbered_steps"}:
            return "markdown"
        if normalized == "yaml_like":
            return "yaml"
        if normalized == "mermaid_graph":
            return "markdown"
        if normalized in {"freeform_prose", "unknown"}:
            return "text"
        return "json"

    @staticmethod
    def _majority_snapshot(counter: Counter, *, total: int) -> dict[str, Any]:
        if total <= 0 or not counter:
            return {"value": None, "share": 0.0, "state": "unknown"}
        value, count = counter.most_common(1)[0]
        share = round(count / total, 4)
        state = "stable" if total >= 5 and share >= 0.6 else "candidate"
        return {"value": value, "share": share, "state": state}

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
        telemetry = get_planning_telemetry_service()
        shape = Counter()
        output_format = Counter()
        parse = Counter()
        repair = Counter()
        family = Counter()
        family_groups: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "run_count": 0,
            "parse_success_count": 0,
            "validation_success_count": 0,
            "materialization_success_count": 0,
            "repair_count": 0,
            "truncation_count": 0,
            "shape": Counter(),
            "format": Counter(),
            "parse": Counter(),
        })
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
            learning_record = telemetry.build_learning_record(r)
            shp = str(learning_record.get("observed_output_shape") or learning_record.get("output_shape") or "unknown")
            fmt = self._shape_to_output_format(shp)
            shape[shp] += 1
            output_format[fmt] += 1
            parse[str(learning_record.get("parse_mode") or "unknown")] += 1
            repair["repair_needed" if r.repair_needed else "no_repair"] += 1
            model_family = str(
                learning_record.get("model_family")
                or self._infer_model_family(learning_record.get("model_name"))
                or "unknown"
            )
            family[model_family] += 1
            family_item = family_groups[model_family]
            family_item["run_count"] += 1
            family_item["parse_success_count"] += 1 if str(learning_record.get("parse_mode") or "") not in {"", "parse_failed"} else 0
            family_item["validation_success_count"] += 1 if bool(learning_record.get("validation_success")) else 0
            family_item["materialization_success_count"] += 1 if int(learning_record.get("generated_task_count") or 0) > 0 else 0
            family_item["repair_count"] += 1 if bool(r.repair_needed) else 0
            family_item["truncation_count"] += 1 if bool(learning_record.get("truncation_flag")) else 0
            family_item["shape"][shp] += 1
            family_item["format"][fmt] += 1
            family_item["parse"][str(learning_record.get("parse_mode") or "unknown")] += 1

        family_summaries: list[dict[str, Any]] = []
        for model_family, item in family_groups.items():
            count = max(1, int(item["run_count"] or 0))
            parse_success_rate = round(float(item["parse_success_count"]) / float(count), 4)
            repair_rate = round(float(item["repair_count"]) / float(count), 4)
            validation_success_rate = round(float(item["validation_success_count"]) / float(count), 4)
            materialization_success_rate = round(float(item["materialization_success_count"]) / float(count), 4)
            truncation_rate = round(float(item["truncation_count"]) / float(count), 4)
            preferred_output_shape = self._majority_snapshot(item["shape"], total=int(item["run_count"] or 0))
            preferred_output_format = self._majority_snapshot(item["format"], total=int(item["run_count"] or 0))
            preferred_parse_mode = self._majority_snapshot(item["parse"], total=int(item["run_count"] or 0))
            family_summaries.append(
                {
                    "model_family": model_family,
                    "run_count": int(item["run_count"]),
                    "parse_success_rate": parse_success_rate,
                    "repair_rate": repair_rate,
                    "validation_success_rate": validation_success_rate,
                    "materialization_success_rate": materialization_success_rate,
                    "truncation_rate": truncation_rate,
                    "output_shape_distribution": self._normalized_distribution(item["shape"], total=int(item["run_count"] or 0)),
                    "output_format_distribution": self._normalized_distribution(item["format"], total=int(item["run_count"] or 0)),
                    "parse_mode_distribution": self._normalized_distribution(item["parse"], total=int(item["run_count"] or 0)),
                    "preferred_output_shape": preferred_output_shape,
                    "preferred_output_format": preferred_output_format,
                    "preferred_parse_mode": preferred_parse_mode,
                    "behavior_state": "stable" if preferred_output_format.get("state") == "stable" and parse_success_rate >= 0.6 and validation_success_rate >= 0.6 else "candidate",
                }
            )
        family_summaries.sort(key=lambda item: item["run_count"], reverse=True)

        return {
            "observed_run_count": total,
            "primary_output_shape_distribution": self._normalized_distribution(shape, total=total),
            "primary_output_format_distribution": self._normalized_distribution(output_format, total=total),
            "parse_mode_distribution": self._normalized_distribution(parse, total=total),
            "repair_success_distribution": self._normalized_distribution(repair, total=total),
            "model_family_distribution": self._normalized_distribution(family, total=total),
            "preferred_output_shape": self._majority_snapshot(shape, total=total),
            "preferred_output_format": self._majority_snapshot(output_format, total=total),
            "preferred_parse_mode": self._majority_snapshot(parse, total=total),
            "preferred_model_family": self._majority_snapshot(family, total=total),
            "family_behavior_profiles": family_summaries,
        }


_SERVICE = ModelResponseBehaviorAggregationService()


def get_model_response_behavior_aggregation_service() -> ModelResponseBehaviorAggregationService:
    return _SERVICE
