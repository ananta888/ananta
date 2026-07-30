from __future__ import annotations

import hashlib

from ananta_contracts.model_intelligence import ArtifactRef
from agent.services.model_intelligence_alerts import (
    ModelIntelligenceAlertEvaluator,
    ModelIntelligenceOperationalSnapshot,
)
from agent.services.model_intelligence_artifact_store import (
    ModelIntelligenceArtifactRef,
)
from agent.services.model_intelligence_retention_adapter import (
    SqliteModelIntelligenceRetentionAdapter,
)
from agent.services.model_intelligence_retention_service import (
    ModelIntelligenceRetentionService,
)
from agent.services.model_intelligence_security_policy import (
    ModelIntelligencePrincipal,
    ModelIntelligenceRetentionRecord,
    ModelIntelligenceRole,
    RetentionCause,
    RetentionClass,
    RetentionState,
)


def _artifact() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="artifact-delete-1",
        job_id="job-delete-1",
        kind="analysis.report",
        sha256="f" * 64,
        size_bytes=64,
        media_type="application/json",
    )


class _DeletePort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ModelIntelligenceArtifactRef]] = []

    def delete(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> bool:
        self.calls.append((tenant_id, reference))
        return True


def test_retention_service_composes_tenant_rbac_persistence_and_delete_port(
    tmp_path,
) -> None:
    artifact = _artifact()
    record = ModelIntelligenceRetentionRecord(
        tenant_id="tenant-a",
        artifact_ref=artifact,
        retention_class=RetentionClass.STANDARD,
        created_at_epoch_seconds=1,
        retain_until_epoch_seconds=2,
    )
    store = SqliteModelIntelligenceRetentionAdapter(tmp_path / "retention.sqlite3")
    store.register(record)
    deletion = _DeletePort()
    service = ModelIntelligenceRetentionService(
        retention_store=store,
        artifact_delete_port=deletion,
    )
    principal = ModelIntelligencePrincipal(
        "operator-1",
        "tenant-a",
        frozenset({ModelIntelligenceRole.OPERATOR}),
    )

    result = service.delete(
        principal=principal,
        artifact_id=artifact.artifact_id,
        idempotency_key="delete-request-1",
        now_epoch_seconds=3,
        cause=RetentionCause.RETENTION_EXPIRED,
    )

    assert result.state is RetentionState.DELETED
    assert len(deletion.calls) == 1
    tenant_id, reference = deletion.calls[0]
    assert tenant_id == "tenant-a"
    assert reference.digest == f"sha256:{artifact.sha256}"
    assert reference.tenant_scope == hashlib.sha256(b"tenant-a").hexdigest()
    assert store.get(tenant_id="tenant-b", artifact_id=artifact.artifact_id) is None
    assert len(result.audit_events()) == 2


def test_alert_evaluator_is_deterministic_and_content_free() -> None:
    snapshot = ModelIntelligenceOperationalSnapshot(
        queue_depth=10,
        queue_limit=10,
        disk_bytes=95,
        disk_limit_bytes=100,
        ram_bytes=50,
        ram_limit_bytes=100,
        artifact_bytes=10,
        artifact_limit_bytes=100,
        completed_jobs=10,
        failed_jobs=2,
        cancelled_jobs=0,
        worker_crashes=3,
    )
    evaluator = ModelIntelligenceAlertEvaluator()

    first = evaluator.evaluate(snapshot)
    second = evaluator.evaluate(snapshot)

    assert first == second
    assert [alert.alert_id for alert in first] == sorted(
        alert.alert_id for alert in first
    )
    assert {
        alert.alert_id for alert in first
    } == {
        "model_intelligence_disk_pressure",
        "model_intelligence_failure_rate",
        "model_intelligence_queue_pressure",
        "model_intelligence_worker_crashes",
    }
    assert all(alert.severity == "critical" for alert in first)
    assert "tenant" not in repr([alert.public() for alert in first]).lower()
