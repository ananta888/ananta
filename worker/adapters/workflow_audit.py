"""Execution trace and audit event log for workflow adapters (LCG-018).

Each adapter task (dry_run or execute) gets an isolated audit log so
traces never leak across tasks. WorkflowAuditLog is a thin in-memory
collector flushed to the WorkflowArtifactResult.execution_trace; the
real source of truth for audit events lives in the hub's audit_service.
"""
from __future__ import annotations

import time
from typing import Any


class WorkflowAuditLog:
    """In-memory audit log; flushed to WorkflowArtifactResult.execution_trace."""

    def __init__(self, adapter_id: str) -> None:
        self._adapter_id = adapter_id
        self._entries: list[dict[str, Any]] = []

    def log(self, event: str, **kwargs: Any) -> None:
        self._entries.append({
            "ts": time.monotonic(),
            "adapter_id": self._adapter_id,
            "event": event,
            **kwargs,
        })

    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def snapshot(self) -> list[dict[str, Any]]:
        """Return current entries and reset the log atomically.

        Used at task boundaries so the trace attached to a result is
        exactly the events produced by that task — not a tail of
        preceding tasks sharing the same adapter instance.
        """
        entries = list(self._entries)
        self._entries.clear()
        return entries

    def clear(self) -> None:
        self._entries.clear()