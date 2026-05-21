from __future__ import annotations

import hashlib
import time
from typing import Any

from agent.db_models import PlanningRunDB
from agent.services.repository_registry import get_repository_registry


class PlanningTelemetryService:
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
    def _hash(value: str | None) -> str | None:
        if not value:
            return None
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_mode_data(run) -> dict[str, Any]:
        return dict(getattr(run, "mode_data", {}) or {})

    def build_learning_record(self, run) -> dict[str, Any]:
        mode_data = self._safe_mode_data(run)
        output_shape = str(mode_data.get("__output_shape__") or "").strip() or None
        parser_trace = mode_data.get("__parser_trace__") if isinstance(mode_data.get("__parser_trace__"), dict) else {}
        truncation_flag = bool(
            mode_data.get("__truncated__")
            or (output_shape == "partial_json")
            or any(str(code).strip().lower() == "truncate" for code in list(getattr(run, "parse_warnings", []) or []))
        )
        return {
            "goal_id": getattr(run, "goal_id", None),
            "trace_id": getattr(run, "trace_id", None),
            "task_id": getattr(run, "task_id", None),
            "mode": getattr(run, "mode", None),
            "model_provider": getattr(run, "model_provider", None),
            "model_name": getattr(run, "model_name", None),
            "model_family": self._infer_model_family(getattr(run, "model_name", None)),
            "planning_profile": getattr(run, "planning_profile", None),
            "prompt_version_id": getattr(run, "prompt_version_id", None),
            "prompt_language": getattr(run, "prompt_language", None),
            "parse_mode": getattr(run, "parse_mode", None),
            "parse_confidence": getattr(run, "parse_confidence", None),
            "output_shape": output_shape,
            "repair_strategy_used": getattr(run, "repair_strategy_used", None),
            "repair_attempt_count": int(getattr(run, "repair_attempt_count", 0) or 0),
            "validation_success": bool(getattr(run, "validation_success", False)),
            "generated_task_count": int(getattr(run, "generated_task_count", 0) or 0),
            "expected_artifacts_count": int(getattr(run, "expected_artifacts_count", 0) or 0),
            "verification_spec_count": int(getattr(run, "verification_spec_count", 0) or 0),
            "dependency_mode_distribution": dict(getattr(run, "dependency_mode_distribution", {}) or {}),
            "materialized_task_count": len(list(getattr(run, "materialized_task_ids", []) or [])),
            "truncation_flag": truncation_flag,
            "parse_warnings": list(getattr(run, "parse_warnings", []) or []),
            "parser_trace": parser_trace,
        }

    def start_run(
        self,
        *,
        goal_id: str | None,
        trace_id: str | None,
        goal_text: str,
        mode: str,
        mode_data: dict[str, Any] | None,
        provider: str | None,
        model_name: str | None,
        model_base_url: str | None,
        planning_profile: str | None,
        prompt_version_id: str | None,
        prompt_language: str | None,
        context_char_count: int,
        status: str = "started",
    ) -> PlanningRunDB:
        run = PlanningRunDB(
            goal_id=goal_id,
            trace_id=trace_id,
            goal_text_hash=self._hash(goal_text),
            goal_text_preview=str(goal_text or "")[:200],
            mode=str(mode or "generic"),
            mode_data=dict(mode_data or {}),
            model_provider=provider,
            model_name=model_name,
            model_base_url_hash=self._hash(model_base_url),
            planning_profile=planning_profile,
            prompt_version_id=prompt_version_id,
            prompt_language=prompt_language,
            context_char_count=max(0, int(context_char_count or 0)),
            status=status,
        )
        return get_repository_registry().planning_run_repo.save(run)

    def update_run(
        self,
        run: PlanningRunDB,
        *,
        mode_data_patch: dict[str, Any] | None = None,
        raw_output: str | None = None,
        parse_mode: str | None = None,
        parse_confidence: str | None = None,
        parse_warnings: list[str] | None = None,
        repair_needed: bool | None = None,
        repair_success: bool | None = None,
        repair_strategy_used: str | None = None,
        repair_attempt_count: int | None = None,
        validation_success: bool | None = None,
        validation_errors: list[str] | None = None,
        generated_task_count: int | None = None,
        expected_artifacts_count: int | None = None,
        verification_spec_count: int | None = None,
        dependency_mode_distribution: dict[str, Any] | None = None,
        materialized_task_ids: list[str] | None = None,
        error_classification: str | None = None,
        status: str | None = None,
    ) -> PlanningRunDB:
        if mode_data_patch:
            merged_mode_data = dict(run.mode_data or {})
            merged_mode_data.update(dict(mode_data_patch))
            run.mode_data = merged_mode_data
        if raw_output is not None:
            run.raw_output_preview = str(raw_output or "")[:1200]
        if parse_mode is not None:
            run.parse_mode = parse_mode
        if parse_confidence is not None:
            run.parse_confidence = parse_confidence
        if parse_warnings is not None:
            run.parse_warnings = list(parse_warnings)
        if repair_needed is not None:
            run.repair_needed = bool(repair_needed)
        if repair_success is not None:
            run.repair_success = bool(repair_success)
        if repair_strategy_used is not None:
            run.repair_strategy_used = repair_strategy_used
        if repair_attempt_count is not None:
            run.repair_attempt_count = max(0, int(repair_attempt_count))
        if validation_success is not None:
            run.validation_success = bool(validation_success)
        if validation_errors is not None:
            run.validation_errors = [str(x) for x in list(validation_errors)]
        if generated_task_count is not None:
            run.generated_task_count = max(0, int(generated_task_count))
        if expected_artifacts_count is not None:
            run.expected_artifacts_count = max(0, int(expected_artifacts_count))
        if verification_spec_count is not None:
            run.verification_spec_count = max(0, int(verification_spec_count))
        if dependency_mode_distribution is not None:
            run.dependency_mode_distribution = dict(dependency_mode_distribution)
        if materialized_task_ids is not None:
            run.materialized_task_ids = [str(x) for x in list(materialized_task_ids)]
        if error_classification is not None:
            run.error_classification = error_classification
        if status is not None:
            run.status = status
        run.updated_at = time.time()
        return get_repository_registry().planning_run_repo.save(run)


_SERVICE = PlanningTelemetryService()


def get_planning_telemetry_service() -> PlanningTelemetryService:
    return _SERVICE
