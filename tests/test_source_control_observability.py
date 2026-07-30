from __future__ import annotations

import pytest

from agent.services.source_control_observability import (
    SourceControlAuditEvent,
    SourceControlAuditOperation,
    SourceControlDecision,
    SourceControlObservabilityError,
    bounded_metric_labels,
    emit_source_control_audit,
)


def _event() -> SourceControlAuditEvent:
    return SourceControlAuditEvent(
        operation=SourceControlAuditOperation.index,
        actor_id="actor-example",
        tenant_id="tenant-example",
        project_id="project-example",
        resource_kind="source_revision",
        resource_id="revision-example",
        trace_id="trace-example",
        decision=SourceControlDecision.allow,
        reason_code="policy_match",
        revision_digest="a" * 64,
        manifest_digest="b" * 64,
        policy_digest="c" * 64,
    )


def test_audit_event_is_content_free() -> None:
    event = _event()
    captured: list[dict] = []

    emit_source_control_audit(
        event,
        sink=lambda **kwargs: captured.append(kwargs),
    )

    details = captured[0]["details"]
    assert captured[0]["action"] == "source_control.index"
    assert "content" not in details
    assert "path" not in details
    assert "url" not in details
    assert "credential" not in details


def test_audit_rejects_non_digest_evidence() -> None:
    with pytest.raises(SourceControlObservabilityError):
        SourceControlAuditEvent(
            operation=SourceControlAuditOperation.scan,
            actor_id="actor-example",
            tenant_id="tenant-example",
            project_id="project-example",
            resource_kind="source_revision",
            resource_id="revision-example",
            trace_id="trace-example",
            decision=SourceControlDecision.deny,
            reason_code="secret_detected",
            revision_digest="/tmp/source.txt",
        )


def test_metrics_reject_high_cardinality_labels() -> None:
    assert bounded_metric_labels(
        connector_type="workspace",
        status="completed",
    ) == {
        "connector_type": "workspace",
        "status": "completed",
    }
    with pytest.raises(SourceControlObservabilityError):
        bounded_metric_labels(source_id="source-example")
