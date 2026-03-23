from __future__ import annotations

import time
import uuid
from typing import Any


def new_pipeline_trace(
    *,
    pipeline: str,
    task_kind: str | None,
    policy_version: str | None,
    trace_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "trace_id": trace_id or str(uuid.uuid4()),
        "pipeline": pipeline,
        "task_kind": task_kind,
        "policy_version": policy_version,
        "created_at": time.time(),
        "metadata": metadata or {},
        "stages": [],
    }


def append_stage(
    pipeline: dict[str, Any],
    *,
    name: str,
    status: str,
    metadata: dict[str, Any] | None = None,
    started_at: float | None = None,
) -> dict[str, Any]:
    stage_started_at = float(started_at or time.time())
    stage = {
        "name": name,
        "status": status,
        "started_at": stage_started_at,
        "finished_at": time.time(),
        "duration_ms": max(0, int((time.time() - stage_started_at) * 1000)),
        "metadata": metadata or {},
    }
    pipeline.setdefault("stages", []).append(stage)
    pipeline["updated_at"] = stage["finished_at"]
    return stage
