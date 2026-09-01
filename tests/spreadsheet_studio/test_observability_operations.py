from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import text

from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_observability_service import (
    SpreadsheetCorrelation,
    SpreadsheetObservabilityError,
    SpreadsheetObservabilityService,
)
from agent.services.spreadsheet_operations_service import SpreadsheetOperationsService
from tests.spreadsheet_studio.helpers import proposal
from tests.spreadsheet_studio.test_execution_queue import _services


class Metrics:
    def __init__(self) -> None:
        self.increments: list[dict] = []
        self.durations: list[dict] = []
        self.depths: dict[str, int] = {}
        self.alerts: dict[str, bool] = {}

    def increment(self, **values) -> None:
        self.increments.append(values)

    def observe_duration(self, **values) -> None:
        self.durations.append(values)

    def set_queue_depth(self, *, status: str, value: int) -> None:
        self.depths[status] = value

    def set_alert(self, *, reason_code: str, active: bool) -> None:
        self.alerts[reason_code] = active


def test_content_safe_correlation_covers_end_to_end_ids_without_metric_cardinality() -> None:
    metrics = Metrics()
    service = SpreadsheetObservabilityService(metrics=metrics, clock=lambda: 100.0)
    correlation = SpreadsheetCorrelation.from_mapping(
        {
            "trace_id": "trace-one",
            "task_id": "task-one",
            "worker_job_id": "worker-job-one",
            "attempt_id": "attempt-one",
            "document_id": "document-one",
            "candidate_id": "candidate-one",
            "dataset_id": "dataset-one",
            "training_job_id": "training-one",
            "adapter_id": "adapter-one",
        }
    )
    service.record(
        operation="training",
        outcome="completed",
        reason_code="spreadsheet_training_completed",
        correlation=correlation,
        duration_seconds=5.0,
    )

    snapshot = service.snapshot()

    assert snapshot["recent_correlations"][0]["correlation"] == correlation.to_dict()
    assert metrics.increments == [
        {
            "operation": "training",
            "outcome": "completed",
            "reason_code": "spreadsheet_training_completed",
        }
    ]
    assert "trace-one" not in str(metrics.increments)
    with pytest.raises(SpreadsheetObservabilityError, match="fields_invalid"):
        SpreadsheetCorrelation.from_mapping({"trace_id": "trace-one", "raw_cell": "secret"})
    with pytest.raises(SpreadsheetObservabilityError, match="reason_code_invalid"):
        service.record(
            operation="training",
            outcome="failed",
            reason_code="raw cell content is forbidden",
            correlation=correlation,
        )


def test_slo_alerts_backpressure_and_not_run_are_automatic() -> None:
    metrics = Metrics()
    service = SpreadsheetObservabilityService(metrics=metrics)
    service.record(
        operation="queue_wait",
        outcome="completed",
        reason_code="spreadsheet_assignment_claimed",
        correlation=SpreadsheetCorrelation(task_id="task-one"),
        duration_seconds=31.0,
    )
    service.publish_queue_depth({"dispatch_pending": 10, "queued": 15, "leased": 2, "failed": 0})

    snapshot = service.snapshot()

    assert snapshot["status"] == "degraded"
    assert snapshot["backpressure_active"] is True
    assert snapshot["slos"]["queue_wait"]["status"] == "breached"
    assert snapshot["slos"]["training"]["status"] == "not_run"
    assert metrics.alerts["spreadsheet_queue_backpressure"] is True
    assert snapshot["human_intervention_required"] is False


def test_retention_never_deletes_referenced_or_recent_artifacts(tmp_path) -> None:
    store = SpreadsheetArtifactStore(tmp_path / "artifacts")
    old_referenced = b"old-referenced"
    old_orphan = b"old-orphan"
    recent_orphan = b"recent-orphan"
    stored = []
    for content in (old_referenced, old_orphan, recent_orphan):
        digest = hashlib.sha256(content).hexdigest()
        stored.append(
            store.store(
                tenant_id="tenant-one",
                content=content,
                format="xlsx",
                media_type="application/xlsx",
                expected_sha256=digest,
            )
        )
    now = 2_000_000.0
    root = tmp_path / "artifacts"
    paths = sorted(root.glob("*/*/original.xlsx"), key=lambda path: path.parent.name)
    by_digest = {path.parent.name: path for path in paths}
    os.utime(by_digest[stored[0].sha256], (now - 200_000, now - 200_000))
    os.utime(by_digest[stored[1].sha256], (now - 200_000, now - 200_000))
    os.utime(by_digest[stored[2].sha256], (now, now))

    dry_run = store.enforce_retention(
        referenced_digests={stored[0].sha256}, retention_seconds=86_400, now=now
    )
    deleted = store.enforce_retention(
        referenced_digests={stored[0].sha256}, retention_seconds=86_400, delete=True, now=now
    )

    assert dry_run["candidate_count"] == 1 and dry_run["deleted_count"] == 0
    assert deleted["deleted_count"] == 1
    assert by_digest[stored[0].sha256].exists()
    assert not by_digest[stored[1].sha256].exists()
    assert by_digest[stored[2].sha256].exists()


def test_hub_reconciliation_terminalizes_stale_jobs_and_preserves_assignment(tmp_path) -> None:
    engine, queue, submitter, _, _, document = _services(tmp_path)
    queued = submitter.execute_proposal(
        tenant_id="tenant-a", principal_id="owner-a", proposal=proposal(document)
    )
    stale_at = datetime.now(timezone.utc) - timedelta(hours=2)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE spreadsheet_execution_jobs SET updated_at=:stale "
                "WHERE tenant_id='tenant-a' AND job_id=:job_id"
            ),
            {"stale": stale_at, "job_id": queued["job_id"]},
        )
    artifacts = SpreadsheetArtifactStore(tmp_path / "artifacts")
    observer = SpreadsheetObservabilityService(metrics=Metrics())
    service = SpreadsheetOperationsService(
        queue=queue,
        artifacts=artifacts,
        artifact_references=lambda: set(),
        observability=observer,
        stale_seconds=900,
    )

    before = service.snapshot()
    result = service.reconcile(
        max_jobs=10,
        artifact_retention_days=30,
        delete_unreferenced_artifacts=False,
    )

    assert before["recovery"]["recoverable_count"] == 1
    schema = json.loads(Path("schemas/spreadsheet-studio/operations-snapshot.v1.json").read_text())
    Draft202012Validator(schema).validate(before)
    assert result["terminalized_job_ids"] == [queued["job_id"]]
    assert queue.get(tenant_id="tenant-a", job_id=queued["job_id"])["status"] == "failed"
    assert queue.get_assignment(tenant_id="tenant-a", job_id=queued["job_id"])["assignment_digest"] == queued[
        "assignment_digest"
    ]
    assert result["human_intervention_required"] is False
