from __future__ import annotations

import hashlib
import time
from typing import Any

from agent.db_models import PlanningRunDB
from agent.services.repository_registry import get_repository_registry


class PlanningTelemetryService:
    @staticmethod
    def _hash(value: str | None) -> str | None:
        if not value:
            return None
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

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
