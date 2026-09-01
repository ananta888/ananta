"""Hub-owned recovery, retention and operations read model."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_execution_queue_ports import SpreadsheetExecutionQueuePort
from agent.services.spreadsheet_observability_service import (
    SpreadsheetCorrelation,
    SpreadsheetObservabilityService,
)


class SpreadsheetOperationsService:
    """Coordinates operational policy while repositories retain persistence responsibility."""

    def __init__(
        self,
        *,
        queue: SpreadsheetExecutionQueuePort,
        artifacts: SpreadsheetArtifactStore,
        artifact_references: Callable[[], set[str]],
        observability: SpreadsheetObservabilityService,
        stale_seconds: int = 900,
        clock=time.time,
    ) -> None:
        if not 60 <= int(stale_seconds) <= 86_400:
            raise ValueError("spreadsheet_stale_window_invalid")
        self._queue = queue
        self._artifacts = artifacts
        self._artifact_references = artifact_references
        self._observability = observability
        self._stale_seconds = int(stale_seconds)
        self._clock = clock

    def snapshot(self, *, artifact_retention_days: int = 30) -> dict[str, Any]:
        queue = self._queue.operations_summary(stale_before=self._stale_before())
        counts = dict(queue["counts"])
        self._observability.publish_queue_depth(
            {status: int(counts.get(status, 0)) for status in ("dispatch_pending", "queued", "leased", "failed")}
        )
        retention = self._artifacts.enforce_retention(
            referenced_digests=self._artifact_references(),
            retention_seconds=self._retention_seconds(artifact_retention_days),
            delete=False,
            now=float(self._clock()),
        )
        return {
            **self._observability.snapshot(),
            "recovery": {
                "stale_after_seconds": self._stale_seconds,
                "recoverable_count": len(queue["stale_jobs"]),
                "recoverable_jobs": [
                    {
                        "job_id": job["job_id"],
                        "worker_job_id": job.get("worker_job_id"),
                        "document_id": job["document_id"],
                        "status": job["status"],
                    }
                    for job in queue["stale_jobs"]
                ],
            },
            "artifact_retention": retention,
        }

    def reconcile(
        self,
        *,
        max_jobs: int,
        artifact_retention_days: int,
        delete_unreferenced_artifacts: bool,
    ) -> dict[str, Any]:
        if not 1 <= int(max_jobs) <= 100:
            raise ValueError("spreadsheet_recovery_limit_invalid")
        started = float(self._clock())
        recovered = self._queue.terminalize_stale(stale_before=self._stale_before(), limit=int(max_jobs))
        for job in recovered:
            self._observability.record(
                operation="timeout",
                outcome="failed",
                reason_code="spreadsheet_execution_stale_terminalized",
                correlation=SpreadsheetCorrelation(
                    task_id=str(job["job_id"]),
                    worker_job_id=str(job.get("worker_job_id") or job["job_id"]),
                    document_id=str(job["document_id"]),
                ),
            )
        retention = self._artifacts.enforce_retention(
            referenced_digests=self._artifact_references(),
            retention_seconds=self._retention_seconds(artifact_retention_days),
            delete=bool(delete_unreferenced_artifacts),
            now=float(self._clock()),
        )
        self._observability.record(
            operation="cleanup",
            outcome="completed",
            reason_code="spreadsheet_reconciliation_completed",
            correlation=SpreadsheetCorrelation(task_id="spreadsheet-operations-reconciliation"),
            duration_seconds=max(0.0, float(self._clock()) - started),
        )
        return {
            "schema": "ananta.spreadsheet-operations-reconciliation.v1",
            "terminalized_job_count": len(recovered),
            "terminalized_job_ids": [job["job_id"] for job in recovered],
            "artifact_retention": retention,
            "automatic_decision": True,
            "human_intervention_required": False,
        }

    def _stale_before(self) -> datetime:
        return datetime.fromtimestamp(float(self._clock()), tz=timezone.utc) - timedelta(seconds=self._stale_seconds)

    @staticmethod
    def _retention_seconds(days: int) -> int:
        if isinstance(days, bool) or not 1 <= int(days) <= 3_650:
            raise ValueError("spreadsheet_artifact_retention_days_invalid")
        return int(days) * 86_400


__all__ = ["SpreadsheetOperationsService"]
