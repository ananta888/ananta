from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.services.source_control_observability import (
    SourceControlHealthMonitor,
)
from agent.services.source_control_rollout_policy import (
    SourceControlRolloutConfiguration,
    SourceControlRolloutPolicy,
    SourceControlRolloutStage,
)
from agent.services.source_control_runtime_observability import (
    SourceControlRuntimeObservability,
)


@dataclass(frozen=True)
class _Principal:
    subject_id: str = "actor-example"
    tenant_id: str = "tenant-example"
    project_id: str = "project-example"


class _Metrics:
    def __init__(self) -> None:
        self.labels: list[dict[str, str]] = []

    def observe_duration(self, metric, seconds, labels) -> None:
        del metric, seconds
        self.labels.append(dict(labels))

    def increment(self, metric, labels) -> None:
        del metric
        self.labels.append(dict(labels))

    def set_gauge(self, metric, value, labels) -> None:
        del metric, value
        self.labels.append(dict(labels))


class _Runtime:
    def create_connection(self, **kwargs):
        del kwargs
        return {"connection_id": "connection-example"}

    def dispatch_operation(self, **kwargs):
        del kwargs
        raise RuntimeError("raw source content must not escape")


def _observed(*, audits):
    return SourceControlRuntimeObservability(
        _Runtime(),
        rollout=SourceControlRolloutPolicy(
            SourceControlRolloutConfiguration(
                stage=SourceControlRolloutStage.GITHUB,
                shadow_compare_enabled=False,
                legacy_aliases_enabled=True,
                production_release_allowed=False,
            )
        ),
        metrics=_Metrics(),
        health=SourceControlHealthMonitor(),
        audit_emitter=audits.append,
        trace_id=lambda: "trace-example",
    )


def test_runtime_emits_content_free_create_audit() -> None:
    audits = []
    result = _observed(audits=audits).create_connection(
        principal=_Principal(),
        payload={"display_name": "must-not-be-audited"},
        idempotency_key="key-example",
    )

    assert result["connection_id"] == "connection-example"
    assert audits[0].to_payload()["operation"] == "create"
    assert "display_name" not in audits[0].to_payload()


def test_runtime_rethrows_failure_without_auditing_exception_text() -> None:
    audits = []
    with pytest.raises(RuntimeError, match="raw source content"):
        _observed(audits=audits).dispatch_operation(
            principal=_Principal(),
            operation="refresh",
            connection_id="connection-example",
            payload={},
            if_match="etag",
            idempotency_key="key-example",
        )

    assert audits[0].reason_code == "internal_error"
    assert "raw source content" not in str(audits[0].to_payload())
