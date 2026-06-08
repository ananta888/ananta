"""Execution trace and audit event log for workflow adapters (LCG-018)."""
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

    def clear(self) -> None:
        self._entries.clear()
