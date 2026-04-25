from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def build_no_llm_fallback_artifact(
    *,
    task_id: str,
    capability_id: str,
    fallback_reason: str,
    candidate_files: list[str],
    constraints: dict[str, Any],
) -> dict[str, Any]:
    normalized_files = [str(path).strip() for path in candidate_files if str(path).strip()]
    return {
        "schema": "worker_no_llm_fallback.v1",
        "task_id": str(task_id).strip(),
        "capability_id": str(capability_id).strip(),
        "llm_used": False,
        "fallback_reason": str(fallback_reason).strip() or "no_model_provider",
        "created_at": datetime.now(UTC).isoformat(),
        "analysis_plan": [
            "Review candidate files and identify probable edit targets.",
            "Propose bounded edit plan artifact without semantic patch generation.",
            "Request model-enabled run for semantic patch proposal if needed.",
        ],
        "candidate_files": normalized_files,
        "constraints_summary": dict(constraints or {}),
        "status": "degraded",
    }
