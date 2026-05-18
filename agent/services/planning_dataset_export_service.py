from __future__ import annotations

from typing import Any

from agent.services.repository_registry import get_repository_registry


_SECRET_KEYS = ("api_key", "token", "password", "secret", "authorization", "credential")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(marker in str(k).lower() for marker in _SECRET_KEYS):
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return value[:1000]
    return value


class PlanningDatasetExportService:
    def export(
        self,
        *,
        limit: int = 200,
        model_provider: str | None = None,
        model_name: str | None = None,
        prompt_version: str | None = None,
        min_generated_tasks: int | None = None,
        include_raw_output: bool = False,
        output_format: str = "json",
    ) -> dict[str, Any]:
        runs = get_repository_registry().planning_run_repo.get_recent(limit=limit)
        rows = []
        for r in runs:
            if model_provider and str(r.model_provider or "") != str(model_provider):
                continue
            if model_name and str(r.model_name or "") != str(model_name):
                continue
            if prompt_version and str(r.prompt_version_id or "") != str(prompt_version):
                continue
            if min_generated_tasks is not None and int(r.generated_task_count or 0) < int(min_generated_tasks):
                continue
            row = {
                "id": str(r.id),
                "goal_id": r.goal_id,
                "trace_id": r.trace_id,
                "mode": r.mode,
                "mode_data": _redact(dict(r.mode_data or {})),
                "model_provider": r.model_provider,
                "model_name": r.model_name,
                "planning_profile": r.planning_profile,
                "prompt_version_id": r.prompt_version_id,
                "parse_mode": r.parse_mode,
                "parse_confidence": r.parse_confidence,
                "parse_warnings": list(r.parse_warnings or []),
                "repair_needed": bool(r.repair_needed),
                "repair_success": bool(r.repair_success),
                "repair_attempt_count": int(r.repair_attempt_count or 0),
                "validation_success": bool(r.validation_success),
                "validation_errors": _redact(list(r.validation_errors or [])),
                "generated_task_count": int(r.generated_task_count or 0),
                "status": r.status,
                "created_at": r.created_at,
            }
            if include_raw_output:
                row["raw_output_preview"] = _redact(str(r.raw_output_preview or ""))
                row["raw_output_ref"] = _redact(str(r.raw_output_ref or ""))
            rows.append(row)
        if str(output_format).strip().lower() == "jsonl":
            import json

            jsonl = "\n".join(json.dumps(item, ensure_ascii=True) for item in rows)
            return {"schema": "planning_dataset_export.v1", "count": len(rows), "format": "jsonl", "jsonl": jsonl}
        return {"schema": "planning_dataset_export.v1", "count": len(rows), "format": "json", "rows": rows}


_SERVICE = PlanningDatasetExportService()


def get_planning_dataset_export_service() -> PlanningDatasetExportService:
    return _SERVICE
