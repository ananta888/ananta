from __future__ import annotations

import hashlib
import time
from typing import Any


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:32]


class ToolRunCatalogService:
    """Records deterministic RUN_* evidence entries for tool executions."""

    def build_run_entry(
        self,
        *,
        task_id: str,
        index: int,
        tool_name: str,
        command: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        artifact_paths: list[str] | None = None,
        started_at: float | None = None,
        ended_at: float | None = None,
    ) -> dict[str, Any]:
        started = float(started_at if started_at is not None else time.time())
        ended = float(ended_at if ended_at is not None else started)
        return {
            "source_id": f"RUN_{int(index):04d}",
            "source_type": "tool_run",
            "task_id": str(task_id),
            "run_id": f"run-{_h(str(task_id) + ':' + str(index))[:16]}",
            "tool_name": str(tool_name),
            "command": str(command),
            "exit_code": int(exit_code),
            "stdout_hash": _h(str(stdout or "")),
            "stderr_hash": _h(str(stderr or "")),
            "artifact_paths": [str(p) for p in list(artifact_paths or [])],
            "started_at": started,
            "ended_at": ended,
            "allowed_for_llm_scope": True,
        }


_SERVICE = ToolRunCatalogService()


def get_tool_run_catalog_service() -> ToolRunCatalogService:
    return _SERVICE

