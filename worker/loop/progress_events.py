from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def build_progress_event(
    *,
    task_id: str,
    trace_id: str,
    phase: str,
    iteration: int,
    artifact_refs: list[str] | None = None,
    detail: str = "",
) -> dict[str, Any]:
    refs = [str(item).strip() for item in list(artifact_refs or []) if str(item).strip()]
    return {
        "schema": "worker_progress_event.v1",
        "task_id": str(task_id).strip(),
        "trace_id": str(trace_id).strip(),
        "phase": str(phase).strip(),
        "iteration": int(iteration),
        "artifact_refs": refs,
        "detail": str(detail).strip(),
        "emitted_at": datetime.now(UTC).isoformat(),
    }
