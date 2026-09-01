"""Capability adapter that rejects synchronous execution in production mode."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class QueueBoundSpreadsheetExecutionAdapter:
    @property
    def capability(self) -> Mapping[str, Any]:
        return {
            "state": "available",
            "engine": "hub-task-queue",
            "production_fidelity": True,
            "supported_formats": ["canonical_snapshot", "xlsx", "ods"],
            "execution_mode": "queue_and_lease",
        }

    def dry_run(self, **_: Any) -> Mapping[str, Any]:
        raise RuntimeError("spreadsheet_synchronous_execution_forbidden")

    def import_document(self, **_: Any) -> Mapping[str, Any]:
        raise RuntimeError("spreadsheet_import_queue_required")


__all__ = ["QueueBoundSpreadsheetExecutionAdapter"]
