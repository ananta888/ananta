from __future__ import annotations

from dataclasses import replace

from ananta_contracts.model_intelligence import ArtifactRef
from agent.services.model_intelligence_metrics_adapter import InProcessOpenMetricsAdapter
from agent.services.model_intelligence_observability import (
    HmacModelIntelligenceCorrelationService,
    ModelIntelligenceMetricPoint,
    ModelIntelligenceQuotaLimits,
    ModelIntelligenceQuotaPolicy,
    ModelIntelligenceResourceRequest,
    TenantResourceSnapshot,
)
from agent.services.model_intelligence_retention_adapter import (
    SqliteModelIntelligenceRetentionAdapter,
)
from agent.services.model_intelligence_security_policy import (
    ModelIntelligenceAccessPolicy,
    ModelIntelligenceAction,
    ModelIntelligencePrincipal,
    ModelIntelligenceResourceKind,
    ModelIntelligenceResourceScope,
    ModelIntelligenceRetentionPolicy,
    ModelIntelligenceRetentionRecord,
    ModelIntelligenceRole,
    RetentionCause,
    RetentionClass,
    RetentionState,
)
from worker.model_intelligence.metrics import WorkerModelIntelligenceMetrics
from worker.model_intelligence.openmetrics import WorkerInProcessOpenMetricsPort


def _artifact() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="artifact-integration-1",
        job_id="job-integration-1",
        kind="analysis.report",
        sha256="e" * 64,
        size_bytes=512,
        media_type="application/json",
    )


def test_authorized_quota_checked_job_correlates_worker_and_persisted_retention(
    tmp_path,
) -> None:
    artifact = _artifact()
    principal = ModelIntelligencePrincipal(
        "subject-1",
        "tenant-a",
        frozenset({ModelIntelligenceRole.OPERATOR}),
    )
    resource = ModelIntelligenceResourceScope(
        "tenant-a",
        ModelIntelligenceResourceKind.ARTIFACT,
        artifact.artifact_id,
    )
    access = ModelIntelligenceAccessPolicy().decide(
        principal,
        resource,
        ModelIntelligenceAction.DELETE_ARTIFACT,
    )
    quota = ModelIntelligenceQuotaPolicy(
        ModelIntelligenceQuotaLimits(
            max_disk_bytes=1024,
            max_ram_bytes=1024,
            max_vram_bytes=0,
            max_parallel_jobs=1,
            max_artifact_bytes=1024,
        )
    ).decide(
        TenantResourceSnapshot("tenant-a"),
        ModelIntelligenceResourceRequest(
            "tenant-a",
            disk_bytes=512,
            artifact_bytes=512,
        ),
    )
    correlation = HmacModelIntelligenceCorrelationService(b"k" * 32).correlate(
        hub_job_id=artifact.job_id,
        worker_task_id="worker-task-1",
        artifact_ref=artifact,
    )

    worker_metrics = WorkerInProcessOpenMetricsPort()
    WorkerModelIntelligenceMetrics(worker_metrics).record_job_state(
        state="succeeded",
        reason_code="accepted",
        analysis_kind="analysis.static",
        correlation_id=correlation.correlation_id,
        duration_seconds=0.25,
    )

    record = ModelIntelligenceRetentionRecord(
        tenant_id="tenant-a",
        artifact_ref=artifact,
        retention_class=RetentionClass.STANDARD,
        created_at_epoch_seconds=100,
        retain_until_epoch_seconds=200,
    )
    store = SqliteModelIntelligenceRetentionAdapter(tmp_path / "retention.sqlite3")
    store.register(record)
    policy = ModelIntelligenceRetentionPolicy()
    pending = policy.plan_deletion(
        record,
        requesting_tenant_id="tenant-a",
        idempotency_key="integration-delete-1",
        now_epoch_seconds=300,
        cause=RetentionCause.RETENTION_EXPIRED,
    )
    pending_record = store.apply(
        pending,
        tenant_id="tenant-a",
        recorded_at_epoch_seconds=301,
    )
    deleted = policy.confirm_deletion(
        replace(record, state=pending_record.state),
        requesting_tenant_id="tenant-a",
        idempotency_key="integration-delete-2",
    )
    deleted_record = store.apply(
        deleted,
        tenant_id="tenant-a",
        recorded_at_epoch_seconds=302,
    )

    assert access.allowed is True
    assert quota.allowed is True
    assert deleted_record.state is RetentionState.DELETED
    assert len(store.history(tenant_id="tenant-a", artifact_id=artifact.artifact_id)) == 2
    assert (
        SqliteModelIntelligenceRetentionAdapter(tmp_path / "retention.sqlite3")
        .get(tenant_id="tenant-a", artifact_id=artifact.artifact_id)
        .state
        is RetentionState.DELETED
    )
    rendered = worker_metrics.render_openmetrics()
    assert "model_intelligence_jobs_total" in rendered
    assert artifact.job_id not in rendered
    assert artifact.artifact_id not in rendered


def test_hub_openmetrics_adapter_accumulates_counters_and_renders_histograms() -> None:
    metrics = InProcessOpenMetricsAdapter(histogram_buckets=(0.1, 1.0))
    labels = {
        "analysis_kind": "analysis.static",
        "reason_code": "accepted",
        "state": "succeeded",
    }

    metrics.observe(ModelIntelligenceMetricPoint("model_intelligence_jobs_total", 1, labels))
    metrics.observe(ModelIntelligenceMetricPoint("model_intelligence_jobs_total", 1, labels))
    metrics.observe(
        ModelIntelligenceMetricPoint(
            "model_intelligence_job_duration_seconds",
            0.25,
            labels,
        )
    )
    output = metrics.render_openmetrics()

    assert 'model_intelligence_jobs_total{analysis_kind="analysis.static"' in output
    assert "} 2" in output
    assert 'model_intelligence_job_duration_seconds_bucket{' in output
    assert 'le="+Inf"' in output
    assert output.endswith("# EOF\n")


def test_retention_store_never_reveals_foreign_tenant_records(tmp_path) -> None:
    record = ModelIntelligenceRetentionRecord(
        tenant_id="tenant-a",
        artifact_ref=_artifact(),
        retention_class=RetentionClass.STANDARD,
        created_at_epoch_seconds=1,
        retain_until_epoch_seconds=2,
    )
    store = SqliteModelIntelligenceRetentionAdapter(tmp_path / "retention.sqlite3")
    store.register(record)

    assert store.get(tenant_id="tenant-b", artifact_id=record.artifact_ref.artifact_id) is None
    assert store.history(tenant_id="tenant-b", artifact_id=record.artifact_ref.artifact_id) == ()
