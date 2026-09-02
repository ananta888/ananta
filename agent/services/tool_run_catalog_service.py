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
        source_id: str | None = None,
        run_id: str | None = None,
        evidence_scope: str = "production",
    ) -> dict[str, Any]:
        normalized_scope = str(evidence_scope or "").strip().lower()
        if normalized_scope not in {"test", "local", "external", "production"}:
            raise ValueError("tool_run_evidence_scope_invalid")
        normalized_source_id = str(source_id or "").strip()
        normalized_run_id = str(run_id or "").strip()
        if bool(normalized_source_id) != bool(normalized_run_id):
            raise ValueError("tool_run_authority_binding_incomplete")
        if normalized_source_id:
            from ananta_contracts.recovery_run_evidence import (
                RECOVERY_RUN_SOURCE_ID_PATTERN,
            )

            if (
                RECOVERY_RUN_SOURCE_ID_PATTERN.fullmatch(
                    normalized_source_id
                )
                is None
                or len(normalized_run_id) > 200
            ):
                raise ValueError("tool_run_authority_binding_invalid")
        elif normalized_scope != "test":
            # Production/local tool output is not evidence unless the Hub
            # reserved and transported both identifiers before execution.
            raise ValueError("tool_run_hub_authority_binding_required")
        started = float(started_at if started_at is not None else time.time())
        ended = float(ended_at if ended_at is not None else started)
        return {
            "source_id": (
                normalized_source_id
                or f"RUN_{int(index):04d}"
            ),
            "source_type": "tool_run",
            "task_id": str(task_id),
            "run_id": (
                normalized_run_id
                or f"run-{_h(str(task_id) + ':' + str(index))[:16]}"
            ),
            "tool_name": str(tool_name),
            "command": str(command),
            "exit_code": int(exit_code),
            "stdout_hash": _h(str(stdout or "")),
            "stderr_hash": _h(str(stderr or "")),
            "artifact_paths": [str(p) for p in list(artifact_paths or [])],
            "started_at": started,
            "ended_at": ended,
            "allowed_for_llm_scope": True,
            "evidence_scope": normalized_scope,
        }


_SERVICE = ToolRunCatalogService()


def get_tool_run_catalog_service() -> ToolRunCatalogService:
    return _SERVICE
