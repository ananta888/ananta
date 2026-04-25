from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def create_patch_plan(
    *,
    task_id: str,
    capability_id: str,
    target_files: list[str],
    expected_effects: list[str],
    context_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_files = [str(item).strip() for item in target_files if str(item).strip()]
    if not normalized_files:
        raise ValueError("target_files_required")
    return {
        "schema": "worker_patch_plan.v1",
        "task_id": str(task_id).strip(),
        "capability_id": str(capability_id).strip(),
        "created_at": datetime.now(UTC).isoformat(),
        "target_files": normalized_files,
        "expected_effects": [str(item).strip() for item in expected_effects if str(item).strip()],
        "context_refs": list(context_refs or []),
        "apply_state": "propose_only",
    }
